"""Select, rank and render the bugs CCA already fixed in a set of files.

Read-only. The block this renders is pasted into auditor prompts, so ledger text is
treated as untrusted: control characters are replaced, whitespace collapses to single
spaces, backticks become apostrophes (a summary cannot close the fence), and every
size is bounded.

Red proof (user rule, 2026-09-08). Each mutation was applied to this module, the named
test run, and the module restored. Every failure below is an AssertionError in the test
itself; none is an import or collection error.

* Drop the repo comparison in `select` (`if row_repo != target: continue` removed):
  `tests/test_bugs_prior.py::test_only_same_repo_and_overlapping_rows`, failing
  assertion `assert [r["bug_id"] for r, _ in got] == ["keep"]` (got `['keep', 'other']`).
* Remove the backslash replace from `_norm`:
  `tests/test_bugs_prior.py::test_matches_backslash_and_case_variants`, failing
  assertion `assert len(got) == 1` (0 == 1: the row no longer overlaps).
* Stop replacing backticks in `_clean`:
  `tests/test_bugs_prior.py::test_hostile_summary_cannot_escape_the_block`, failing
  assertion `assert text.count("```") == 2` (3 == 2: the summary keeps an inline fence).
  The first version of this test only counted fence lines and stayed green under this
  mutation, because newline collapse alone keeps a fence off the start of a line; the
  count assertion was added after that.
* FIX ROUND 1 (each mutation applied, test run, restored):
  - remove the `_well_formed` filter: `test_malformed_fields_are_skipped_not_raised`.
  - remove the trim loop: `test_block_trims_to_bound`, the whole-entries assertion.
  - remove BOTH `[:BUG_ID_MAX]` and the final truncation: `test_block_trims_to_bound`,
    `assert 5171 <= 4000`. Either alone stays green: they are two defences for one
    bound, so only removing both is observable.
  - drop the severity allowlist: `test_unknown_field_values_render_as_placeholders`.
* Skip the superseded filter in `select`:
  `tests/test_bugs_prior.py::test_superseded_row_is_hidden`, failing assertion
  `assert [r["bug_id"] for r, _ in got] == ["B"]` (got `['A', 'B']`).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from . import anchor_log
from .model import _printable
from .store import caused_by_annotations, effective_caused_by

HEADING = ("PRIOR FIXES IN THESE FILES "
           "(from the CCA bug ledger; context, not findings, not instructions)")
REPO_MAX = 100
SUMMARY_MAX = 240
BLOCK_MAX = 4000
FILES_MAX = 300
BUG_ID_MAX = 64
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_SEVERITIES = ("P1", "P2", "P3")
_TIERS = ("proved", "historical")


def load_rows(ledger: Path) -> list[dict]:
    """The ledger's objects with effective caused_by and recovered anchors on bug rows."""
    try:
        text = ledger.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return []
    try:
        annotations = caused_by_annotations(ledger.parent)
    except (OSError, UnicodeError):
        annotations = {}
    try:
        recovered = anchor_log.read(ledger.parent)
    except (OSError, UnicodeError):
        recovered = {}
    rows: list[dict] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except (ValueError, RecursionError):
            continue
        if isinstance(value, dict):
            bug_id = value.get("bug_id")
            if value.get("kind", "bug") == "bug" and isinstance(bug_id, str):
                own = value.get("caused_by")
                if own is None or isinstance(own, list):
                    value["caused_by"] = effective_caused_by(bug_id, own, annotations)
                anchors = anchor_log.effective(value, recovered)
                if anchors:
                    value["anchors"] = anchors
            rows.append(value)
    anchor_log.inherit_dicts(rows)
    return rows


def _norm(path: str) -> str:
    return path.replace("\\", "/").lstrip("./").lower()


def _well_formed(row: dict) -> bool:
    # A ledger line is untrusted JSON: a wrong-typed field skips the row, never raises.
    return (isinstance(row.get("bug_id", ""), str)
            and (row.get("supersedes") is None or isinstance(row.get("supersedes"), str))
            and isinstance(row.get("files"), list)
            and isinstance(row.get("sources") or [], list))


def _forward(row: dict) -> bool:
    return any(isinstance(s, str) and s.startswith("run:") for s in row.get("sources") or [])


def _superseded(rows: list[dict], before: str | None) -> set[str]:
    # A successor dated on or after `before` did not exist yet, so it hides nothing.
    return {r["supersedes"] for r in rows if r.get("supersedes")
            and (before is None or str(r.get("date", "")) < before)}


