"""Back-filled defect classes, stored beside the append-only bug ledger in `defect_classes.jsonl`.

Findings recorded before `record L2` required a `defect_class` were classed once, afterwards,
and written there (one row per confirmed finding: `repo`, `run` (the run folder name,
`<run_id>__<commit>`), `finding_id`, `defect_class`, `defect_class_reason`). A ledger row with
no class of its own takes the one its `(repo, run_id, finding_id)` names; a class from L2 always
wins, and `bugs.jsonl` is never rewritten.

A finding the file classes twice, differently (the same run copied into two run folders), takes
neither: a wrong class is worse than none.
"""
from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from ..layer_outputs import DEFECT_CLASSES
from .model import BugRow

if TYPE_CHECKING:
    from .records import Row

NAME = "defect_classes.jsonl"
Key = tuple[str, str, str]


def _key(repo: str, run_id: str, finding_id: str) -> Key:
    return repo.lower(), run_id, finding_id


def read(root: Path) -> dict[Key, dict]:
    """Each classed finding's row, by `(repo, run_id, finding_id)`; malformed lines are ignored."""
    path = root / NAME
    if not path.exists():
        return {}
    out: dict[Key, dict] = {}
    conflicted: set[Key] = set()
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if not isinstance(value, dict) or value.get("defect_class") not in DEFECT_CLASSES:
            continue
        repo, run, finding = value.get("repo"), value.get("run"), value.get("finding_id")
        if not all(isinstance(v, str) and v for v in (repo, run, finding)):
            continue
        key = _key(repo, run.rsplit("__", 1)[0], finding)
        if key in out and out[key]["defect_class"] != value["defect_class"]:
            conflicted.add(key)
        out[key] = value
    for key in conflicted:
        del out[key]
    return out


def apply(row: Row, notes: dict[Key, dict]) -> Row:
    """`row` with the back-filled class when it is a bug row that has none of its own."""
    if not isinstance(row, BugRow) or row.defect_class is not None:
        return row
    # A malformed ledger line can hold a list here; as a dictionary key it would raise and take
    # every reader of the ledger down with it (the identity rule of anchor recovery, BUG-002).
    if not all(isinstance(v, str) and v for v in (row.repo, row.run_id, row.finding_id)):
        return row
    note = notes.get(_key(row.repo, row.run_id, row.finding_id))
    if note is None:
        return row
    reason = note.get("defect_class_reason")
    return replace(row, defect_class=note["defect_class"],
                   defect_class_reason=reason if isinstance(reason, str) else "")
