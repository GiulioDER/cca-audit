"""Append rows to the ledger and write one memo per new row.

Append-only: a row whose bug_id is already in the ledger is not written again, and an
existing memo is never overwritten, so a second run changes nothing and a note added
by hand survives. A correction is a new row naming the one it supersedes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .model import BugRow, memo_filename, render_memo

LEDGER = "bugs.jsonl"
MEMOS = "memos"


def bugs_dir() -> Path:
    env = os.environ.get("CCA_BUGS_DIR")
    return Path(env) if env else Path.home() / ".claude" / "cca-bugs"


def existing_ids(ledger: Path) -> set[str]:
    if not ledger.exists():
        return set()
    ids: set[str] = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            ids.add(json.loads(line)["bug_id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return ids


def write(rows: list[BugRow], root: Path, modified: str) -> tuple[int, int]:
    memos = root / MEMOS
    memos.mkdir(parents=True, exist_ok=True)
    ledger = root / LEDGER
    have = existing_ids(ledger)
    added = written = 0
    with ledger.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            if row.bug_id in have:
                continue
            fh.write(json.dumps(row.to_json(), ensure_ascii=False, sort_keys=True) + "\n")
            have.add(row.bug_id)
            added += 1
            memo = memos / memo_filename(row.bug_id)
            if not memo.exists():
                memo.write_text(render_memo(row, modified), encoding="utf-8", newline="\n")
                written += 1
    return added, written
