#!/usr/bin/env python
"""Shared helpers for GGSS/SWAN repo pairing and git command execution."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

VALID_REPOS = {"Director", "MnC", "Scheduler", "Tools"}


def run_git(args: list[str], cwd: str | Path | None = None) -> tuple[str, str, int]:
    result = subprocess.run(
        ["git"] + args,
        cwd=str(cwd) if cwd is not None else None,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip(), result.stderr.strip(), result.returncode


def die(msg: str) -> None:
    print(f"ERROR: {msg}")
    raise SystemExit(1)


def extract_repo_name_from_path(path: str) -> str:
    """Extract repo name from the final path component."""
    tail = os.path.basename(path.rstrip("\\/"))
    if tail in VALID_REPOS:
        return tail
    die(
        f"Cannot determine repo name from upstream path '{path}'. "
        f"Expected one of: {sorted(VALID_REPOS)}"
    )


def classify_upstream_flex(path: str) -> tuple[str, str]:
    """
    Classify upstream as GGSS or SWAN based on path content and trailing repo name.
    Returns (classification, repo_name).
    """
    repo = extract_repo_name_from_path(path)
    is_swan = "swan" in path.lower()
    return ("SWAN" if is_swan else "GGSS"), repo


def toggle_upstream_path(path: str, repo: str, current_name: str) -> str:
    """
    Toggle SWAN insertion/removal while preserving the rest of the path:
    - SWAN -> GGSS: remove SWAN segment
    - GGSS -> SWAN: insert SWAN segment before repo segment
    """
    parts = path.replace("/", "\\").split("\\")
    parts = [p for p in parts if p]

    parts_no_swan = [p for p in parts if p.lower() != "swan"]

    try:
        idx = parts_no_swan.index(repo)
    except ValueError:
        die(f"Repo '{repo}' not found in upstream path '{path}'.")

    if current_name == "SWAN":
        new_parts = parts_no_swan
    else:
        new_parts = parts_no_swan[:idx] + ["SWAN"] + parts_no_swan[idx:]

    new_path = "\\".join(new_parts)
    if path.startswith("\\\\"):
        new_path = "\\\\" + new_path

    return new_path


def detect_repo_name() -> str:
    git_root = Path.cwd() / ".git"
    if not git_root.exists():
        print("ERROR: Script must be run from repo root.")
        sys.exit(1)

    cwd_name = Path.cwd().name
    if cwd_name not in VALID_REPOS:
        die(f"Current directory '{cwd_name}' is not one of: {sorted(VALID_REPOS)}")

    return cwd_name


def detect_current_upstream() -> str:
    out, _, code = run_git(["remote", "get-url", "origin"])
    if code != 0:
        die("Not a git repository or no remote named 'origin'.")
    return out


def classify_upstream(path: str, repo: str | None = None) -> tuple[str, str]:
    """Public wrapper. Returns (classification, path)."""
    name, extracted_repo = classify_upstream_flex(path)
    if repo and repo != extracted_repo:
        die(f"Repo mismatch: expected '{repo}', got '{extracted_repo}' from '{path}'.")
    return name, path


def paired_upstream(repo: str, current_url: str) -> tuple[str, str]:
    """Return (other_classification, toggled_url)."""
    current_name, extracted_repo = classify_upstream_flex(current_url)
    if extracted_repo != repo:
        die(
            f"Repo mismatch: expected '{repo}', got '{extracted_repo}' from '{current_url}'."
        )

    new_path = toggle_upstream_path(current_url, repo, current_name)
    new_name = "GGSS" if current_name == "SWAN" else "SWAN"
    return new_name, new_path


def normalize_repo_selector(sel: str | None) -> str | None:
    if sel is None:
        return None

    s = sel.strip().upper()
    if s in ("G", "GGSS"):
        return "GGSS"
    if s in ("S", "SWAN"):
        return "SWAN"

    die(f"Invalid repo selector '{sel}'. Use G|GGSS or S|SWAN.")



