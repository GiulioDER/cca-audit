"""Append rows to the ledger and write one memo per new row.

Append-only: a row whose id is already in the ledger is not written again, and an
existing memo is never overwritten, so a second run changes nothing and a note added
by hand survives. A correction is a new row naming the one it supersedes. The one
sanctioned overwrite of memos is `records.rerender_all`, which never writes the ledger.

A bug line keeps its historical shape (`BugRow.to_json`, keyed by `bug_id`); every other
kind is stored as its record (keyed by `id`), see `records.ledger_line`.
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence
from pathlib import Path

from .model import BugRow, memo_filename
from .records import Row, ledger_line, one_line, render_memo

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
            value = json.loads(line)
            ids.add(value["bug_id"] if "bug_id" in value else value["id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return ids


def bug_rows(ledger: Path) -> list[BugRow]:
    """Every bug row the ledger holds; a line that does not read is skipped, as above."""
    if not ledger.exists():
        return []
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if value.get("kind", "bug") == "bug":
                rows.append(BugRow.from_json(value))
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            continue
    return rows


def write(rows: Sequence[Row], root: Path, modified: str) -> tuple[int, int]:
    memos = root / MEMOS
    memos.mkdir(parents=True, exist_ok=True)
    ledger = root / LEDGER
    have = existing_ids(ledger)
    added = written = 0
    with ledger.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            if row.id in have:
                continue
            # one_line: every reader of the ledger splits it with splitlines().
            fh.write(one_line(json.dumps(ledger_line(row), ensure_ascii=False,
                                         sort_keys=True)) + "\n")
            have.add(row.id)
            added += 1
            memo = memos / memo_filename(row.id)
            if not memo.exists():
                memo.write_text(render_memo(row, modified=modified), encoding="utf-8",
                                newline="\n")
                written += 1
    return added, written
