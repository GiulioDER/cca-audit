"""Append rows to the ledger and write one memo per new row.

Append-only: a row whose id is already in the ledger is not written again, and an
existing memo is never overwritten, so a second run changes nothing and a note added
by hand survives. A correction is a new row naming the one it supersedes. The one
sanctioned overwrite of memos is `records.rerender_all`, which never writes the ledger.

A bug line keeps its historical shape (`BugRow.to_json`, keyed by `bug_id`); every other
kind is stored as its record (keyed by `id`), see `records.ledger_line`.

`caused_by` derived after a row was written (`bugs backfill-caused-by`) never amends the row:
it is appended to a second append-only log, `caused_by.jsonl`, beside the ledger. A row's
effective `caused_by` is its own when non-empty, else the last annotation for its id, and every
reader takes it from `effective_caused_by`.
"""
from __future__ import annotations

import json
import os
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path

from . import anchor_log
from .model import BugRow, memo_filename
from .records import Row, ledger_line, no_nul, one_line, render_memo

LEDGER = "bugs.jsonl"
MEMOS = "memos"
ANNOTATIONS = "caused_by.jsonl"


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
    """Every bug row with effective caused_by and recovered anchors; unreadable rows are skipped.

    Neither derived field changes an authored ledger line.
    """
    if not ledger.exists():
        return []
    annotations = caused_by_annotations(ledger.parent)
    recovered = anchor_log.read(ledger.parent)
    rows = []
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            value = json.loads(line)
            if value.get("kind", "bug") == "bug":
                row = with_effective_caused_by(BugRow.from_json(value), annotations)
                rows.append(anchor_log.apply(row, recovered))
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError):
            continue
    by_id = anchor_log.inherit_rows({r.bug_id: r for r in rows})
    out = []
    for row in rows:
        inherited = by_id.get(row.bug_id)
        out.append(inherited if isinstance(inherited, BugRow) else row)
    return out


def caused_by_annotations(root: Path) -> dict[str, list[str]]:
    """bug_id to the caused_by of its LAST annotation in `caused_by.jsonl` under `root`.

    A line that does not read, or that is not an object with a string `bug_id` and a list of
    string `caused_by`, is skipped: a line torn by an interrupted append is never misread.
    """
    path = root / ANNOTATIONS
    if not path.exists():
        return {}
    out: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(value, dict):
            continue
        bug_id, causes = value.get("bug_id"), value.get("caused_by")
        if (isinstance(bug_id, str) and isinstance(causes, list)
                and all(isinstance(c, str) for c in causes)):
            out[bug_id] = list(causes)
    return out


def effective_caused_by(bug_id: str, own: list[str] | None,
                        annotations: dict[str, list[str]]) -> list[str]:
    """The row's own caused_by when non-empty (forward rows, from run_facts.json), else its last
    annotation, else empty. The one place this is decided."""
    if own:
        return list(own)
    return list(annotations.get(bug_id, []))


def with_effective_caused_by(row: Row, annotations: dict[str, list[str]]) -> Row:
    """`row` with its effective caused_by; a kind other than bug is returned as it is."""
    if not isinstance(row, BugRow):
        return row
    effective = effective_caused_by(row.bug_id, row.caused_by, annotations)
    return row if effective == row.caused_by else replace(row, caused_by=effective)


def append_annotations(root: Path, annotations: Sequence[dict]) -> int:
    """Append each annotation to `caused_by.jsonl` under `root` as one line; never rewrites.

    No lock, on purpose. The file is append-only like the ledger, and each line goes out in a
    single unbuffered write in append mode, so an appender never overwrites another's line.
    Its only writer is `bugs backfill-caused-by`, run by hand: two runs at once can at worst
    append the same derived list twice, which reads the same because the last line wins, and a
    line torn by a crash fails to parse and is skipped by `caused_by_annotations` (a newline is
    written first so the next line does not join it).
    """
    if not annotations:
        return 0
    path = root / ANNOTATIONS
    torn = False
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            torn = fh.read(1) != b"\n"
    with path.open("ab", buffering=0) as fh:
        if torn:
            fh.write(b"\n")
        for annotation in annotations:
            line = one_line(json.dumps(no_nul(annotation), ensure_ascii=False,
                                       sort_keys=True)) + "\n"
            fh.write(line.encode("utf-8"))
    return len(annotations)


def write(rows: Sequence[Row], root: Path, modified: str) -> tuple[int, int]:
    memos = root / MEMOS
    memos.mkdir(parents=True, exist_ok=True)
    ledger = root / LEDGER
    have = existing_ids(ledger)
    added = written = 0
    with ledger.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            row = no_nul(row)  # before the id: a finding id is model output too
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
