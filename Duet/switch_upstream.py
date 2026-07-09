+#!/usr/bin/env python\n
+import argparse\n
+from ggss_swan_common import (\n
+    detect_repo_name, detect_current_upstream,\n
+    paired_upstream, classify_upstream,\n
+    normalize_repo_selector, run_git\n
+)\n
+\n
+MAX_NEG = -2147483648  # uncommitted changes without --force\n
+\n
+\n
+def run_git_checked(args, error_message):\n
+    out, err, code = run_git(args)\n
+    if code != 0:\n
+        detail = err or out or "No additional git output."\n
+        print(f"ERROR: {error_message}")\n
+        print(detail)\n
+        raise SystemExit(-1)\n
+    return out\n
+\n
+def has_uncommitted_changes():\n
+    out, _, _ = run_git(["status", "--porcelain", "--untracked-files=no"])\n
+    return out.strip() != ""\n
+\n
+def list_untracked_files():\n
+    out, _, _ = run_git(["ls-files", "--others", "--exclude-standard"])\n
+    return out.splitlines() if out else []\n
+\n
+def stash_changes(target):\n
+    msg = f"Auto-stash before upstream switch to {target}"\n
+    print(f"Stashing changes: {msg}")\n
+    run_git(["stash", "push", "-m", msg])\n
+\n
+def set_upstream(new_url):\n
+    print(f"Setting new upstream: {new_url}")\n
+    _, err, code = run_git(["remote", "set-url", "origin", new_url])\n
+    if code != 0:\n
+        print(f"ERROR: Failed to set upstream: {err}")\n
+        raise SystemExit(-1)\n
+\n
+\n
+def detect_current_branch():\n
+    branch, err, code = run_git(["rev-parse", "--abbrev-ref", "HEAD"])\n
+    if code != 0:\n
+        print(f"ERROR: Unable to detect current branch: {err}")\n
+        raise SystemExit(-1)\n
+\n
+    branch = branch.strip()\n
+    if branch == "HEAD":\n
+        print("ERROR: Detached HEAD state detected. Checkout a branch before switching upstream.")\n
+        raise SystemExit(-1)\n
+\n
+    return branch\n
+\n
+\n
+def sync_branch_to_new_upstream(branch):\n
+    print(f"Syncing branch '{branch}' to the new upstream...")\n
+    run_git_checked(["fetch", "origin", "--prune", "--force"], "Failed to fetch from new upstream.")\n
+\n
+    _, _, checkout_code = run_git(["checkout", branch])\n
+    if checkout_code != 0:\n
+        run_git_checked(\n
+            ["checkout", "-B", branch, f"origin/{branch}"],\n
+            f"Failed to create/switch branch '{branch}' from origin/{branch}.",\n
+        )\n
+\n
+    run_git_checked(\n
+        ["reset", "--hard", f"origin/{branch}"],\n
+        f"Failed to reset branch '{branch}' to origin/{branch}.",\n
+    )\n
+\n
+def main():\n
+    parser = argparse.ArgumentParser(\n
+        description="Switch GGSS repo to its SWAN equivalent (or vice versa).",\n
+        epilog=(\n
+            "Return codes:\n"\n
+            "  0   = success\n"\n
+            " -1   = URI/URL/path failure\n"\n
+            f"{MAX_NEG} = uncommitted changes without --force\n"\n
+        )\n
+    )\n
+\n
+    parser.add_argument("-f", "--force", action="store_true",\n
+                        help="Force switch by stashing uncommitted changes.")\n
+    parser.add_argument("-r", "--repo", metavar="G|GGSS|S|SWAN",\n
+                        help="Explicitly switch to GGSS or SWAN.")\n
+    parser.add_argument("-i", "--info", action="store_true",\n
+                        help="Show current upstream info and exit.")\n
+\n
+    args = parser.parse_args()\n
+\n
+    repo = detect_repo_name()\n
+    current = detect_current_upstream()\n
+    current_name, _ = classify_upstream(current, repo)\n
+\n
+    if args.info:\n
+        print(f"Repo: {repo}")\n
+        print(f"Current upstream: {current}")\n
+        print(f"Upstream classification: {current_name}")\n
+        raise SystemExit(0)\n
+\n
+    target_requested = normalize_repo_selector(args.repo)\n
+\n
+    if target_requested and target_requested == current_name:\n
+        print(f"Already on {current_name}. No switch needed.")\n
+        raise SystemExit(0)\n
+\n
+    target_name, target_path = paired_upstream(repo, current)\n
+\n
+    print(f"Current repo: {repo}")\n
+    print(f"Current upstream: {current}")\n
+    print(f"Target upstream: {target_path} ({target_name})")\n
+\n
+    if has_uncommitted_changes():\n
+        if not args.force:\n
+            print("Uncommitted changes detected. Refusing to switch.")\n
+            print("Use --force to stash changes automatically.")\n
+            raise SystemExit(MAX_NEG)\n
+        else:\n
+            print("Uncommitted changes detected, but --force was used.")\n
+            stash_changes(target_name)\n
+\n
+    untracked = list_untracked_files()\n
+    if untracked:\n
+        print("Warning: Untracked files detected:")\n
+        for f in untracked:\n
+            print(f"  {f}")\n
+        print("These files persist across upstream switches.")\n
+\n
+    branch = detect_current_branch()\n
+    set_upstream(target_path)\n
+    sync_branch_to_new_upstream(branch)\n
+\n
+    final = detect_current_upstream()\n
+    print(f"Upstream for {repo} repo is now {final} ({target_name}), branch '{branch}' synced.")\n
+\n
+    raise SystemExit(0)\n
+\n
+if __name__ == "__main__":\n
+    main()\n
-- \n
2.39.5\n
\n
