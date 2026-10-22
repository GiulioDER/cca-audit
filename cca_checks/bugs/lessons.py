"""Accepting and retracting lessons: the owner's half of `distill.py`.

`accept` turns a stored proposal into a `LessonRow` and its memo, through `store.write` (append
only, U+0000 replaced). It refuses a proposal it could not stand behind: fewer than three
members, a member that is not a bug of the same repository in the ledger, an anchor that
pins a code version or is not a well-formed place, and a rule or pattern that tells an auditor to
withhold a finding. The accepted proposal file moves from `lessons/proposed/` to
`lessons/accepted/`. A lesson never lapses when code changes; it ends only by `retract`, which
appends a `lesson_retraction` row.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from .anchors import Anchor, well_formed_file, well_formed_symbol
from .distill import (
    ACCEPTED,
    MIN_MEMBERS,
    PATTERN_MAX,
    PROPOSED,
    RULE_MAX,
    one_sentence,
    proposal_path,
)
from .model import BugRow
from .records import LessonRow, RetractionRow, from_record, no_nul, read_ledger, to_record


class Refused(ValueError):
    """The proposal or the request cannot become a ledger row; nothing was written."""


@dataclass
class Written:
    id: str
    rows: int
    memos: int


def load_proposal(root: Path, ref: str) -> tuple[dict, Path]:
    """The proposal `ref` names: a path to its file, or its lesson id."""
    path = Path(ref)
    if not path.is_file():
        path = proposal_path(root, ref)
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise Refused(f"no proposal {ref!r} (looked for {path})") from None
    except (OSError, ValueError) as exc:
        raise Refused(f"{path}: {type(exc).__name__}: {exc}") from None
    if not isinstance(value, dict):
        raise Refused(f"{path}: a proposal is a JSON object")
    return value, path


def _text(proposal: dict, key: str) -> str:
    value = proposal.get(key)
    if not isinstance(value, str) or not value.strip():
        raise Refused(f"the proposal has no {key}")
    return value.strip()


def _anchors(raw: object) -> list[Anchor]:
    if not isinstance(raw, list) or not raw:
        raise Refused("a lesson needs at least one anchor")
    out = []
    for a in raw:
        if not isinstance(a, dict) or set(a) != {"file", "symbol", "code_sha"}:
            raise Refused(f"an anchor is {{file, symbol, code_sha}}, got {a!r}")
        if a["code_sha"] is not None:
            raise Refused("a lesson names a place, not one version of its code: code_sha must "
                          "be null")
        if not well_formed_file(a["file"]) or not well_formed_symbol(a["symbol"]):
            raise Refused(f"not a well-formed place: {a!r}")
        out.append(Anchor(a["file"], a["symbol"], None))
    return out


def lesson_from(proposal: dict, rows: dict, *, by: str, on: str) -> LessonRow:
    """The lesson row `proposal` becomes when `by` accepts it on `on`; Refused when it cannot."""
    by = by.strip()
    if not by:
        raise Refused("--by must name who accepts the lesson")
    project, repo = _text(proposal, "project"), _text(proposal, "repo")
    try:
        rule = one_sentence(proposal.get("rule"), RULE_MAX)
        pattern = one_sentence(proposal.get("pattern"), PATTERN_MAX)
    except ValueError as exc:
        raise Refused(f"rule or pattern: {exc}") from None
    members = proposal.get("members")
    if not isinstance(members, list) or not all(isinstance(m, str) for m in members):
        raise Refused("members is a list of bug ids")
    members = sorted(set(members))
    if len(members) < MIN_MEMBERS:
        raise Refused(f"a lesson needs at least {MIN_MEMBERS} member bugs, got {len(members)}")
    # A superseded bug may be cited: it happened, and the golden lesson (contract C4) cites one.
    for member in members:
        row = rows.get(member)
        if not isinstance(row, BugRow) or row.repo.lower() != repo.lower():
            raise Refused(f"member {member!r} is not a bug of {repo} in the ledger")
    lesson = LessonRow(project=project, repo=repo, date=on, rule=rule, pattern=pattern,
                       anchors=_anchors(proposal.get("anchors")), members=members,
                       accepted_by=by, accepted_at=on)
    # The record must rebuild to the same row, as RE-call will return it (contract C1).
    if from_record(to_record(lesson)) != lesson:
        raise Refused("the lesson record does not rebuild to the same row")
    return lesson


def accept(root: Path, ref: str, *, by: str, on: str, modified: str) -> Written:
    """Append the lesson `ref` proposes to the ledger and write its memo; the ledger is
    otherwise only read. Refused when the lesson is already in the ledger."""
    from .store import write

    proposal, path = load_proposal(root, ref)
    rows, skipped = read_ledger(root)
    if skipped:
        raise Refused(f"{len(skipped)} ledger line(s) cannot be read; the members cannot be "
                      "checked")
    lesson = no_nul(lesson_from(proposal, rows, by=by, on=on))
    if lesson.id in rows:
        raise Refused(f"{lesson.id} is already a lesson in the ledger")
    added, memos = write([lesson], root, modified)
    target = proposal_path(root, lesson.id, ACCEPTED)
    target.parent.mkdir(parents=True, exist_ok=True)
    if path.resolve().parent == (root / PROPOSED).resolve():
        os.replace(path, target)
    else:
        # A proposal the owner keeps elsewhere stays where it is; the queue gets a copy.
        target.write_bytes(path.read_bytes())
    return Written(lesson.id, added, memos)


def retract(root: Path, lesson_id: str, *, reason: str, by: str, on: str,
            modified: str) -> Written:
    """Append a `lesson_retraction` of `lesson_id`; a second retraction of it writes nothing."""
    from .store import write

    rows, _ = read_ledger(root)
    target = rows.get(lesson_id)
    if not isinstance(target, LessonRow):
        raise Refused(f"{lesson_id!r} is not a lesson of the ledger")
    reason = " ".join(reason.split())
    if not reason:
        raise Refused("--reason must not be empty")
    if not by.strip():
        raise Refused("--by must name who retracts the lesson")
    row = RetractionRow(kind="lesson_retraction", project=target.project, repo=target.repo,
                        date=on, target_id=target.id, reason=reason, retracted_by=by.strip())
    added, memos = write([row], root, modified)
    return Written(row.id, added, memos)
