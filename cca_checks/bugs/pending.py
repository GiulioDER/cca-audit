"""Stage 1 of the forward ledger: keep a closed run until its pull request merges.

`pipeline close` calls `capture`. The record holds copies of the run's own files, so a
worktree or a VPS2 session copy can be deleted after the merge without losing the run.
Stage 2 (`resolve.py`) reads it. Bug rows wait for that merge; the run's accepted false
positives do not (`fp_records.py`): they are appended to the ledger here, at close, and a run
that fixed nothing but has some is kept straight under `recorded/`, since no pull request will
ever hold it.
"""
from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import fp_records, run_facts
from .gitread import remote_repo
from .store import bugs_dir, write

PENDING = "pending"
RECORDED = "recorded"
ABANDONED = "abandoned"
TMP_PREFIX = ".tmp-"
RUN_FILES = ("summary.json", "L2.json", "L2.5.json", "L4.json", "L5.6.json", "L6.json",
             "fp_anchors.json", "match_fp.json", "fp-records.json", "prior-records.json",
             "prior-code.json")
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


def _report_skipped(skipped: list[str]) -> None:
    """Say which false positives became no record, and why, so a lost one is never silent."""
    if skipped:
        print("note: false positives not recorded for later runs: " + "; ".join(skipped),
              file=sys.stderr)


def capture(root, run_id: str, verdict: str = "") -> Path | None:
    """Keep the run for the ledger; `verdict` is its final L6 verdict.

    A run with neither fixes nor accepted false positives keeps nothing and returns None.
    """
    root = Path(root)
    run_dir = root / ".claude" / "audits" / "runs" / run_id
    l4 = run_dir / "L4.json"
    fixes = json.loads(l4.read_text(encoding="utf-8-sig")) if l4.exists() else []
    stamp = datetime.now(timezone.utc)
    picked, skipped = fp_records.eligible_for_run(root, run_id, verdict)
    if not fixes and not picked:
        _report_skipped(skipped)
        return None
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    commit = summary["commit"]
    # A run that fixed nothing waits for no pull request: its accepted false positives are
    # written now, and its record is kept straight under recorded/.
    pending = bugs_dir() / (PENDING if fixes else RECORDED)
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
        repo = remote_repo(root)
        accepted, more = fp_records.to_rows(picked, repo=repo, date=stamp.strftime("%Y-%m-%d"))
        skipped += more
        if accepted:
            # Before the record is renamed in: a row written twice is skipped by id, a lost
            # one is not.
            write(accepted, bugs_dir(), stamp.strftime("%Y-%m-%dT%H:%M:%SZ"))
        (tmp / fp_records.REPORT).write_text(json.dumps(
            {"written": [r.id for r in accepted], "skipped": skipped}, indent=1) + "\n",
            encoding="utf-8")
        meta = {
            "schema": 1, "run_id": run_id, "commit": commit,
            "repo": repo, "branch": _branch(root),
            "captured_utc": stamp.strftime("%Y-%m-%dT%H:%M:%SZ"),
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
    _report_skipped(skipped)
    return dest
