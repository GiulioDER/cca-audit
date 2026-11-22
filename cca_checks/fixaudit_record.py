"""Bookkeeping for the fix audit layer L5.4: the repair journal and the side ledger of verdicts.

Both files are append-only, like the disposition ledger: a line is never rewritten. The journal
lives in the run's directory and is what makes the 3-cycle repair cap a rule the code enforces,
not one the agent is trusted to count. The side ledger is a separate file because fix audit
verdicts reuse the words CONFIRMED and FALSE_POSITIVE, and the disposition ledger's readers
would tally them as L2.5 dispositions.
"""

from __future__ import annotations

import datetime
import json
import pathlib

JOURNAL = "fixaudit-journal.jsonl"
LEDGER = pathlib.Path(".claude/audits/FIXAUDIT_LEDGER.jsonl")
#: A finding that stays CONFIRMED through this many repair cycles is parked, not retried.
CAP = 3


def _append(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def _journal_path(root, run_id: str) -> pathlib.Path:
    return pathlib.Path(root) / ".claude" / "audits" / "runs" / run_id / JOURNAL


def _rows(root, run_id: str) -> list[dict]:
    path = _journal_path(root, run_id)
    if not path.exists():
        return []
    text = path.read_text(encoding="utf-8-sig")
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def journal(root, run_id: str, *, finding: str, fix_id: str, pass_no: int, verdict: str,
            reason: str) -> None:
    """Append one fix audit outcome for `finding`, the original finding the fix answered."""
    _append(_journal_path(root, run_id), [{
        "finding": finding, "fix_id": fix_id, "pass": pass_no, "verdict": verdict,
        "reason": reason,
    }])


def cycles(root, run_id: str) -> dict[str, int]:
    """Per original finding, how many fix audit passes confirmed a defect in its fix."""
    counts: dict[str, int] = {}
    for row in _rows(root, run_id):
        if row.get("verdict") == "CONFIRMED":
            counts[row["finding"]] = counts.get(row["finding"], 0) + 1
    return counts


def parked(root, run_id: str) -> set[str]:
    """Findings that reached the cap, plus any the journal parks outright."""
    held = {finding for finding, n in cycles(root, run_id).items() if n >= CAP}
    return held | {row["finding"] for row in _rows(root, run_id) if row.get("verdict") == "PARKED"}


def next_pass(root, run_id: str) -> int:
    """One past the highest pass journaled, so numbering survives a pause and resume."""
    return 1 + max((row["pass"] for row in _rows(root, run_id)), default=0)


def append_verdicts(root, run_id: str, audit: dict) -> int:
    """Append one side ledger row per finding of a checked L5.4 file; return how many."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    rows = [{**finding, "run_id": run_id, "layer": "L5.4", "pass": audit["pass"], "ts": ts}
            for finding in audit["findings"]]
    _append(pathlib.Path(root) / LEDGER, rows)
    return len(rows)
