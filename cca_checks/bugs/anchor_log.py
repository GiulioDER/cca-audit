"""Recovered finding anchors, stored separately from the append-only bug ledger.

Annotations are scoped to the original bug, repository, fix and run. Authored anchors
always win. This log enriches bug context only; it cannot create an accepted FP.
"""
from __future__ import annotations

import hashlib
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
    if (not isinstance(value, dict) or type(value.get("v")) is not int
            or value["v"] not in (1, 2)):
        return False
    if not all(isinstance(value.get(k), str) and value[k]
               for k in ("bug_id", "repo", "fix_commit")):
        return False
    run = value.get("run_id")
    if ("run_id" not in value or not (isinstance(run, str) and run)
            and not (value["v"] == 2 and run is None)):
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
    if value["v"] == 2:
        return _git_provenance(value, provenance)
    return (isinstance(provenance, dict)
            and all(isinstance(provenance.get(k), str) and _SHA.fullmatch(provenance[k])
                    for k in ("meta_sha256", "summary_sha256", "l2_sha256", "l4_sha256")))


def _git_provenance(note: dict, provenance: object) -> bool:
    if not isinstance(provenance, dict):
        return False
    if (provenance.get("method") not in ("git-finding-file", "git-finding-symbol-file")
            or provenance.get("commit") != note["fix_commit"]
            or not isinstance(provenance.get("finding_id"), str)
            or not provenance["finding_id"]
            or type(provenance.get("ordinal")) is not int or provenance["ordinal"] < 1
            or not all(isinstance(provenance.get(k), str) and _SHA.fullmatch(provenance[k])
                       for k in ("message_sha256", "summary_sha256"))
            or any(a["symbol"] is not None for a in note["anchors"])):
        return False
    refs = provenance.get("references")
    if provenance["method"] == "git-finding-symbol-file":
        if (not isinstance(provenance.get("parent"), str)
                or not SHA40.fullmatch(provenance["parent"])
                or not isinstance(refs, list)
                or not all(isinstance(r, dict) and isinstance(r.get("symbol"), str)
                           and well_formed_symbol(r["symbol"])
                           and r.get("before_sha") != r.get("after_sha")
                           and all(k in r and (r[k] is None or isinstance(r[k], str)
                                              and _SHA.fullmatch(r[k]))
                                   for k in ("before_sha", "after_sha")) for r in refs)):
            return False
    return (isinstance(refs, list) and len(refs) == len(note["anchors"])
            and all(isinstance(r, dict) and well_formed_file(r.get("file"))
                    and isinstance(r.get("token"), str) and r["token"] for r in refs)
            and {r["file"] for r in refs} == {a["file"] for a in note["anchors"]})


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
    if note["v"] == 2:
        provenance = note["provenance"]
        summary = row.get("summary")
        if not isinstance(summary, str):
            return []
        try:
            summary_sha = hashlib.sha256(summary.encode("utf-8")).hexdigest()
        except UnicodeError:
            return []
        if (summary_sha != provenance["summary_sha256"]
                or row.get("finding_id") != provenance["finding_id"]
                or type(row.get("ordinal", 1)) is not int
                or row.get("ordinal", 1) != provenance["ordinal"]):
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
