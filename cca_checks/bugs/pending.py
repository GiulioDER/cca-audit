"""Stage 1 of the forward ledger: keep a closed run until its pull request merges.

`pipeline close` calls `capture`. The record holds copies of the run's own files, so a
worktree or a VPS2 session copy can be deleted after the merge without losing the run.
Stage 2 (`resolve.py`) reads it. Nothing here touches the ledger.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from . import run_facts
from .gitread import remote_repo
from .store import bugs_dir

PENDING = "pending"
RECORDED = "recorded"
ABANDONED = "abandoned"
TMP_PREFIX = ".tmp-"
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


def _write_facts(root: Path, run_dir: Path, summary: dict, fixes: list, dest: Path) -> None:
    """Anchors and blamed commits of the fixed findings (run_facts.py), from this checkout.

    Never fails a capture: a run whose facts could not be computed is still kept, and the
    reason is written in their place.
    """
    commit, base = summary.get("commit"), summary.get("base_commit")
    if not (isinstance(base, str) and base and isinstance(commit, str)) or base == commit:
        return
    try:
        l2_path = run_dir / "L2.json"
        l2 = json.loads(l2_path.read_text(encoding="utf-8-sig")) if l2_path.exists() else []
        facts = run_facts.compute(root, base, commit, l2 if isinstance(l2, list) else [],
                                  fixes if isinstance(fixes, list) else [])
    except Exception as exc:  # noqa: BLE001 - see the docstring
        facts = {"schema": run_facts.SCHEMA, "error": f"{type(exc).__name__}: {exc}"}
    (dest / run_facts.NAME).write_text(json.dumps(facts, indent=1) + "\n", encoding="utf-8")


def capture(root, run_id: str) -> Path | None:
    root = Path(root)
    run_dir = root / ".claude" / "audits" / "runs" / run_id
    l4 = run_dir / "L4.json"
    fixes = json.loads(l4.read_text(encoding="utf-8-sig")) if l4.exists() else []
    if not fixes:
        return None
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    commit = summary["commit"]
    pending = bugs_dir() / PENDING
    dest = pending / f"{run_id}__{commit[:12]}"
    # Built beside pending/<name> and renamed in, so a reader (resolve, or the VPS2
    # harvest) never sees a half-written record.
    tmp = pending / f"{TMP_PREFIX}{dest.name}-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True)
    try:
        for name in RUN_FILES:
            if (run_dir / name).exists():
                shutil.copy2(run_dir / name, tmp / name)
        (tmp / "scorecard.jsonl").write_text(_own_rows(root, run_id), encoding="utf-8",
                                             newline="\n")
        _write_facts(root, run_dir, summary, fixes, tmp)
        meta = {
            "schema": 1, "run_id": run_id, "commit": commit,
            "repo": remote_repo(root), "branch": _branch(root),
            "captured_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "host": socket.gethostname(),
            # Closed before the fixes were committed: HEAD is the base, a commit an
            # earlier pull request already merged, so no PR can be matched to this run.
            "commit_is_base": commit == summary.get("base_commit"),
        }
        (tmp / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
        if dest.exists():
            shutil.rmtree(dest)
        tmp.rename(dest)
    except BaseException:
        shutil.rmtree(tmp, ignore_errors=True)
        raise
    return dest
