"""Recovered finding anchors, stored separately from the append-only bug ledger.

Annotations are scoped to the original bug, repository, fix and run. Authored anchors
always win. This log enriches bug context only; it cannot create an accepted FP.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from .anchors import Anchor, well_formed_file, well_formed_symbol
from .model import SHA40, BugRow

if TYPE_CHECKING:
    from .records import Row

NAME = "anchors.jsonl"
_SHA = re.compile(r"[0-9a-f]{64}")


def valid(value: object) -> bool:
    if not isinstance(value, dict) or type(value.get("v")) is not int or value["v"] != 1:
        return False
    if not all(isinstance(value.get(k), str) and value[k]
               for k in ("bug_id", "repo", "run_id", "fix_commit")):
        return False
    if not SHA40.fullmatch(value["fix_commit"]):
        return False
    anchors = value.get("anchors")
    if not isinstance(anchors, list) or not anchors or len(anchors) > 200:
        return False
    for a in anchors:
        if not isinstance(a, dict) or set(a) != {"file", "symbol", "code_sha"}:
            return False
        sha = a["code_sha"]
        if not (well_formed_file(a["file"]) and well_formed_symbol(a["symbol"])
                and isinstance(sha, str) and _SHA.fullmatch(sha)):
            return False
    provenance = value.get("provenance")
    return (isinstance(provenance, dict)
            and all(isinstance(provenance.get(k), str) and _SHA.fullmatch(provenance[k])
                    for k in ("meta_sha256", "summary_sha256", "l2_sha256", "l4_sha256")))


def read(root: Path) -> dict[str, dict]:
    """Last valid annotation per bug; malformed or interrupted lines grant no anchors."""
    path = root / NAME
    if not path.exists():
        return {}
    out: dict[str, dict] = {}
    for line in path.read_text(encoding="utf-8", errors="replace").split("\n"):
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if valid(value):
            out[value["bug_id"]] = value
    return out


def effective(row: dict, annotations: dict[str, dict]) -> list:
    """One rule for all ledger readers, including the legacy dictionary reader."""
    own = row.get("anchors")
    if own:
        return own if isinstance(own, list) else []
    note = annotations.get(row.get("bug_id", ""))
    if note is None or not valid(note):
        return []
    repo = row.get("repo")
    if (row.get("kind", "bug") != "bug" or not isinstance(repo, str)
            or repo.lower() != note["repo"].lower()
            or row.get("fix_commit") != note["fix_commit"]
            or row.get("run_id") != note["run_id"]):
        return []
    files = row.get("files")
    if (not isinstance(files, list) or not all(isinstance(f, str) for f in files)
            or not all(a["file"] in files for a in note["anchors"])):
        return []
    return [dict(a) for a in note["anchors"]]


def apply(row: Row, annotations: dict[str, dict]) -> Row:
    if not isinstance(row, BugRow) or row.anchors:
        return row
    anchors = effective(row.to_json(), annotations)
    return replace(row, anchors=[Anchor(**a) for a in anchors]) if anchors else row


def append(root: Path, notes: list[dict]) -> int:
    """Append validated provenance in single writes; never open bugs.jsonl for writing."""
    from .records import one_line

    if not notes:
        return 0
    if not all(valid(note) for note in notes):
        raise ValueError("invalid recovered anchor annotation")
    root.mkdir(parents=True, exist_ok=True)
    path = root / NAME
    torn = False
    if path.exists() and path.stat().st_size:
        with path.open("rb") as fh:
            fh.seek(-1, os.SEEK_END)
            torn = fh.read(1) != b"\n"
    with path.open("ab", buffering=0) as fh:
        if torn:
            fh.write(b"\n")
        for note in notes:
            data = one_line(json.dumps(note, ensure_ascii=False, sort_keys=True)) + "\n"
            encoded = data.encode("utf-8")
            if fh.write(encoded) != len(encoded):
                raise OSError("incomplete anchor annotation write")
    return len(notes)
