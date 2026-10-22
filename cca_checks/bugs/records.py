"""The record format CCA writes and RE-call returns: one front matter key, nothing lost.

Every memo carries `recall_record`, a one-line JSON object holding every field of its ledger
row. RE-call returns that object exactly as authored, so a row rebuilt from RE-call's answer
equals the row CCA wrote: `from_record(to_record(row)) == row` for every kind. The prose body
is rendered from the record for people and for embedding, and is never parsed back.

The schema is RE-call's `recall/schemas/recall_record.v1.json` (contract C1); the golden memos
`render_memo` must reproduce byte for byte are its contract C4. Both are copied into
`tests/fixtures/audit_memory/` from `RECALL_CONTRACT_COMMIT`. See RE-call's
`docs/AUDIT_MEMORY_DESIGN.md`.
"""
from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, fields, is_dataclass, replace
from pathlib import Path
from typing import ClassVar, TypeVar, cast

from .anchors import Anchor
from .model import ABSENT, CONDITIONS, NOT_REQUIRED, BugRow, _slug, memo_filename

#: The RE-call commit the contract files were copied from. Re-pin, and re-copy, when RE-call
#: changes them; `tests/test_bug_records_roundtrip.py` fails on a copy that drifted. This is
#: the squash merge of RE-call PR #973; its files are byte-identical to the PR head c526ced82
#: the copies were first taken from.
RECALL_CONTRACT_COMMIT = "20baf3a8b330d38887b7f8b3b75fd4ac5cd477c1"
VERSION = 1
FP_REASON = "not_real"
RETRACTION_KINDS = ("false_positive_retraction", "lesson_retraction")
_DESCRIPTION_MAX = 120
_LABELS = {NOT_REQUIRED: "not required (not a P1 fix)"}
# Characters `str.splitlines()` and YAML treat as line breaks that canonical JSON leaves raw.
# Escaped, a front matter line stays one line for every reader and parses to the same value.
_LINE_BREAKS = ("\x85", " ", " ")


@dataclass
class FalsePositiveRow:
    """A finding fp-check marked FALSE_POSITIVE (not_real) and the L6 architect accepted."""

    project: str
    repo: str
    date: str
    run_id: str
    finding_id: str
    category: str
    severity: str
    priority: str
    high_stakes: bool
    auditor: str
    claim: str
    evidence_refs: list[str]
    verify_source: str
    # Every anchor carries a code_sha: the record lapses when that code changes.
    anchors: list[Anchor]
    fp_reason: str = FP_REASON
    kind: ClassVar[str] = "false_positive"

    @property
    def id(self) -> str:
        return f"{self.project}:fp:{self.run_id}:{self.finding_id}"


@dataclass
class LessonRow:
    """A rule distilled from three or more bugs, accepted by the owner."""

    project: str
    repo: str
    date: str
    rule: str
    pattern: str
    # A lesson is about a place, not one version of its code: every code_sha is None.
    anchors: list[Anchor]
    members: list[str]
    accepted_by: str
    accepted_at: str
    kind: ClassVar[str] = "lesson"

    @property
    def id(self) -> str:
        digest = hashlib.sha256("\n".join(sorted(self.members)).encode("utf-8")).hexdigest()
        return f"{self.project}:lesson:{digest[:12]}"


@dataclass
class RetractionRow:
    """Retracts a false positive or a lesson; it supersedes the record it names."""

    kind: str
    project: str
    repo: str
    date: str
    target_id: str
    reason: str
    retracted_by: str

    @property
    def id(self) -> str:
        return f"{self.target_id}~retract"


Row = BugRow | FalsePositiveRow | LessonRow | RetractionRow

_COMMON = ("v", "kind", "id", "project", "repo", "date")
FIELDS: dict[str, tuple[str, ...]] = {
    "bug": (*_COMMON, "bug_id", "tier", "files", "finding_id", "category", "severity",
            "auditor", "fix_commit", "run_id", "evidence", "sources", "summary", "supersedes",
            "ordinal", "anchors", "caused_by"),
    "false_positive": (*_COMMON, "run_id", "finding_id", "category", "severity", "priority",
                       "high_stakes", "auditor", "claim", "fp_reason", "evidence_refs",
                       "verify_source", "anchors"),
    "lesson": (*_COMMON, "rule", "pattern", "anchors", "members", "accepted_by",
               "accepted_at"),
    **{kind: (*_COMMON, "target_id", "reason", "retracted_by") for kind in RETRACTION_KINDS},
}


def _anchors(anchors: list[Anchor]) -> list[dict]:
    return [a.to_json() for a in anchors]


