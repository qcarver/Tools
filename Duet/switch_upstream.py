#!/usr/bin/env python
import argparse
from ggss_swan_common import (
    detect_repo_name, detect_current_upstream,
    paired_upstream, classify_upstream,
    normalize_repo_selector, run_git
)

MAX_NEG = -2147483648  # uncommitted changes without --force


def run_git_checked(args, error_message):
    out, err, code = run_git(args)
    if code != 0:
        detail = err or out or "No additional git output."
        print(f"ERROR: {error_message}")
        print(detail)
        raise SystemExit(-1)
    return out

def has_uncommitted_changes():
    out, _, _ = run_git(["status", "--porcelain", "--untracked-files=no"])
    return out.strip() != ""

def list_untracked_files():
    out, _, _ = run_git(["ls-files", "--others", "--exclude-standard"])
    return out.splitlines() if out else []

def stash_changes(target):
    msg = f"Auto-stash before upstream switch to {target}"
    print(f"Stashing changes: {msg}")
    run_git(["stash", "push", "-m", msg])

def set_upstream(new_url):
    print(f"Setting new upstream: {new_url}")
    _, err, code = run_git(["remote", "set-url", "origin", new_url])
    if code != 0:
        print(f"ERROR: Failed to set upstream: {err}")
        raise SystemExit(-1)


def detect_current_branch():
    branch, err, code = run_git(["rev-parse", "--abbrev-ref", "HEAD"])
    if code != 0:
        print(f"ERROR: Unable to detect current branch: {err}")
        raise SystemExit(-1)

    branch = branch.strip()
    if branch == "HEAD":
        print("ERROR: Detached HEAD state detected. Checkout a branch before switching upstream.")
        raise SystemExit(-1)

    return branch


def sync_branch_to_new_upstream(branch):
    print(f"Syncing branch '{branch}' to the new upstream...")
    run_git_checked(["fetch", "origin", "--prune", "--force"], "Failed to fetch from new upstream.")

    _, _, checkout_code = run_git(["checkout", branch])
    if checkout_code != 0:
        run_git_checked(
            ["checkout", "-B", branch, f"origin/{branch}"],
            f"Failed to create/switch branch '{branch}' from origin/{branch}.",
        )

    run_git_checked(
        ["reset", "--hard", f"origin/{branch}"],
        f"Failed to reset branch '{branch}' to origin/{branch}.",
    )

def main():
    parser = argparse.ArgumentParser(
        description="Switch GGSS repo to its SWAN equivalent (or vice versa).",
        epilog=(
            "Return codes:\n"
            "  0   = success\n"
            " -1   = URI/URL/path failure\n"
            f"{MAX_NEG} = uncommitted changes without --force\n"
        )
    )

    parser.add_argument("-f", "--force", action="store_true",
                        help="Force switch by stashing uncommitted changes.")
    parser.add_argument("-r", "--repo", metavar="G|GGSS|S|SWAN",
                        help="Explicitly switch to GGSS or SWAN.")
    parser.add_argument("-i", "--info", action="store_true",
                        help="Show current upstream info and exit.")

    args = parser.parse_args()

    repo = detect_repo_name()
    current = detect_current_upstream()
    current_name, _ = classify_upstream(current, repo)

    if args.info:
        print(f"Repo: {repo}")
        print(f"Current upstream: {current}")
        print(f"Upstream classification: {current_name}")
        raise SystemExit(0)

    target_requested = normalize_repo_selector(args.repo)

    if target_requested and target_requested == current_name:
        print(f"Already on {current_name}. No switch needed.")
        raise SystemExit(0)

    target_name, target_path = paired_upstream(repo, current)

    print(f"Current repo: {repo}")
    print(f"Current upstream: {current}")
    print(f"Target upstream: {target_path} ({target_name})")

    if has_uncommitted_changes():
        if not args.force:
            print("Uncommitted changes detected. Refusing to switch.")
            print("Use --force to stash changes automatically.")
            raise SystemExit(MAX_NEG)
        else:
            print("Uncommitted changes detected, but --force was used.")
            stash_changes(target_name)

    untracked = list_untracked_files()
    if untracked:
        print("Warning: Untracked files detected:")
        for f in untracked:
            print(f"  {f}")
        print("These files persist across upstream switches.")

    branch = detect_current_branch()
    set_upstream(target_path)
    sync_branch_to_new_upstream(branch)

    final = detect_current_upstream()
    print(f"Upstream for {repo} repo is now {final} ({target_name}), branch '{branch}' synced.")

    raise SystemExit(0)

if __name__ == "__main__":
    main()