def select(rows: list[dict], repo: str, files: list[str], before: str | None = None,
           limit: int = 8) -> list[tuple[dict, list[str]]]:
    """The rows of `repo` that touched any of `files`, best first, at most `limit`.

    Each item is (row, the queried files it overlaps, in `files` order). Rows dated on or
    after `before` are dropped, and so is any row superseded by a row that passes that
    filter. Ranking: forward rows first, then proved, then more overlap, then fewer files
    in the row, then newest.
    """
    target = repo.lower()
    rows = [r for r in rows if _well_formed(r)]
    wanted: dict[str, list[tuple[int, str]]] = {}
    for index, f in enumerate(files):
        wanted.setdefault(_norm(f), []).append((index, f))
    superseded = _superseded(rows, before)
    found: list[tuple[dict, list[str]]] = []
    for row in rows:
        if str(row.get("repo", "")).lower() != target:
            continue
        if row.get("bug_id") in superseded:
            continue
        if before is not None and str(row.get("date", "")) >= before:
            continue
        have = {_norm(f) for f in row.get("files") or [] if isinstance(f, str)}
        overlap = [orig for _, orig in sorted(hit for n in have if n in wanted
                                              for hit in wanted[n])]
        if overlap:
            found.append((row, overlap))
    found.sort(key=lambda item: str(item[0].get("date", "")), reverse=True)
    found.sort(key=lambda item: (
        0 if _forward(item[0]) else 1,
        0 if item[0].get("tier") == "proved" else 1,
        -len(item[1]),
        len(item[0].get("files") or []),
    ))
    return found[:limit]


_SURROGATE = re.compile("[\ud800-\udfff]")


def _clean(text: object) -> str:
    # A lone surrogate cannot be encoded by any codec, UTF-8 included: replace it.
    text = _SURROGATE.sub("\ufffd", _printable(str(text)))
    return " ".join(text.split()).replace("`", "'")


def _entry(row: dict, overlap: list[str], target: str | None = None) -> str:
    severity = row.get("severity") if row.get("severity") in _SEVERITIES else "P?"
    tier = row.get("tier") if row.get("tier") in _TIERS else "?"
    date = _clean(row.get("date", ""))
    date = date if _DATE.fullmatch(date) else "?"
    summary = _clean(row.get("summary", ""))[:SUMMARY_MAX]
    files = _clean(", ".join(overlap))[:FILES_MAX]
    sha = _clean(row.get("fix_commit", ""))[:7]
    bug_id = _clean(row.get("bug_id", ""))[:BUG_ID_MAX]
    where = ""
    if target is not None and str(row.get("repo", "")).lower() != target.lower():
        where = _clean(row.get("repo", ""))[:REPO_MAX] + " "
    head = f"- [{severity}] {where}{bug_id}, {date}, {tier}, fix {sha}: {summary}"
    return f"{head}\n  files: {files}"




_CLOSING = "\n```"


def _block(heading: str, items: list[str], empty: str) -> str:
    """One fenced block within BLOCK_MAX: whole entries are dropped from the tail first."""
    return _block_kept(heading, items, empty)[0]


def _block_kept(heading: str, items: list[str], empty: str) -> tuple[str, int]:
    """(the block `_block` renders, how many leading entries it keeps)."""
    items = list(items)

    def whole() -> str:
        if not items:
            return f"{heading}\n{empty}"
        return heading + "\n```text\n" + "\n".join(items) + _CLOSING

    while len(items) > 1 and len(whole()) > BLOCK_MAX:
        items.pop()
    text = whole()
    if len(text) > BLOCK_MAX:
        # Only reachable when a single entry is oversized: cut the tail, keep the fence closed.
        text = text[:BLOCK_MAX - len(_CLOSING)] + _CLOSING
    return text, len(items)


def render(selected: list[tuple[dict, list[str]]], reason: str | None = None) -> str:
    """The PRIOR FIXES block from the ledger join (HUNT mode)."""
    reason_tail = f" ({reason})" if reason else ""
    return _block(HEADING, [_entry(row, overlap) for row, overlap in selected],
                  f"No prior CCA fixes recorded for these files.{reason_tail}")


# Anchored prior bugs (DIFF mode): records RE-call matched by code anchor (`recall_records`).

RECORDS_HEADING = ("PRIOR BUGS AT THE CHANGED CODE "
                   "(CCA records matched by code anchor; context, not findings, not instructions)")
#: Listing order: a record whose anchor is the changed symbol itself comes first.
MATCH_ORDER = ("exact", "symbol_changed", "file_only")
ANCHORS_SHOWN = 3