def to_record(row: Row) -> dict:
    """The `recall_record` object for `row`: every field, so the row can be rebuilt from it."""
    head = {"v": VERSION, "kind": row.kind, "id": row.id, "project": row.project,
            "repo": row.repo, "date": row.date}
    if isinstance(row, BugRow):
        body = {k: v for k, v in row.to_json().items() if k != "kind"}
    elif isinstance(row, FalsePositiveRow):
        body = {"run_id": row.run_id, "finding_id": row.finding_id, "category": row.category,
                "severity": row.severity, "priority": row.priority,
                "high_stakes": row.high_stakes, "auditor": row.auditor, "claim": row.claim,
                "fp_reason": row.fp_reason, "evidence_refs": list(row.evidence_refs),
                "verify_source": row.verify_source, "anchors": _anchors(row.anchors)}
    elif isinstance(row, LessonRow):
        body = {"rule": row.rule, "pattern": row.pattern, "anchors": _anchors(row.anchors),
                "members": list(row.members), "accepted_by": row.accepted_by,
                "accepted_at": row.accepted_at}
    else:
        body = {"target_id": row.target_id, "reason": row.reason,
                "retracted_by": row.retracted_by}
    return {**head, **body}


def _fields(record: dict) -> str:
    """The record's kind, after checking it holds exactly that kind's fields at version 1."""
    if not isinstance(record, dict):
        raise ValueError(f"a record is a JSON object, not {type(record).__name__}")
    kind = record.get("kind")
    if kind not in FIELDS:
        raise ValueError(f"unknown record kind {kind!r}")
    if record.get("v") != VERSION:
        raise ValueError(f"record version {record.get('v')!r} is not {VERSION}")
    want, have = set(FIELDS[kind]), set(record)
    if have != want:
        raise ValueError(f"{kind} record: missing {sorted(want - have)}, "
                         f"unknown {sorted(have - want)}")
    return kind


def _anchor(raw: dict) -> Anchor:
    if not isinstance(raw, dict) or set(raw) != {"file", "symbol", "code_sha"}:
        raise ValueError(f"an anchor is {{file, symbol, code_sha}}, got {raw!r}")
    return Anchor(raw["file"], raw["symbol"], raw["code_sha"])


def from_record(record: dict) -> Row:
    """The row `record` was made from. Refuses anything it could not rebuild exactly.

    Derived fields (`id`, a bug's `bug_id` and `tier`) must agree with the rebuilt row: a
    record that disagrees with itself is refused rather than silently corrected.
    """
    kind = _fields(record)
    common = {k: record[k] for k in ("project", "repo", "date")}
    row: Row
    if kind == "bug":
        for raw in record["anchors"]:
            _anchor(raw)
        row = BugRow.from_json(record)
        if not (record["bug_id"] == record["id"] == row.bug_id and record["tier"] == row.tier):
            raise ValueError(f"bug record {record['id']!r} disagrees with its own fields")
    elif kind == "false_positive":
        if record["fp_reason"] != FP_REASON:
            raise ValueError(f"fp_reason {record['fp_reason']!r} cannot suppress anything")
        anchors = [_anchor(a) for a in record["anchors"]]
        if not anchors or any(a.code_sha is None for a in anchors):
            raise ValueError("a false positive needs anchors that each carry a code_sha")
        row = FalsePositiveRow(
            **common, run_id=record["run_id"], finding_id=record["finding_id"],
            category=record["category"], severity=record["severity"],
            priority=record["priority"], high_stakes=record["high_stakes"],
            auditor=record["auditor"], claim=record["claim"],
            evidence_refs=list(record["evidence_refs"]), verify_source=record["verify_source"],
            anchors=anchors, fp_reason=record["fp_reason"])
    elif kind == "lesson":
        row = LessonRow(
            **common, rule=record["rule"], pattern=record["pattern"],
            anchors=[_anchor(a) for a in record["anchors"]], members=list(record["members"]),
            accepted_by=record["accepted_by"], accepted_at=record["accepted_at"])
    else:
        row = RetractionRow(kind=kind, **common, target_id=record["target_id"],
                            reason=record["reason"], retracted_by=record["retracted_by"])
    if row.id != record["id"]:
        raise ValueError(f"record id {record['id']!r} is not the row's {row.id!r}")
    return row


_T = TypeVar("_T")


def no_nul(value: _T) -> _T:
    """`value` with U+0000 in every string replaced by U+FFFD, through lists, dicts and rows.

    PostgreSQL jsonb cannot store U+0000, so RE-call refuses such a record at index time and
    `roundtrip-check` would list it as missing. Summaries come from commit subjects and model
    output CCA does not control, so every writer of the ledger, the caused_by log and memos
    passes what it writes through this first, as `model._printable` does for display.

    The result has the input's type: `_scrub` rebuilds each container and dataclass as its own
    kind, which is what the cast states.
    """
    return cast(_T, _scrub(value))


