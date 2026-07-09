

diff --git a/ggss_swan_common.py b/ggss_swan_common.py\n
new file mode 100644\n
index 0000000..25f9691\n
--- /dev/null\n
+++ b/ggss_swan_common.py\n
@@ -0,0 +1,129 @@\n
+#!/usr/bin/env python3\n
+"""Shared helpers for GGSS/SWAN repo pairing and git command execution."""\n
+\n
+from __future__ import annotations\n
+\n
+import os\n
+import subprocess\n
+import sys\n
+from pathlib import Path\n
+\n
+VALID_REPOS = {"Director", "MnC", "Scheduler", "Tools"}\n
+\n
+\n
+def run_git(args: list[str], cwd: str | Path | None = None) -> tuple[str, str, int]:\n
+    result = subprocess.run(\n
+        ["git"] + args,\n
+        cwd=str(cwd) if cwd is not None else None,\n
+        capture_output=True,\n
+        text=True,\n
+    )\n
+    return result.stdout.strip(), result.stderr.strip(), result.returncode\n
+\n
+\n
+def die(msg: str) -> None:\n
+    print(f"ERROR: {msg}")\n
+    raise SystemExit(1)\n
+\n
+\n
+def extract_repo_name_from_path(path: str) -> str:\n
+    """Extract repo name from the final path component."""\n
+    tail = os.path.basename(path.rstrip("\\/"))\n
+    if tail in VALID_REPOS:\n
+        return tail\n
+    die(\n
+        f"Cannot determine repo name from upstream path '{path}'. "\n
+        f"Expected one of: {sorted(VALID_REPOS)}"\n
+    )\n
+\n
+\n
+def classify_upstream_flex(path: str) -> tuple[str, str]:\n
+    """\n
+    Classify upstream as GGSS or SWAN based on path content and trailing repo name.\n
+    Returns (classification, repo_name).\n
+    """\n
+    repo = extract_repo_name_from_path(path)\n
+    is_swan = "swan" in path.lower()\n
+    return ("SWAN" if is_swan else "GGSS"), repo\n
+\n
+\n
+def toggle_upstream_path(path: str, repo: str, current_name: str) -> str:\n
+    """\n
+    Toggle SWAN insertion/removal while preserving the rest of the path:\n
+    - SWAN -> GGSS: remove SWAN segment\n
+    - GGSS -> SWAN: insert SWAN segment before repo segment\n
+    """\n
+    parts = path.replace("/", "\\").split("\\")\n
+    parts = [p for p in parts if p]\n
+\n
+    parts_no_swan = [p for p in parts if p.lower() != "swan"]\n
+\n
+    try:\n
+        idx = parts_no_swan.index(repo)\n
+    except ValueError:\n
+        die(f"Repo '{repo}' not found in upstream path '{path}'.")\n
+\n
+    if current_name == "SWAN":\n
+        new_parts = parts_no_swan\n
+    else:\n
+        new_parts = parts_no_swan[:idx] + ["SWAN"] + parts_no_swan[idx:]\n
+\n
+    new_path = "\\".join(new_parts)\n
+    if path.startswith("\\\\"):\n
+        new_path = "\\\\" + new_path\n
+\n
+    return new_path\n
+\n
+\n
+def detect_repo_name() -> str:\n
+    git_root = Path.cwd() / ".git"\n
+    if not git_root.exists():\n
+        print("ERROR: Script must be run from repo root.")\n
+        sys.exit(1)\n
+\n
+    cwd_name = Path.cwd().name\n
+    if cwd_name not in VALID_REPOS:\n
+        die(f"Current directory '{cwd_name}' is not one of: {sorted(VALID_REPOS)}")\n
+\n
+    return cwd_name\n
+\n
+\n
+def detect_current_upstream() -> str:\n
+    out, _, code = run_git(["remote", "get-url", "origin"])\n
+    if code != 0:\n
+        die("Not a git repository or no remote named 'origin'.")\n
+    return out\n
+\n
+\n
+def classify_upstream(path: str, repo: str | None = None) -> tuple[str, str]:\n
+    """Public wrapper. Returns (classification, path)."""\n
+    name, extracted_repo = classify_upstream_flex(path)\n
+    if repo and repo != extracted_repo:\n
+        die(f"Repo mismatch: expected '{repo}', got '{extracted_repo}' from '{path}'.")\n
+    return name, path\n
+\n
+\n
+def paired_upstream(repo: str, current_url: str) -> tuple[str, str]:\n
+    """Return (other_classification, toggled_url)."""\n
+    current_name, extracted_repo = classify_upstream_flex(current_url)\n
+    if extracted_repo != repo:\n
+        die(\n
+            f"Repo mismatch: expected '{repo}', got '{extracted_repo}' from '{current_url}'."\n
+        )\n
+\n
+    new_path = toggle_upstream_path(current_url, repo, current_name)\n
+    new_name = "GGSS" if current_name == "SWAN" else "SWAN"\n
+    return new_name, new_path\n
+\n
+\n
+def normalize_repo_selector(sel: str | None) -> str | None:\n
+    if sel is None:\n
+        return None\n
+\n
+    s = sel.strip().upper()\n
+    if s in ("G", "GGSS"):\n
+        return "GGSS"\n
+    if s in ("S", "SWAN"):\n
+        return "SWAN"\n
+\n
+    die(f"Invalid repo selector '{sel}'. Use G|GGSS or S|SWAN.")\n

\n