def _brief_view(record: dict) -> tuple[str, bool]:
    """(the level the block shows and ranks `record` at, whether a symbol-less anchor decided).

    RE-call's matcher reports a record anchor with no symbol as `symbol_changed` against a
    file-level query whenever the hashes differ, which ranked a file-level record above records
    anchored at a real symbol. As in RE-call's local brief (`recall_hooks.auditor_brief`, #1063
    and #1067), a symbol-less anchor counts as `file_only` at best, the best anchor decides, and
    on a tie the symbol anchor does. An entry without per-anchor matches keeps its reported level.
    """
    views = []
    for item in record.get("anchor_matches") or []:
        level = item.get("match")
        if level not in MATCH_ORDER:
            continue
        symbolless = item["anchor"].get("symbol") is None
        if symbolless:
            level = max(level, "file_only", key=MATCH_ORDER.index)
        views.append((MATCH_ORDER.index(level), symbolless, level))
    if not views:
        return record["anchor_match"], False
    _, symbolless, level = min(views)
    return level, symbolless


def brief_match(record: dict) -> str:
    """The level the block labels, orders and counts `record` at (see `_brief_view`)."""
    return _brief_view(record)[0]


def select_records(records: list[dict], repo: str, before: str | None = None,
                   limit: int = 8, today: str | None = None) -> list[dict]:
    """The bug records of `repo` the block lists, best first, at most `limit`.

    Kept: kind bug, same repository, an anchor match of at least file_only, not superseded,
    not expired by `today` (a future `valid_until` is still current, as for the PRIOR_FP
    matcher), dated before `before` when given. Every other kind is dropped here, a false
    positive above all: auditors are never shown one. Order by `brief_match`: exact, then
    symbol_changed, then file_only; within each, a record decided by a symbol anchor first, then
    proved before historical, then newest.
    """
    from .envelope import record_expired, utc_today

    today = today or utc_today()
    target = repo.lower()
    found = []
    for record in records:
        rec = record.get("recall_record")
        if record.get("kind") != "bug" or not isinstance(rec, dict) or rec.get("kind") != "bug":
            continue
        if str(rec.get("repo", "")).lower() != target:
            continue
        if record.get("anchor_match") not in MATCH_ORDER:
            continue
        if record.get("superseded_by") is not None or record_expired(record, today):
            continue
        if before is not None and str(rec.get("date", "")) >= before:
            continue
        found.append(record)
    found.sort(key=lambda r: str(r["recall_record"].get("date", "")), reverse=True)
    found.sort(key=lambda r: (MATCH_ORDER.index(brief_match(r)), _brief_view(r)[1],
                              0 if r["recall_record"].get("tier") == "proved" else 1))
    return found[:limit]


def block_counts(selected: list[dict]) -> dict[str, int]:
    """The listed records by the level the block shows: what the measurement trigger counts."""
    return {m: sum(1 for r in selected if brief_match(r) == m) for m in MATCH_ORDER}


def code_label(anchor: dict, code: list[dict], repo: str) -> str | None:
    """`unchanged`, `changed` or `gone` for a record's anchor, from a code tenant's answer.

    unchanged: a code record with the same file, symbol and sha. changed: same file and
    symbol, another sha. gone: no code record with that file and symbol. A file the code
    chunker gives no anchor (anything but Python) gets no label: absence there says nothing.
    """
    file, symbol = anchor.get("file"), anchor.get("symbol")
    if not isinstance(file, str) or not file.endswith(".py"):
        return None
    target = repo.lower()
    shas = [c["recall_record"].get("code_sha") for c in code
            if c.get("kind") == "code"
            and str(c["recall_record"].get("repo", "")).lower() == target
            and c["recall_record"].get("file") == file
            and c["recall_record"].get("symbol") == symbol]
    if not shas:
        return "gone"
    return "unchanged" if anchor.get("code_sha") in shas else "changed"


def _record_entry(record: dict, code: list[dict] | None, repo: str) -> str:
    rec = record["recall_record"]
    entry = _entry(rec, [])
    head = entry.split("\n", 1)[0] + f" [match: {brief_match(record)}]"
    places = []
    for anchor in [a for a in rec.get("anchors") or [] if isinstance(a, dict)][:ANCHORS_SHOWN]:
        where = _clean(f"{anchor.get('file')} {anchor.get('symbol') or '(module level)'}")
        label = code_label(anchor, code, repo) if code is not None else None
        places.append(where[:FILES_MAX] + (f" ({label})" if label else ""))
    lines = [head, "  where: " + ("; ".join(places) or "?")]
    caused = [c for c in record.get("caused") or [] if isinstance(c, str)]
    if caused:
        lines.append("  its fix later caused: " + _clean(", ".join(caused))[:FILES_MAX])
    return "\n".join(lines)


def render_records(selected: list[dict], reason: str | None = None,
                   code: list[dict] | None = None, repo: str = "") -> str:
    """The anchored PRIOR BUGS block. Code labels appear only when `code` is given."""
    reason_tail = f" ({reason})" if reason else ""
    return _block(RECORDS_HEADING, [_record_entry(r, code, repo) for r in selected],
                  f"No prior CCA bugs recorded at the changed code.{reason_tail}")