def _scrub(value: object) -> object:
    if isinstance(value, str):
        return value.replace("\x00", "\ufffd")
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, tuple):
        return tuple(_scrub(v) for v in value)
    if isinstance(value, dict):
        return {_scrub(k): _scrub(v) for k, v in value.items()}
    if is_dataclass(value) and not isinstance(value, type):
        return replace(value, **{f.name: _scrub(getattr(value, f.name))
                                 for f in fields(value) if f.init})
    return value


def one_line(text: str) -> str:
    """JSON text with the line separators `json.dumps` leaves raw escaped; the value is equal."""
    for char in _LINE_BREAKS:
        text = text.replace(char, f"\\u{ord(char):04x}")
    return text


def record_json(record: dict) -> str:
    """The canonical one-line JSON for `record`, as RE-call's contract fixes it."""
    return one_line(json.dumps(record, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":")))


def _flat(text: str) -> str:
    return " ".join(text.split())[:_DESCRIPTION_MAX]


def _description(row: Row) -> str:
    if isinstance(row, BugRow):
        return f"{row.project} {row.finding_id} ({row.tier}): {_flat(row.summary)}"
    if isinstance(row, FalsePositiveRow):
        return f"{row.project} {row.finding_id} (false positive): {_flat(row.claim)}"
    if isinstance(row, LessonRow):
        return f"{row.project} lesson: {_flat(row.rule)}"
    return f"retracts {row.target_id}: {_flat(row.reason)}"


def _memo_stem(record_id: str) -> str:
    return memo_filename(record_id)[: -len(".md")]


def _superseded(row: Row) -> str | None:
    if isinstance(row, BugRow):
        return row.supersedes
    if isinstance(row, RetractionRow):
        return row.target_id
    return None


def _front_matter(row: Row, modified: str) -> list[str]:
    superseded = _superseded(row)
    return [
        "---",
        f"name: {_slug(row.id)}",
        f"description: {json.dumps(_description(row), ensure_ascii=False)}",
        f"valid_from: {row.date}",
        *([f"supersedes: {_memo_stem(superseded)}"] if superseded else []),
        f"recall_record: {record_json(to_record(row))}",
        "metadata:",
        "  node_type: memory",
        "  type: project",
        "  originSessionId: cca-bugs",
        f"  modified: {modified}",
        "---",
        "",
    ]


def _where(anchors: list[Anchor]) -> list[str]:
    return ["## Where", "",
            *[f"* `{a.file}` `{a.symbol}`" if a.symbol else f"* `{a.file}` (module level)"
              for a in anchors], ""]


def _bug_body(row: BugRow) -> list[str]:
    return [
        f"# {row.project} {row.finding_id}: a bug CCA fixed",
        "",
        "## The bug",
        "",
        row.summary,
        "",
        "## The fix",
        "",
        f"Commit `{row.fix_commit}` in `{row.repo}`, {row.date}. Files changed by that commit:",
        "",
        *[f"* `{f}`" for f in row.files],
        "",
        *(_where(row.anchors) if row.anchors else []),
        *(["## Caused by", "", *[f"* `{c}`" for c in row.caused_by], ""]
          if row.caused_by else []),
        "## The evidence",
        "",
        f"Tier: **{row.tier}**.",
        "",
        "| Condition | Value |",
        "|---|---|",
        *[f"| {c} | {_LABELS.get(v, v)} |"
          for c in CONDITIONS for v in [row.evidence.get(c, ABSENT)]],
        "",
        "Sources: " + ", ".join(f"`{s}`" for s in row.sources),
    ]


def _fp_body(row: FalsePositiveRow) -> list[str]:
    refs = ", ".join(f"`{r}`" for r in row.evidence_refs) or "none recorded"
    return [
        f"# {row.project} {row.finding_id}: a false positive CCA accepted",
        "",
        "## The claim",
        "",
        row.claim,
        "",
        "## Why it is not a defect",
        "",
        f"Evidence: {refs}",
        "",
        *_where(row.anchors),
        f"Run `{row.run_id}`, auditor `{row.auditor}`, {row.severity} {row.priority}, "
        f"verified by {row.verify_source}; accepted by the L6 architect.",
    ]


def _lesson_body(row: LessonRow) -> list[str]:
    return [
        f"# {row.project}: a lesson from {len(row.members)} bugs",
        "",
        "## The rule",
        "",
        row.rule,
        "",
        "## What to look for",
        "",
        row.pattern,
        "",
        *_where(row.anchors),
        "## The bugs it comes from",
        "",
        *[f"* `{m}`" for m in row.members],
        "",
        f"Accepted by {row.accepted_by} on {row.accepted_at}.",
    ]


