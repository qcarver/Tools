+#!/usr/bin/env python\n
+"""\n
+Diff GGSS and SWAN repositories by cloning locally and comparing working trees.\n
+\n
+Why this exists:\n
+- Git for Windows can be unreliable when diffing UNC-hosted repos directly.\n
+- This script clones each side to a deterministic temp directory, updates the clones,\n
+  then computes per-file changes using local paths.\n
+\n
+Behavior:\n
+- Reports numstat-like rows for new, deleted, and modified files.\n
+- Tracks total changed files and lines.\n
+- In verbose mode, prints full unified diffs for modified files that exist on both sides.\n
+"""\n
+\n
+from __future__ import annotations\n
+\n
+import argparse\n
+import hashlib\n
+import os\n
+import shutil\n
+import subprocess\n
+import sys\n
+import tempfile\n
+from dataclasses import dataclass\n
+from pathlib import Path\n
+from typing import Iterable\n
+\n
+from ggss_swan_common import (\n
+    classify_upstream,\n
+    detect_current_upstream,\n
+    detect_repo_name,\n
+    paired_upstream,\n
+    run_git,\n
+)\n
+\n
+RED = "31"\n
+GREEN = "32"\n
+YELLOW = "33"\n
+CYAN = "36"\n
+\n
+MAX_LINES = 2047  # 11 bits\n
+MAX_FILES = 31    # 5 bits\n
+\n
+TEXT_EXTENSIONS = {\n
+    ".c",\n
+    ".cc",\n
+    ".cpp",\n
+    ".h",\n
+    ".hpp",\n
+    ".cs",\n
+    ".java",\n
+    ".py",\n
+    ".ps1",\n
+    ".sh",\n
+    ".bat",\n
+    ".cmd",\n
+    ".cmake",\n
+    ".txt",\n
+    ".md",\n
+    ".json",\n
+    ".xml",\n
+    ".yaml",\n
+    ".yml",\n
+    ".ini",\n
+    ".cfg",\n
+    ".toml",\n
+    ".csv",\n
+    ".tsv",\n
+    ".sql",\n
+}\n
+\n
+\n
+@dataclass(frozen=True)\n
+class FileChange:\n
+    rel_path: Path\n
+    added: int | None\n
+    deleted: int | None\n
+    status: str  # THEIRS_ONLY, OURS_ONLY, DIFFER, BINARY\n
+\n
+\n
+def color(text: str, code: str) -> str:\n
+    return f"\033[{code}m{text}\033[0m"\n
+\n
+\n
+def parse_args() -> argparse.Namespace:\n
+    parser = argparse.ArgumentParser(\n
+        description="Deterministic diff between GGSS and SWAN repos using local clones.",\n
+        epilog=(\n
+            "Return code bit layout (16-bit signed):\n"\n
+            "  bit 15: saturation flag (1 = saturated)\n"\n
+            "  bits 14-11: file count (0-31)\n"\n
+            "  bits 10-0:  line count (0-2047)\n"\n
+            "\n"\n
+            "If saturated, return code is negative.\n"\n
+            "If not saturated, return code is positive.\n"\n
+            "Quiet mode (-q) suppresses output."\n
+        ),\n
+    )\n
+\n
+    parser.add_argument(\n
+        "-v",\n
+        "--verbose",\n
+        action="store_true",\n
+        help="Show full per-file diff for changed files present in both trees.",\n
+    )\n
+    parser.add_argument(\n
+        "-q",\n
+        "--quiet",\n
+        action="store_true",\n
+        help="Suppress all output; return code only.",\n
+    )\n
+    parser.add_argument(\n
+        "-i",\n
+        "--info",\n
+        action="store_true",\n
+        help="Show repo/upstream info and exit.",\n
+    )\n
+    parser.add_argument(\n
+        "-o",\n
+        "--ours",\n
+        metavar="BRANCH",\n
+        help="Our branch (default: current branch).",\n
+    )\n
+    parser.add_argument(\n
+        "-t",\n
+        "--theirs",\n
+        metavar="BRANCH",\n
+        default="master",\n
+        help="Their branch (default: master).",\n
+    )\n
+    parser.add_argument(\n
+        "-X",\n
+        "--expunge-cache",\n
+        action="store_true",\n
+        help="Delete cached clones for this upstream/branch pair before diffing.",\n
+    )\n
+    parser.add_argument(\n
+        "-b",\n
+        "--binary",\n
+        action="store_true",\n
+        help="Include binary-file differences in the summary output.",\n
+    )\n
+\n
+    return parser.parse_args()\n
+\n
+\n
+def clone_key(remote_path: str, branch: str) -> str:\n
+    h = hashlib.sha1()\n
+    h.update(f"{remote_path}|{branch}".encode("utf-8"))\n
+    return h.hexdigest()[:10]\n
+\n
+\n
+def clone_dir_for(remote_path: str, repo_name: str, side: str, branch: str) -> Path:\n
+    base = Path(tempfile.gettempdir()) / "_ggss_swan_diff"\n
+    base.mkdir(parents=True, exist_ok=True)\n
+    return base / f"{repo_name}_{side}_{clone_key(remote_path, branch)}"\n
+\n
+\n
+def _run_checked(cmd: list[str], quiet: bool = False) -> tuple[str, str, int]:\n
+    proc = subprocess.run(cmd, capture_output=True, text=True)\n
+    if proc.returncode != 0 and not quiet:\n
+        print(color(f"Command failed: {' '.join(cmd)}", RED))\n
+        if proc.stderr.strip():\n
+            print(proc.stderr.strip())\n
+    return proc.stdout, proc.stderr, proc.returncode\n
+\n
+\n
+def ensure_clone(remote_path: str, repo_name: str, side: str, branch: str, quiet: bool) -> Path:\n
+    clone_dir = clone_dir_for(remote_path, repo_name, side, branch)\n
+\n
+    if clone_dir.exists() and (clone_dir / ".git").exists():\n
+        if not quiet:\n
+            print(color(f"==> Reusing clone ({side}): {clone_dir}", CYAN))\n
+\n
+        _run_checked(["git", "-C", str(clone_dir), "fetch", "--all", "--prune"], quiet=quiet)\n
+\n
+        # Try local branch first, then origin/<branch> if missing.\n
+        _, _, checkout_code = _run_checked(\n
+            ["git", "-C", str(clone_dir), "checkout", branch],\n
+            quiet=True,\n
+        )\n
+        if checkout_code != 0:\n
+            _, _, switch_code = _run_checked(\n
+                ["git", "-C", str(clone_dir), "checkout", "-B", branch, f"origin/{branch}"],\n
+                quiet=True,\n
+            )\n
+            if switch_code != 0:\n
+                if not quiet:\n
+                    print(color(f"ERROR: Could not checkout branch '{branch}' in {clone_dir}", RED))\n
+                raise SystemExit(2)\n
+\n
+        _run_checked(\n
+            ["git", "-C", str(clone_dir), "reset", "--hard", f"origin/{branch}"],\n
+            quiet=True,\n
+        )\n
+        return clone_dir\n
+\n
+    if clone_dir.exists():\n
+        shutil.rmtree(clone_dir, ignore_errors=True)\n
+\n
+    if not quiet:\n
+        print(color(f"==> Cloning {side} from {remote_path} -> {clone_dir}", CYAN))\n
+\n
+    _, stderr, code = _run_checked(\n
+        ["git", "clone", "--branch", branch, "--single-branch", remote_path, str(clone_dir)],\n
+        quiet=quiet,\n
+    )\n
+    if code != 0:\n
+        if not quiet:\n
+            print(color("ERROR: Failed to clone repository.", RED))\n
+            if stderr.strip():\n
+                print(stderr.strip())\n
+        raise SystemExit(2)\n
+\n
+    return clone_dir\n
+\n
+\n
+def is_binary_file(path: Path) -> bool:\n
+    if path.suffix.lower() in TEXT_EXTENSIONS:\n
+        return False\n
+\n
+    try:\n
+        with path.open("rb") as f:\n
+            sample = f.read(8192)\n
+    except OSError:\n
+        return True\n
+\n
+    if b"\x00" in sample:\n
+        return True\n
+\n
+    try:\n
+        sample.decode("utf-8")\n
+        return False\n
+    except UnicodeDecodeError:\n
+        return True\n
+\n
+\n
+def count_text_lines(path: Path) -> int:\n
+    try:\n
+        with path.open("rb") as f:\n
+            data = f.read()\n
+    except OSError:\n
+        return 0\n
+\n
+    if not data:\n
+        return 0\n
+\n
+    return data.count(b"\n") + (0 if data.endswith(b"\n") else 1)\n
+\n
+\n
+def iter_repo_files(root: Path) -> dict[Path, Path]:\n
+    out: dict[Path, Path] = {}\n
+    for p in root.rglob("*"):\n
+        if not p.is_file():\n
+            continue\n
+        if ".git" in p.parts:\n
+            continue\n
+        rel = p.relative_to(root)\n
+        out[rel] = p\n
+    return out\n
+\n
+\n
+def _to_relative_path(raw_path: str, ours_root: Path, theirs_root: Path) -> Path:\n
+    candidate = raw_path.strip().strip('"')\n
+    candidate = candidate.replace("\\", "/")\n
+    ours_str = str(ours_root).replace("\\", "/")\n
+    theirs_str = str(theirs_root).replace("\\", "/")\n
+\n
+    if candidate.startswith(ours_str + "/"):\n
+        return Path(candidate[len(ours_str) + 1 :])\n
+    if candidate.startswith(theirs_str + "/"):\n
+        return Path(candidate[len(theirs_str) + 1 :])\n
+    if candidate.startswith("a/") or candidate.startswith("b/"):\n
+        return Path(candidate[2:])\n
+    return Path(candidate)\n
+\n
+\n
+def compute_changes(ours_root: Path, theirs_root: Path, include_binary: bool) -> list[FileChange]:\n
+    ours_files = iter_repo_files(ours_root)\n
+    theirs_files = iter_repo_files(theirs_root)\n
+\n
+    out, err, code = run_git(\n
+        [\n
+            "diff",\n
+            "--no-index",\n
+            "--numstat",\n
+            "--no-renames",\n
+            str(ours_root),\n
+            str(theirs_root),\n
+        ]\n
+    )\n
+\n
+    if code == 0:\n
+        return []\n
+    if code > 1:\n
+        detail = err or out or "No additional git output."\n
+        print(color("ERROR: Failed to compute numstat summary.", RED))\n
+        print(detail)\n
+        raise SystemExit(2)\n
+\n
+    changes: list[FileChange] = []\n
+\n
+    for line in out.splitlines():\n
+        parts = line.split("\t", 2)\n
+        if len(parts) != 3:\n
+            continue\n
+\n
+        add_raw, del_raw, path_raw = parts\n
+        rel = _to_relative_path(path_raw, ours_root, theirs_root)\n
+        ours_exists = rel in ours_files\n
+        theirs_exists = rel in theirs_files\n
+\n
+        if add_raw.strip() == "-" or del_raw.strip() == "-":\n
+            if include_binary:\n
+                changes.append(FileChange(rel_path=rel, added=None, deleted=None, status="BINARY"))\n
+            continue\n
+\n
+        try:\n
+            added = int(add_raw.strip())\n
+            deleted = int(del_raw.strip())\n
+        except ValueError:\n
+            if include_binary:\n
+                changes.append(FileChange(rel_path=rel, added=None, deleted=None, status="BINARY"))\n
+            continue\n
+\n
+        if ours_exists and theirs_exists:\n
+            status = "DIFFER"\n
+        elif ours_exists:\n
+            status = "OURS_ONLY"\n
+        elif theirs_exists:\n
+            status = "THEIRS_ONLY"\n
+        else:\n
+            status = "DIFFER"\n
+\n
+        changes.append(FileChange(rel_path=rel, added=added, deleted=deleted, status=status))\n
+\n
+    return changes\n
+\n
+\n
+def print_numstat_summary(changes: Iterable[FileChange], quiet: bool) -> tuple[int, int]:\n
+    files_changed = 0\n
+    lines_changed = 0\n
+\n
+    for change in changes:\n
+        files_changed += 1\n
+\n
+        if change.added is None or change.deleted is None:\n
+            added_txt = "-"\n
+            deleted_txt = "-"\n
+        else:\n
+            added_txt = str(change.added)\n
+            deleted_txt = str(change.deleted)\n
+            lines_changed += change.added + change.deleted\n
+\n
+        if not quiet:\n
+            status = color(change.status, YELLOW)\n
+            print(f"{added_txt}\t{deleted_txt}\t{change.rel_path.as_posix()}\t[{status}]")\n
+\n
+    return files_changed, lines_changed\n
+\n
+\n
+def print_verbose_diffs(\n
+    changes: Iterable[FileChange],\n
+    ours_root: Path,\n
+    theirs_root: Path,\n
+    ours_label: str,\n
+    theirs_label: str,\n
+    quiet: bool,\n
+) -> None:\n
+    if quiet:\n
+        return\n
+\n
+    for change in changes:\n
+        if change.status != "DIFFER":\n
+            continue\n
+\n
+        left = ours_root / change.rel_path\n
+        right = theirs_root / change.rel_path\n
+\n
+        print(color("\n---", CYAN))\n
+        print(\n
+            color(\n
+                f"{ours_label}:{change.rel_path.as_posix()} (ours) <-> "\n
+                f"{theirs_label}:{change.rel_path.as_posix()} (theirs)",\n
+                CYAN,\n
+            )\n
+        )\n
+\n
+        proc = subprocess.run(\n
+            ["git", "diff", "--no-index", "--color=always", str(left), str(right)],\n
+            capture_output=True,\n
+            text=True,\n
+        )\n
+        if proc.stdout:\n
+            print(proc.stdout.rstrip())\n
+        elif proc.stderr:\n
+            print(proc.stderr.rstrip())\n
+\n
+\n
+def pack_return_code(files_changed: int, lines_changed: int) -> int:\n
+    sat = 0\n
+\n
+    if lines_changed > MAX_LINES:\n
+        lines_changed = MAX_LINES\n
+        sat = 1\n
+    if files_changed > MAX_FILES:\n
+        files_changed = MAX_FILES\n
+        sat = 1\n
+\n
+    packed = (sat << 15) | (files_changed << 11) | lines_changed\n
+    return -packed if sat else packed\n
+\n
+\n
+def resolve_repo_root() -> Path:\n
+    out, _, code = run_git(["rev-parse", "--show-toplevel"])\n
+    if code != 0:\n
+        return Path.cwd()\n
+    return Path(out.strip())\n
+\n
+\n
+def main() -> None:\n
+    args = parse_args()\n
+\n
+    repo_root = resolve_repo_root()\n
+    os.chdir(repo_root)\n
+\n
+    repo = detect_repo_name()\n
+    current_upstream = detect_current_upstream()\n
+    ours_classification, _ = classify_upstream(current_upstream, repo)\n
+    theirs_classification, theirs_path = paired_upstream(repo, current_upstream)\n
+\n
+    if args.ours:\n
+        ours_branch = args.ours\n
+    else:\n
+        out, _, code = run_git(["rev-parse", "--abbrev-ref", "HEAD"])\n
+        if code != 0:\n
+            print(color("ERROR: Unable to determine current branch.", RED))\n
+            raise SystemExit(2)\n
+        ours_branch = out.strip()\n
+\n
+    theirs_branch = args.theirs\n
+\n
+    if args.info:\n
+        print(f"Repo: {repo}")\n
+        print(f"Repo root: {repo_root}")\n
+        print(f"Current upstream: {current_upstream}")\n
+        print(f"Ours classification: {ours_classification}")\n
+        print(f"Ours branch: {ours_branch}")\n
+        print(f"Theirs name: {theirs_classification}")\n
+        print(f"Theirs upstream path: {theirs_path}")\n
+        print(f"Theirs branch: {theirs_branch}")\n
+        raise SystemExit(0)\n
+\n
+    if not args.quiet:\n
+        print(\n
+            color(\n
+                f"# Comparing {ours_classification}:{ours_branch} (ours) vs "\n
+                f"{theirs_classification}:{theirs_branch} (theirs)",\n
+                CYAN,\n
+            )\n
+        )\n
+\n
+    if args.expunge_cache:\n
+        ours_cache = clone_dir_for(current_upstream, repo, "ours", ours_branch)\n
+        theirs_cache = clone_dir_for(theirs_path, repo, "theirs", theirs_branch)\n
+        if ours_cache.exists():\n
+            if not args.quiet:\n
+                print(color(f"==> Expunging cache: {ours_cache}", YELLOW))\n
+            shutil.rmtree(ours_cache, ignore_errors=True)\n
+        if theirs_cache.exists():\n
+            if not args.quiet:\n
+                print(color(f"==> Expunging cache: {theirs_cache}", YELLOW))\n
+            shutil.rmtree(theirs_cache, ignore_errors=True)\n
+\n
+    ours_clone = ensure_clone(current_upstream, repo, "ours", ours_branch, args.quiet)\n
+    theirs_clone = ensure_clone(theirs_path, repo, "theirs", theirs_branch, args.quiet)\n
+\n
+    if not args.quiet:\n
+        print(color("==> Computing numstat summary...", CYAN))\n
+\n
+    changes = compute_changes(ours_clone, theirs_clone, include_binary=args.binary)\n
+\n
+    if not changes:\n
+        if not args.quiet:\n
+            print("No differences found.")\n
+        raise SystemExit(0)\n
+\n
+    files_changed, lines_changed = print_numstat_summary(changes, args.quiet)\n
+\n
+    if args.verbose:\n
+        print_verbose_diffs(\n
+            changes=changes,\n
+            ours_root=ours_clone,\n
+            theirs_root=theirs_clone,\n
+            ours_label=f"{ours_classification}:{repo}:{ours_branch}",\n
+            theirs_label=f"{theirs_classification}:{repo}:{theirs_branch}",\n
+            quiet=args.quiet,\n
+        )\n
+\n
+    if not args.quiet:\n
+        print(color(f"\n# files changed: {files_changed}, lines changed: {lines_changed}", GREEN))\n
+\n
+    raise SystemExit(pack_return_code(files_changed, lines_changed))\n
+\n
+\n
+if __name__ == "__main__":\n
+    main()\n

