"""Recovered finding anchors, stored separately from the append-only bug ledger.

Annotations are scoped to the original bug, repository, fix and run. Authored anchors
always win. This log enriches bug context only; it cannot create an accepted FP.

A row that supersedes another and has no anchors of its own, authored or recovered, inherits
its predecessor's (`inherit_rows`, `inherit_dicts`), when every inherited file is one the
successor's fix touched. Annotations are keyed by bug id, so without this a correction such as
`bugs relink` (a run's rows re-keyed to their squash merge) dropped the place the old row had:
measured 2026-10-29, RE-call:b27298bf4125:SEC-001 lost `recall/lite/refresh.py _refresh_locked`.
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
    if (provenance.get("method") not in ("git-finding-file", "git-finding-symbol-file",
                                          "git-single-production-file", "git-leading-stem-file")
            or provenance.get("commit") != note["fix_commit"]
            or not isinstance(provenance.get("finding_id"), str)
            or not provenance["finding_id"]
            or type(provenance.get("ordinal")) is not int or provenance["ordinal"] < 1
            or not all(isinstance(provenance.get(k), str) and _SHA.fullmatch(provenance[k])
                       for k in ("message_sha256", "summary_sha256"))
            or any(a["symbol"] is not None for a in note["anchors"])):
        return False
    refs = provenance.get("references")
    if provenance["method"] in ("git-single-production-file", "git-leading-stem-file"):
        version = provenance.get("exclusions_version")
        if provenance["method"] == "git-single-production-file":
            refs_ok = refs == [{"file": a["file"]} for a in note["anchors"]]
        else:
            # The word of the summary that named the file, beside it.
            refs_ok = (isinstance(refs, list) and len(refs) == len(note["anchors"]) == 1
                       and isinstance(refs[0], dict) and set(refs[0]) == {"file", "token"}
                       and refs[0]["file"] == note["anchors"][0]["file"]
                       and isinstance(refs[0]["token"], str) and bool(refs[0]["token"]))
        return (isinstance(provenance.get("parent"), str)
                and bool(SHA40.fullmatch(provenance["parent"]))
                and isinstance(provenance.get("changed_sha256"), str)
                and bool(_SHA.fullmatch(provenance["changed_sha256"]))
                and type(version) is int and version >= 1
                and refs_ok)
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


def _fits(anchor_files: list[str], files: object) -> bool:
    return (isinstance(files, list) and all(isinstance(f, str) for f in files)
            and bool(anchor_files) and all(f in files for f in anchor_files))


def inherit_rows(rows: dict[str, Row]) -> dict[str, Row]:
    """`rows` by id, each anchorless bug row given its predecessor's anchors (chains included)."""
    out = dict(rows)
    for _ in range(len(out)):  # one link per pass; a chain of n rows settles in n passes
        changed = False
        for row_id, row in out.items():
            if not isinstance(row, BugRow) or row.anchors or not row.supersedes:
                continue
            before = out.get(row.supersedes)
            if (isinstance(before, BugRow) and before.anchors
                    and _fits([a.file for a in before.anchors], row.files)):
                out[row_id] = replace(row, anchors=list(before.anchors))
                changed = True
        if not changed:
            break
    return out


def inherit_dicts(rows: list[dict]) -> None:
    """The same rule for the legacy dictionary reader, in place."""
    by_id = {r.get("bug_id"): r for r in rows
             if isinstance(r, dict) and r.get("kind", "bug") == "bug" and r.get("bug_id")}
    for _ in range(len(by_id)):
        changed = False
        for row in by_id.values():
            before = by_id.get(row.get("supersedes"))
            anchors = before.get("anchors") if isinstance(before, dict) else None
            if (row.get("anchors") or not isinstance(anchors, list) or not anchors
                    or not all(isinstance(a, dict) and isinstance(a.get("file"), str)
                               for a in anchors)):
                continue
            if _fits([a["file"] for a in anchors], row.get("files")):
                row["anchors"] = [dict(a) for a in anchors]
                changed = True
        if not changed:
            break


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