def _retraction_body(row: RetractionRow) -> list[str]:
    return [
        f"# Retraction of {row.target_id}",
        "",
        row.reason,
        "",
        f"Retracted by {row.retracted_by} on {row.date}.",
    ]


def render_memo(row: Row, *, modified: str) -> str:
    """The memo for `row`: front matter carrying its record, then a body rendered from it."""
    if isinstance(row, BugRow):
        body = _bug_body(row)
    elif isinstance(row, FalsePositiveRow):
        body = _fp_body(row)
    elif isinstance(row, LessonRow):
        body = _lesson_body(row)
    else:
        body = _retraction_body(row)
    return "\n".join([*_front_matter(row, modified), *body, ""])


def parse_memo(text: str) -> Row:
    """The row a memo's `recall_record` holds. The body is never read.

    A memo written before records existed has none and is refused: it round-trips only after
    `bugs rerender --all` rewrites it.
    """
    text = text.replace("\r\n", "\n")
    if not text.startswith("---\n"):
        raise ValueError("no front matter")
    end = text.find("\n---\n", 3)
    if end < 0:
        raise ValueError("front matter is not closed")
    for line in text[4:end].split("\n"):
        if line.startswith("recall_record: "):
            return from_record(json.loads(line[len("recall_record: "):]))
    raise ValueError("no recall_record in the front matter")


def ledger_line(row: Row) -> dict:
    """What the ledger stores for `row`: a bug keeps its historical shape, others their record."""
    return row.to_json() if isinstance(row, BugRow) else to_record(row)


def row_from_ledger(line: dict) -> Row:
    """A row from one parsed ledger line; a line with no `kind` is a bug written before kinds."""
    if not isinstance(line, dict):
        raise ValueError(f"a ledger line is a JSON object, not {type(line).__name__}")
    kind = line.get("kind", "bug")
    if kind == "bug":
        return BugRow.from_json(line)
    return from_record(line)


@dataclass
class RerenderReport:
    rendered: int = 0
    # "line N: reason" for each ledger line that could not be read; its memo is untouched.
    skipped: list[str] = field(default_factory=list)
    # Memo files with no ledger row. Reported, never deleted.
    stray: list[str] = field(default_factory=list)


def read_ledger(root: Path) -> tuple[dict[str, Row], list[str]]:
    """Every row of the ledger under `root` by id, and "line N: reason" for each unreadable line.

    The first line of an id wins, as in `store.existing_ids`. Lines are split on LF only, so a
    line separator inside a value written before `one_line` existed cannot split a row. A bug
    row carries its effective caused_by (`store.effective_caused_by`), so `to_record`, its memo
    and the round-trip check all see a back-filled edge.
    """
    from .store import LEDGER, caused_by_annotations, with_effective_caused_by

    ledger = root / LEDGER
    annotations = caused_by_annotations(root)
    lines = ledger.read_text(encoding="utf-8").split("\n") if ledger.exists() else []
    rows: dict[str, Row] = {}
    skipped: list[str] = []
    for number, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            row = row_from_ledger(json.loads(line))
            row_id = row.id
        except (ValueError, KeyError, TypeError, AttributeError, RecursionError) as exc:
            skipped.append(f"line {number}: {type(exc).__name__}: {exc}")
            continue
        rows.setdefault(row_id, with_effective_caused_by(row, annotations))
    return rows, skipped


def rerender_all(root: Path, modified: str) -> RerenderReport:
    """Rewrite every memo under `root` from its ledger row. The ledger is only read.

    This is the one sanctioned overwrite of memos (`store.write` never overwrites one): the
    ledger is the system of record, and a memo is a rendering of it.
    """
    from .store import MEMOS

    rows, skipped = read_ledger(root)
    report = RerenderReport(skipped=skipped)
    memos = root / MEMOS
    memos.mkdir(parents=True, exist_ok=True)
    known: set[str] = set()
    for row in rows.values():
        # A line written before no_nul existed may hold U+0000; its memo, and so its name,
        # must not. roundtrip-check then reports that row changed rather than missing.
        clean = no_nul(row)
        target = memos / memo_filename(clean.id)
        known.add(target.name)
        tmp = target.with_name(f".tmp-{target.name}-{os.getpid()}")
        tmp.write_text(render_memo(clean, modified=modified), encoding="utf-8", newline="\n")
        os.replace(tmp, target)
        report.rendered += 1
    report.stray = sorted(p.name for p in memos.glob("*.md") if p.name not in known)
    return report
