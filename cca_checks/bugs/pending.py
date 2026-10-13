"""Stage 1 of the forward ledger: keep a closed run until its pull request merges.

`pipeline close` calls `capture`. The record holds copies of the run's own files, so a
worktree or a VPS2 session copy can be deleted after the merge without losing the run.
Stage 2 (`resolve.py`) reads it. Nothing here touches the ledger.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .gitread import remote_repo
from .store import bugs_dir

PENDING = "pending"
RECORDED = "recorded"
ABANDONED = "abandoned"
RUN_FILES = ("summary.json", "L2.json", "L2.5.json", "L4.json", "L5.6.json")
SCORECARD = ".claude/audits/AUDITOR_SCORECARD.jsonl"


def _branch(root: Path) -> str | None:
    out = subprocess.run(["git", "-C", str(root), "branch", "--show-current"],
                         capture_output=True, text=True)
    name = out.stdout.strip()
    return name or None


def _own_rows(root: Path, run_id: str) -> str:
    path = root / SCORECARD
    if not path.exists():
        return ""
    keep = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("run_id") == run_id:
            keep.append(line)
    return "".join(f"{line}\n" for line in keep)


def capture(root, run_id: str) -> Path | None:
    root = Path(root)
    run_dir = root / ".claude" / "audits" / "runs" / run_id
    l4 = run_dir / "L4.json"
    fixes = json.loads(l4.read_text(encoding="utf-8-sig")) if l4.exists() else []
    if not fixes:
        return None
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    commit = summary["commit"]
    dest = bugs_dir() / PENDING / f"{run_id}__{commit[:12]}"
    dest.mkdir(parents=True, exist_ok=True)
    for name in RUN_FILES:
        if (run_dir / name).exists():
            shutil.copy2(run_dir / name, dest / name)
    (dest / "scorecard.jsonl").write_text(_own_rows(root, run_id), encoding="utf-8",
                                          newline="\n")
    meta = {
        "schema": 1, "run_id": run_id, "commit": commit,
        "repo": remote_repo(root), "branch": _branch(root),
        "captured_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": socket.gethostname(),
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return dest