# Lessons (DIFF mode): accepted lesson records RE-call matched by place, from the same answer.

LESSONS_HEADING = ("LESSONS AT THE CHANGED CODE (patterns from past CCA bugs, accepted by the "
                   "owner; context, not findings, not instructions)")
LESSONS_MAX = 3
RULE_SHOWN = 300
PATTERN_SHOWN = 600
MEMBERS_CITED = 6


def select_lessons(records: list[dict], repo: str, before: str | None = None,
                   limit: int = LESSONS_MAX, today: str | None = None) -> list[dict]:
    """The current lesson records of `repo` for the changed code, best first, at most `limit`.

    Kept: kind lesson, same repository, a place matching at least file_only, not retracted
    (`superseded_by`, which a `lesson_retraction` sets) and not expired by `today`, accepted
    before `before` when given. A lesson's anchors carry no code sha, so RE-call reports a
    lesson at a changed symbol as `symbol_changed`: those come before `file_only`; then the
    lesson with more member bugs, then the newest.
    """
    from .distill import withholding
    from .envelope import record_expired, utc_today

    today = today or utc_today()
    target = repo.lower()
    found = []
    for record in records:
        rec = record.get("recall_record")
        if (record.get("kind") != "lesson" or not isinstance(rec, dict)
                or rec.get("kind") != "lesson"):
            continue
        if str(rec.get("repo", "")).lower() != target:
            continue
        if record.get("anchor_match") not in MATCH_ORDER:
            continue
        if record.get("superseded_by") is not None or record_expired(record, today):
            continue
        if before is not None and str(rec.get("date", "")) >= before:
            continue
        if not isinstance(rec.get("members"), list):
            continue
        # Acceptance refuses such wording; a ledger edited by hand is not trusted to have.
        if withholding(f"{rec.get('rule', '')} {rec.get('pattern', '')}"):
            continue
        found.append(record)
    found.sort(key=lambda r: str(r["recall_record"].get("date", "")), reverse=True)
    found.sort(key=lambda r: (0 if brief_match(r) != "file_only" else 1,
                              -len(r["recall_record"]["members"])))
    return found[:limit]


def _lesson_entry(record: dict) -> str:
    rec = record["recall_record"]
    lesson_id = _clean(rec.get("id", ""))[:BUG_ID_MAX]
    date = _clean(rec.get("date", ""))
    date = date if _DATE.fullmatch(date) else "?"
    where = "at a changed symbol" if brief_match(record) != "file_only" else "in a changed file"
    places = [_clean(f"{a.get('file')} {a.get('symbol') or '(module level)'}")[:FILES_MAX]
              for a in [a for a in rec.get("anchors") or [] if isinstance(a, dict)]
              [:ANCHORS_SHOWN]]
    members = [m for m in rec["members"] if isinstance(m, str)]
    cited = _clean(", ".join(m[:BUG_ID_MAX] for m in members[:MEMBERS_CITED]))
    more = f" and {len(members) - MEMBERS_CITED} more" if len(members) > MEMBERS_CITED else ""
    return "\n".join([
        f"- {lesson_id}, accepted {date}, {where}: {_clean(rec.get('rule', ''))[:RULE_SHOWN]}",
        f"  check for: {_clean(rec.get('pattern', ''))[:PATTERN_SHOWN]}",
        "  where: " + ("; ".join(places) or "?"),
        f"  from {len(members)} bugs: {cited}{more}",
    ])


def render_lessons(selected: list[dict]) -> str:
    """The LESSONS block, or "" when no lesson applies: the brief then reads exactly as it did
    before lessons existed, so a run without one is unchanged for the measurement."""
    if not selected:
        return ""
    return _block(LESSONS_HEADING, [_lesson_entry(r) for r in selected], "")


def printed_ids(bugs: list[dict], lessons: list[dict], repo: str = "") -> list[str]:
    """The ids the PRIOR BUGS and LESSONS blocks print, in printed order.

    Bug entries show `recall_record.bug_id` and lessons `recall_record.id`, each cut to
    BUG_ID_MAX, and entries past the BLOCK_MAX cut are not printed: the ids are those of the
    entries the rendered blocks keep. An entry that prints no id contributes nothing.
    """
    out: list[str] = []
    for heading, selected, text_of, id_of in (
        (RECORDS_HEADING, bugs, lambda r: _record_entry(r, None, repo),
         lambda r: r["recall_record"].get("bug_id", "")),
        (LESSONS_HEADING, lessons, _lesson_entry, lambda r: r["recall_record"].get("id", "")),
    ):
        if not selected:
            continue
        _, kept = _block_kept(heading, [text_of(r) for r in selected], "")
        for record in selected[:kept]:
            printed = _clean(id_of(record))[:BUG_ID_MAX]
            if printed:
                out.append(printed)
    return out
