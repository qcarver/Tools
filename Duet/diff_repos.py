#!/usr/bin/env python
"""
Diff GGSS and SWAN repositories by cloning locally and comparing working trees.

Why this exists:
- Git for Windows can be unreliable when diffing UNC-hosted repos directly.
- This script clones each side to a deterministic temp directory, updates the clones,
  then computes per-file changes using local paths.

Behavior:
- Reports numstat-like rows for new, deleted, and modified files.
- Tracks total changed files and lines.
- In verbose mode, prints full unified diffs for modified files that exist on both sides.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from ggss_swan_common import (
    classify_upstream,
    detect_current_upstream,
    detect_repo_name,
    paired_upstream,
    run_git,
)

RED = "31"
GREEN = "32"
YELLOW = "33"
CYAN = "36"

MAX_LINES = 2047  # 11 bits
MAX_FILES = 31    # 5 bits

TEXT_EXTENSIONS = {
    ".c",
    ".cc",
    ".cpp",
    ".h",
    ".hpp",
    ".cs",
    ".java",
    ".py",
    ".ps1",
    ".sh",
    ".bat",
    ".cmd",
    ".cmake",
    ".txt",
    ".md",
    ".json",
    ".xml",
    ".yaml",
    ".yml",
    ".ini",
    ".cfg",
    ".toml",
    ".csv",
    ".tsv",
    ".sql",
}


@dataclass(frozen=True)
class FileChange:
    rel_path: Path
    added: int | None
    deleted: int | None
    status: str  # THEIRS_ONLY, OURS_ONLY, DIFFER, BINARY


def color(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Deterministic diff between GGSS and SWAN repos using local clones.",
        epilog=(
            "Return code bit layout (16-bit signed):\n"
            "  bit 15: saturation flag (1 = saturated)\n"
            "  bits 14-11: file count (0-31)\n"
            "  bits 10-0:  line count (0-2047)\n"
            "\n"
            "If saturated, return code is negative.\n"
            "If not saturated, return code is positive.\n"
            "Quiet mode (-q) suppresses output."
        ),
    )

    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Show full per-file diff for changed files present in both trees.",
    )
    parser.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="Suppress all output; return code only.",
    )
    parser.add_argument(
        "-i",
        "--info",
        action="store_true",
        help="Show repo/upstream info and exit.",
    )
    parser.add_argument(
        "-o",
        "--ours",
        metavar="BRANCH",
        help="Our branch (default: current branch).",
    )
    parser.add_argument(
        "-t",
        "--theirs",
        metavar="BRANCH",
        default="master",
        help="Their branch (default: master).",
    )
    parser.add_argument(
        "-X",
        "--expunge-cache",
        action="store_true",
        help="Delete cached clones for this upstream/branch pair before diffing.",
    )
    parser.add_argument(
        "-b",
        "--binary",
        action="store_true",
        help="Include binary-file differences in the summary output.",
    )

    return parser.parse_args()


def clone_key(remote_path: str, branch: str) -> str:
    h = hashlib.sha1()
    h.update(f"{remote_path}|{branch}".encode("utf-8"))
    return h.hexdigest()[:10]


def clone_dir_for(remote_path: str, repo_name: str, side: str, branch: str) -> Path:
    base = Path(tempfile.gettempdir()) / "_ggss_swan_diff"
    base.mkdir(parents=True, exist_ok=True)
    return base / f"{repo_name}_{side}_{clone_key(remote_path, branch)}"


def _run_checked(cmd: list[str], quiet: bool = False) -> tuple[str, str, int]:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0 and not quiet:
        print(color(f"Command failed: {' '.join(cmd)}", RED))
        if proc.stderr.strip():
            print(proc.stderr.strip())
    return proc.stdout, proc.stderr, proc.returncode


def ensure_clone(remote_path: str, repo_name: str, side: str, branch: str, quiet: bool) -> Path:
    clone_dir = clone_dir_for(remote_path, repo_name, side, branch)

    if clone_dir.exists() and (clone_dir / ".git").exists():
        if not quiet:
            print(color(f"==> Reusing clone ({side}): {clone_dir}", CYAN))

        _run_checked(["git", "-C", str(clone_dir), "fetch", "--all", "--prune"], quiet=quiet)

        # Try local branch first, then origin/<branch> if missing.
        _, _, checkout_code = _run_checked(
            ["git", "-C", str(clone_dir), "checkout", branch],
            quiet=True,
        )
        if checkout_code != 0:
            _, _, switch_code = _run_checked(
                ["git", "-C", str(clone_dir), "checkout", "-B", branch, f"origin/{branch}"],
                quiet=True,
            )
            if switch_code != 0:
                if not quiet:
                    print(color(f"ERROR: Could not checkout branch '{branch}' in {clone_dir}", RED))
                raise SystemExit(2)

        _run_checked(
            ["git", "-C", str(clone_dir), "reset", "--hard", f"origin/{branch}"],
            quiet=True,
        )
        return clone_dir

    if clone_dir.exists():
        shutil.rmtree(clone_dir, ignore_errors=True)

    if not quiet:
        print(color(f"==> Cloning {side} from {remote_path} -> {clone_dir}", CYAN))

    _, stderr, code = _run_checked(
        ["git", "clone", "--branch", branch, "--single-branch", remote_path, str(clone_dir)],
        quiet=quiet,
    )
    if code != 0:
        if not quiet:
            print(color("ERROR: Failed to clone repository.", RED))
            if stderr.strip():
                print(stderr.strip())
        raise SystemExit(2)

    return clone_dir


def is_binary_file(path: Path) -> bool:
    if path.suffix.lower() in TEXT_EXTENSIONS:
        return False

    try:
        with path.open("rb") as f:
            sample = f.read(8192)
    except OSError:
        return True

    if b"\x00" in sample:
        return True

    try:
        sample.decode("utf-8")
        return False
    except UnicodeDecodeError:
        return True


def count_text_lines(path: Path) -> int:
    try:
        with path.open("rb") as f:
            data = f.read()
    except OSError:
        return 0

    if not data:
        return 0

    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)


def iter_repo_files(root: Path) -> dict[Path, Path]:
    out: dict[Path, Path] = {}
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if ".git" in p.parts:
            continue
        rel = p.relative_to(root)
        out[rel] = p
    return out


def _to_relative_path(raw_path: str, ours_root: Path, theirs_root: Path) -> Path:
    candidate = raw_path.strip().strip('"')
    candidate = candidate.replace("\\", "/")
    ours_str = str(ours_root).replace("\\", "/")
    theirs_str = str(theirs_root).replace("\\", "/")

    if candidate.startswith(ours_str + "/"):
        return Path(candidate[len(ours_str) + 1 :])
    if candidate.startswith(theirs_str + "/"):
        return Path(candidate[len(theirs_str) + 1 :])
    if candidate.startswith("a/") or candidate.startswith("b/"):
        return Path(candidate[2:])
    return Path(candidate)


def compute_changes(ours_root: Path, theirs_root: Path, include_binary: bool) -> list[FileChange]:
    ours_files = iter_repo_files(ours_root)
    theirs_files = iter_repo_files(theirs_root)

    out, err, code = run_git(
        [
            "diff",
            "--no-index",
            "--numstat",
            "--no-renames",
            str(ours_root),
            str(theirs_root),
        ]
    )

    if code == 0:
        return []
    if code > 1:
        detail = err or out or "No additional git output."
        print(color("ERROR: Failed to compute numstat summary.", RED))
        print(detail)
        raise SystemExit(2)

    changes: list[FileChange] = []

    for line in out.splitlines():
        parts = line.split("\t", 2)
        if len(parts) != 3:
            continue

        add_raw, del_raw, path_raw = parts
        rel = _to_relative_path(path_raw, ours_root, theirs_root)
        ours_exists = rel in ours_files
        theirs_exists = rel in theirs_files

        if add_raw.strip() == "-" or del_raw.strip() == "-":
            if include_binary:
                changes.append(FileChange(rel_path=rel, added=None, deleted=None, status="BINARY"))
            continue

        try:
            added = int(add_raw.strip())
            deleted = int(del_raw.strip())
        except ValueError:
            if include_binary:
                changes.append(FileChange(rel_path=rel, added=None, deleted=None, status="BINARY"))
            continue

        if ours_exists and theirs_exists:
            status = "DIFFER"
        elif ours_exists:
            status = "OURS_ONLY"
        elif theirs_exists:
            status = "THEIRS_ONLY"
        else:
            status = "DIFFER"

        changes.append(FileChange(rel_path=rel, added=added, deleted=deleted, status=status))

    return changes


def print_numstat_summary(changes: Iterable[FileChange], quiet: bool) -> tuple[int, int]:
    files_changed = 0
    lines_changed = 0

    for change in changes:
        files_changed += 1

        if change.added is None or change.deleted is None:
            added_txt = "-"
            deleted_txt = "-"
        else:
            added_txt = str(change.added)
            deleted_txt = str(change.deleted)
            lines_changed += change.added + change.deleted

        if not quiet:
            status = color(change.status, YELLOW)
            print(f"{added_txt}\t{deleted_txt}\t{change.rel_path.as_posix()}\t[{status}]")

    return files_changed, lines_changed


def print_verbose_diffs(
    changes: Iterable[FileChange],
    ours_root: Path,
    theirs_root: Path,
    ours_label: str,
    theirs_label: str,
    quiet: bool,
) -> None:
    if quiet:
        return

    for change in changes:
        if change.status != "DIFFER":
            continue

        left = ours_root / change.rel_path
        right = theirs_root / change.rel_path

        print(color("\n---", CYAN))
        print(
            color(
                f"{ours_label}:{change.rel_path.as_posix()} (ours) <-> "
                f"{theirs_label}:{change.rel_path.as_posix()} (theirs)",
                CYAN,
            )
        )

        proc = subprocess.run(
            ["git", "diff", "--no-index", "--color=always", str(left), str(right)],
            capture_output=True,
            text=True,
        )
        if proc.stdout:
            print(proc.stdout.rstrip())
        elif proc.stderr:
            print(proc.stderr.rstrip())


def pack_return_code(files_changed: int, lines_changed: int) -> int:
    sat = 0

    if lines_changed > MAX_LINES:
        lines_changed = MAX_LINES
        sat = 1
    if files_changed > MAX_FILES:
        files_changed = MAX_FILES
        sat = 1

    packed = (sat << 15) | (files_changed << 11) | lines_changed
    return -packed if sat else packed


def resolve_repo_root() -> Path:
    out, _, code = run_git(["rev-parse", "--show-toplevel"])
    if code != 0:
        return Path.cwd()
    return Path(out.strip())


def main() -> None:
    args = parse_args()

    repo_root = resolve_repo_root()
    os.chdir(repo_root)

    repo = detect_repo_name()
    current_upstream = detect_current_upstream()
    ours_classification, _ = classify_upstream(current_upstream, repo)
    theirs_classification, theirs_path = paired_upstream(repo, current_upstream)

    if args.ours:
        ours_branch = args.ours
    else:
        out, _, code = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
        if code != 0:
            print(color("ERROR: Unable to determine current branch.", RED))
            raise SystemExit(2)
        ours_branch = out.strip()

    theirs_branch = args.theirs

    if args.info:
        print(f"Repo: {repo}")
        print(f"Repo root: {repo_root}")
        print(f"Current upstream: {current_upstream}")
        print(f"Ours classification: {ours_classification}")
        print(f"Ours branch: {ours_branch}")
        print(f"Theirs name: {theirs_classification}")
        print(f"Theirs upstream path: {theirs_path}")
        print(f"Theirs branch: {theirs_branch}")
        raise SystemExit(0)

    if not args.quiet:
        print(
            color(
                f"# Comparing {ours_classification}:{ours_branch} (ours) vs "
                f"{theirs_classification}:{theirs_branch} (theirs)",
                CYAN,
            )
        )

    if args.expunge_cache:
        ours_cache = clone_dir_for(current_upstream, repo, "ours", ours_branch)
        theirs_cache = clone_dir_for(theirs_path, repo, "theirs", theirs_branch)
        if ours_cache.exists():
            if not args.quiet:
                print(color(f"==> Expunging cache: {ours_cache}", YELLOW))
            shutil.rmtree(ours_cache, ignore_errors=True)
        if theirs_cache.exists():
            if not args.quiet:
                print(color(f"==> Expunging cache: {theirs_cache}", YELLOW))
            shutil.rmtree(theirs_cache, ignore_errors=True)

    ours_clone = ensure_clone(current_upstream, repo, "ours", ours_branch, args.quiet)
    theirs_clone = ensure_clone(theirs_path, repo, "theirs", theirs_branch, args.quiet)

    if not args.quiet:
        print(color("==> Computing numstat summary...", CYAN))

    changes = compute_changes(ours_clone, theirs_clone, include_binary=args.binary)

    if not changes:
        if not args.quiet:
            print("No differences found.")
        raise SystemExit(0)

    files_changed, lines_changed = print_numstat_summary(changes, args.quiet)

    if args.verbose:
        print_verbose_diffs(
            changes=changes,
            ours_root=ours_clone,
            theirs_root=theirs_clone,
            ours_label=f"{ours_classification}:{repo}:{ours_branch}",
            theirs_label=f"{theirs_classification}:{repo}:{theirs_branch}",
            quiet=args.quiet,
        )

    if not args.quiet:
        print(color(f"\n# files changed: {files_changed}, lines changed: {lines_changed}", GREEN))

    raise SystemExit(pack_return_code(files_changed, lines_changed))


if __name__ == "__main__":
    main()

