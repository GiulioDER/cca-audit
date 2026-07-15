#!/usr/bin/env python3
"""Build the workflow task list + the scorer's ground-truth index from data/."""
import os, json, glob

BASE = os.environ.get("CCA_BENCH_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DATA = os.path.join(BASE, "data")
NUMERIC = {"pandas", "matplotlib", "keras"}   # dispatch numeric-auditor for these

tasks, bugs = [], {}
for gtp in sorted(glob.glob(os.path.join(DATA, "*", "groundtruth.json"))):
    g = json.load(open(gtp, encoding="utf-8"))
    bug = f"{g['project']}/{g['bug_id']}"
    d = os.path.dirname(gtp)
    bugs[bug] = {"project": g["project"], "files": {}}
    for path, hunks in g["files"].items():
        buggy = os.path.join(d, "buggy", path.replace("/", os.sep))
        fixed = os.path.join(d, "fixed", path.replace("/", os.sep))
        if not (os.path.exists(buggy) and os.path.exists(fixed)):
            print(f"  SKIP missing file: {bug} {path}")
            continue
        bw = [[h["old_start"], h["old_start"] + max(h["old_len"], 1) - 1] for h in hunks]
        fw = [[h["new_start"], h["new_start"] + max(h["new_len"], 1) - 1] for h in hunks]
        tasks.append({
            "bug": bug, "project": g["project"], "file": path,
            "numeric": g["project"] in NUMERIC,
            "buggy_path": buggy.replace("\\", "/"),
            "fixed_path": fixed.replace("\\", "/"),
        })
        bugs[bug]["files"][path] = {"buggy_windows": bw, "fixed_windows": fw}

json.dump(tasks, open(os.path.join(BASE, "harness", "tasks_wf.json"), "w"), indent=2)
json.dump(bugs, open(os.path.join(BASE, "harness", "bugs_index.json"), "w"), indent=2)
print(f"{len(tasks)} file-tasks across {len(bugs)} bugs "
      f"({sum(t['numeric'] for t in tasks)} numeric)")
