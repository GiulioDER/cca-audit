"""The live round-trip check: every ledger row against RE-call's dump of the records it serves.

The input is `recall records --all --json` (RE-call's contract C5): an envelope whose `records`
each carry `recall_record` exactly as authored. A row passes only when the dump holds a record
equal to `to_record(row)` as a JSON value; equality is taken on canonical JSON, so `true` and
`1`, or `1` and `1.0`, are different values here as they are in the file.

Four lists come back, and any entry in any of them fails the check:

- `missing`: a ledger row with no record in the dump. A memo written before records existed
  is served without one, so it counts here until `bugs rerender --all` and a rebuild.
- `changed`: a record that differs from its row, with the top-level fields that differ.
- `orphan`: a record in the dump with no ledger row.
- `unreadable`: a ledger line or dump entry that could not be read, so nothing can be claimed
  about it.

A dump that says it is `truncated` is incomplete, and the check refuses to run on it rather
than report every record past the cut as missing.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from .records import read_ledger, to_record


class IncompleteDump(ValueError):
    pass


@dataclass
class RoundTrip:
    rows: int = 0
    records: int = 0
    missing: list[str] = field(default_factory=list)
    changed: list[str] = field(default_factory=list)
    orphan: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not (self.missing or self.changed or self.orphan or self.unreadable)


def _canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def served_records(dumps: list[dict]) -> tuple[dict[str, dict], list[str]]:
    """Record id to `recall_record` across the dump pages, and the entries that did not read.

    A code chunk (`kind: code`, from a code tenant) is not a ledger record and is skipped. An
    entry without a `recall_record` object is not a record either: its row stays missing.
    """
    served: dict[str, dict] = {}
    unreadable: list[str] = []
    for page_number, dump in enumerate(dumps, 1):
        if not isinstance(dump, dict) or not isinstance(dump.get("records"), list):
            raise ValueError(f"dump {page_number} is not a recall records envelope")
        for entry in dump["records"]:
            if not isinstance(entry, dict) or entry.get("kind") == "code":
                continue
            record = entry.get("recall_record")
            if not isinstance(record, dict):
                continue
            record_id = record.get("id")
            if not isinstance(record_id, str) or not record_id:
                unreadable.append(f"dump {page_number}: a record with no id")
                continue
            if record_id in served and _canonical(served[record_id]) != _canonical(record):
                unreadable.append(f"dump {page_number}: {record_id} served twice, differently")
                continue
            served[record_id] = record
    if dumps and dumps[-1].get("truncated") is True:
        raise IncompleteDump("the last dump page says truncated: the dump is incomplete")
    return served, unreadable


def check(root: Path, dumps: list[dict]) -> RoundTrip:
    """Compare the ledger under `root` with RE-call's dump pages, in order."""
    rows, skipped = read_ledger(root)
    served, unreadable = served_records(dumps)
    result = RoundTrip(rows=len(rows), records=len(served), unreadable=[*skipped, *unreadable])
    for row_id, row in rows.items():
        got = served.get(row_id)
        if got is None:
            result.missing.append(row_id)
            continue
        want = to_record(row)
        fields = sorted(k for k in set(want) | set(got)
                        if k not in got or k not in want
                        or _canonical(got[k]) != _canonical(want[k]))
        if fields:
            result.changed.append(f"{row_id}: {', '.join(fields)}")
    result.orphan = sorted(set(served) - set(rows))
    return result


def load_dumps(paths: list[Path]) -> list[dict]:
    return [json.loads(Path(p).read_text(encoding="utf-8-sig")) for p in paths]
