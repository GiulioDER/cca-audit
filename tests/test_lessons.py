"""Session 5 of the audit-memory plan: lesson proposals (Task 5.1), acceptance, retraction and the
LESSONS block (Task 5.2).

The model transport is replaced in every test but one, which spends a real call and is skipped
unless `RECALL_RUN_PAID_TESTS=1`. Red proof (user rule, 2026-09-08) is in each test's docstring:
the mutation, the test run against it, the assertion that failed; each was restored.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from pathlib import Path

import pytest
from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.bugs import distill as ds
from cca_checks.bugs import envelope, lessons, prior
from cca_checks.bugs.__main__ import main
from cca_checks.bugs.anchors import Anchor
from cca_checks.bugs.model import EVIDENCED, BugRow
from cca_checks.bugs.records import LessonRow, from_record, read_ledger
from cca_checks.bugs.store import write

FIXTURES = Path(__file__).parent / "fixtures"
GOLDEN_DIR = FIXTURES / "audit_memory"
GOLDEN = json.loads((GOLDEN_DIR / "expected_records.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((GOLDEN_DIR / "recall_record.v1.json").read_text(encoding="utf-8"))
RESPONSES = FIXTURES / "audit_memory_responses"
REPO = "GiulioDER/RE-call"
LESSON = "RE-call__lesson__6a3ae35062b9.md"
LESSON_RETRACT = "RE-call__lesson__6a3ae35062b9~retract.md"
MODIFIED = "2026-10-21T00:00:00Z"


def _row(n: int, *, category="concurrency", file="pkg/pool.py", symbol="Pool._lease",
         repo="acme/widgets", caused_by=(), anchors=True, files=None, supersedes=None):
    sha = f"{n:040x}"
    return BugRow(project=repo.split("/")[1], repo=repo, finding_id=f"BUG-{n}", fix_commit=sha,
                  date=f"2026-09-{n:02d}", files=list(files or [file]),
                  summary=f"bug {n} at {file}", category=category, severity="P2",
                  evidence={"confirmed_l25": EVIDENCED, "fixed": EVIDENCED,
                            "l6_approved": EVIDENCED, "red_proof": EVIDENCED},
                  anchors=[Anchor(file, symbol, "a" * 64)] if anchors else [],
                  caused_by=list(caused_by), supersedes=supersedes)


def _ids(rows):
    return sorted(r.bug_id for r in rows)


class FakeClient:
    model = "fake-model-1"

    def __init__(self, answer=None):
        self.answer = answer or {"rule": "A connection's liveness is judged outside its lease.",
                                 "pattern": "A read of conn.closed after the pool took it back."}
        self.prompts: list[str] = []

    def complete(self, prompt, schema):
        self.prompts.append(prompt)
        return json.dumps(self.answer)


def _ledger(tmp_path, rows) -> Path:
    root = tmp_path / "bugs"
    write(rows, root, MODIFIED)
    return root


def _snapshot(root: Path) -> dict:
    return {p.relative_to(root).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob("*") if p.is_file() and "lessons" not in p.parts}


# Task 5.1: groups


def test_groups_of_fewer_than_three_are_dropped():
    """Invariant: a lesson needs three bugs at one place; two are not a pattern.

    Red 2026-10-22: mutation `if len(members) < MIN_MEMBERS` -> `if len(members) < 2` in
    `distill.candidate_groups`; failed at `assert ds.candidate_groups(two) == []`.
    """
    two = [_row(1), _row(2)]
    assert ds.candidate_groups(two) == []
    three = [*two, _row(3)]
    groups = ds.candidate_groups(three)
    assert [g.members for g in groups] == [_ids(three)]


def test_groups_are_by_repo_category_and_place():
    """Red 2026-10-22: mutation `(row.repo.lower(), row.category, file, symbol)` ->
    `(row.repo.lower(), "", file, symbol)` in `distill.candidate_groups` (the category no
    longer compared); failed at `assert ds.candidate_groups(rows) == []`.
    """
    rows = [_row(1), _row(2), _row(3, category="security"), _row(4, repo="acme/other")]
    assert ds.candidate_groups(rows) == []


def test_a_caused_by_chain_joins_a_group():
    """Invariant: a bug whose fix caused another, or was caused by one, joins that bug's group
    whatever its own category and place.

    Red 2026-10-22: mutation `members = _reach(base, edges)` -> `members = set(base)` in
    `distill.candidate_groups`; failed at `assert len(groups) == 1` (0: the pair alone is too
    small).
    """
    a, b = _row(1), _row(2)
    c = _row(3, category="perf", file="pkg/store.py", symbol="Store.put", caused_by=[a.bug_id])
    groups = ds.candidate_groups([a, b, c])
    assert len(groups) == 1
    assert groups[0].members == _ids([a, b, c]) and groups[0].via_caused_by == [c.bug_id]


def test_an_annotated_caused_by_edge_joins_a_group(tmp_path):
    """Invariant: distill reads the effective caused_by, so an edge back-filled into
    `caused_by.jsonl` (the row itself untouched) joins a group as a forward edge does.

    Red 2026-10-22: mutation `with_effective_caused_by(BugRow.from_json(value), annotations)` ->
    `BugRow.from_json(value)` in `store.bug_rows`, the reader distill uses; failed at
    `assert report.groups and ...` ([] : the pair alone is too small).
    """
    a, b = _row(1), _row(2)
    c = _row(3, category="perf", file="pkg/store.py", symbol="Store.put")
    root = _ledger(tmp_path, [a, b, c])
    (root / "caused_by.jsonl").write_text(
        json.dumps({"bug_id": c.bug_id, "caused_by": [a.bug_id]}) + "\n", encoding="utf-8")
    report = ds.distill(root)
    assert report.groups and report.groups[0]["via_caused_by"] == [c.bug_id]


def test_a_backfilled_row_that_touched_many_files_joins_no_group():
    """Invariant: a row with no anchors counts at its files only when its fix commit touched at
    most three, since a wider commit says nothing about which file was wrong.

    Red 2026-10-22: mutation `elif 1 <= len(row.files) <= UNANCHORED_FILES_MAX:` -> `elif
    row.files:` in `distill._places`; failed at `assert ds.candidate_groups(wide) == []`.
    """
    many = ["pkg/pool.py", "a.py", "b.py", "c.py"]
    wide = [_row(n, anchors=False, files=many) for n in (1, 2, 3)]
    assert ds.candidate_groups(wide) == []
    narrow = [_row(n, anchors=False, files=["pkg/pool.py", "a.py", "b.py"]) for n in (1, 2, 3)]
    assert [g.members for g in ds.candidate_groups(narrow)] == [_ids(narrow)]


def test_a_superseded_row_is_left_out():
    """Red 2026-10-22: mutation `if r.bug_id not in superseded` removed from `current` in
    `distill.candidate_groups`; failed at `assert ds.candidate_groups(rows) == []`.
    """
    old = _row(1)
    rows = [old, _row(2), _row(3, supersedes=old.bug_id)]
    assert ds.candidate_groups(rows) == []


def test_the_symbol_group_wins_over_its_file_with_the_same_members():
    """Invariant: of groups with the same members, only the most specific place is proposed.

    Red 2026-10-22: mutation `k[3] is None` -> `k[3] is not None` in the sort key of
    `distill.candidate_groups`; failed at `assert [(g.file, g.symbol) for g in groups] == ...`
    (the module-level group came first and the symbol group was dropped as a duplicate).
    """
    groups = ds.candidate_groups([_row(1), _row(2), _row(3)])
    assert [(g.file, g.symbol) for g in groups] == [("pkg/pool.py", "Pool._lease")]


# Task 5.1: drafts


def test_a_draft_stores_rule_pattern_members_and_the_model(tmp_path):
    """Invariant: a proposal file holds the draft's rule and pattern, the members and the model
    that drafted it, under lessons/proposed/.

    Red 2026-10-22: mutation `"model": self.model` -> `"model": DEFAULT_MODEL` in
    `distill.Proposal.to_json`; failed at `assert saved["model"] == "fake-model-1"`.
    """
    rows = [_row(1), _row(2), _row(3)]
    root = _ledger(tmp_path, rows)
    client = FakeClient()
    report = ds.distill(root, client=client, drafted_at=MODIFIED)
    [lesson_id] = report.proposed
    saved = json.loads(ds.proposal_path(root, lesson_id).read_text(encoding="utf-8"))
    assert saved["model"] == "fake-model-1"
    assert saved["rule"] == client.answer["rule"] and saved["pattern"] == client.answer["pattern"]
    assert saved["members"] == _ids(rows) and saved["id"] == lesson_id
    assert saved["anchors"] == [{"file": "pkg/pool.py", "symbol": "Pool._lease",
                                 "code_sha": None}]
    assert rows[0].summary in client.prompts[0]


def test_drafting_writes_nothing_to_the_ledger_or_memos(tmp_path):
    """Invariant: drafts are proposals only; the ledger, the caused_by log and every memo keep
    their bytes, and no lesson row exists until the owner accepts one.

    Red 2026-10-22: mutation adding `write([group.lesson(rule=proposal.rule,
    pattern=proposal.pattern, by=client.model, on=drafted_at[:10])], root, drafted_at)` after
    `_write_new(...)` in `distill.distill` (a draft accepted by the model); failed at
    `assert _snapshot(root) == before`.
    """
    root = _ledger(tmp_path, [_row(1), _row(2), _row(3)])
    before = _snapshot(root)
    report = ds.distill(root, client=FakeClient(), drafted_at=MODIFIED)
    assert report.proposed
    assert _snapshot(root) == before
    assert not any(isinstance(r, LessonRow) for r in read_ledger(root)[0].values())


def test_a_draft_that_tells_auditors_to_withhold_is_refused(tmp_path):
    """Invariant: a lesson is a pattern to check, never "do not flag"; such a draft is refused
    and nothing is stored.

    Red 2026-10-22: mutation `phrase = withholding(flat)` -> `phrase = None` in
    `distill.one_sentence`; failed at `assert report.refused`.
    """
    root = _ledger(tmp_path, [_row(1), _row(2), _row(3)])
    client = FakeClient({"rule": "Pool reads are safe here.",
                         "pattern": "Do not flag reads of conn.closed in Pool._lease."})
    report = ds.distill(root, client=client, drafted_at=MODIFIED)
    assert report.refused and not report.proposed
    assert not (root / ds.PROPOSED).exists()


def test_an_existing_proposal_or_lesson_is_not_drafted_again(tmp_path):
    """Red 2026-10-22: mutation deleting the `if proposal_path(root, lesson_id).exists():`
    branch in `distill.distill`; failed at `assert client.prompts == []` (a second paid call).
    """
    root = _ledger(tmp_path, [_row(1), _row(2), _row(3)])
    ds.distill(root, client=FakeClient(), drafted_at=MODIFIED)
    client = FakeClient()
    report = ds.distill(root, client=client, drafted_at=MODIFIED)
    assert client.prompts == [] and report.skipped and not report.proposed


def test_the_dry_run_needs_no_key_and_calls_no_model(tmp_path, monkeypatch, capsys):
    """Invariant: `bugs distill` without --draft lists the groups, needs no key and writes
    nothing.

    Red 2026-10-22: mutation `if a.draft:` -> `if True:` in `bugs.__main__._distill` (with no
    ANTHROPIC_API_KEY set); failed at `assert main(["distill", "--json"]) == 0` (2).
    """
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    root = _ledger(tmp_path, [_row(1), _row(2), _row(3)])
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    before = _snapshot(root)
    assert main(["distill", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert len(out["groups"]) == 1 and out["proposed"] == []
    assert _snapshot(root) == before and not (root / "lessons").exists()
    assert main(["distill", "--draft"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_the_request_carries_the_key_in_a_header_and_asks_for_json():
    """Invariant: the key travels in the `x-api-key` header only, and the request asks for
    output matching the two-field schema.

    Red 2026-10-22: mutation dropping `"format": {...}` from `output_config` in
    `distill.AnthropicClient.complete`; failed at `assert body["output_config"]["format"] ==
    ...` (KeyError is not raised: the test reads it with .get).
    """
    seen = {}

    def transport(url, headers, body):
        seen.update(url=url, headers=headers, body=body)
        return {"stop_reason": "end_turn",
                "content": [{"type": "text", "text": '{"rule": "r", "pattern": "p"}'}]}

    client = ds.AnthropicClient("sk-test-not-real", "claude-opus-5-5", transport)
    assert client.complete("hello", ds.DRAFT_SCHEMA) == '{"rule": "r", "pattern": "p"}'
    body = seen["body"]
    assert seen["headers"]["x-api-key"] == "sk-test-not-real"
    assert "sk-test-not-real" not in json.dumps(body)
    assert body["output_config"].get("format") == {"type": "json_schema",
                                                   "schema": ds.DRAFT_SCHEMA}
    assert body["model"] == "claude-opus-5-5" and seen["url"] == ds.MESSAGES_URL


def test_a_refused_model_answer_stores_no_proposal(tmp_path):
    """Invariant: an answer that did not end its turn (a refusal, a cut-off) is no draft.

    Red 2026-10-22: mutation `if stop != "end_turn":` -> `if stop is None:` in
    `distill.AnthropicClient.complete`; failed at `assert report.refused` (the refusal's text
    was read as a draft).
    """
    root = _ledger(tmp_path, [_row(1), _row(2), _row(3)])

    def transport(url, headers, body):
        return {"stop_reason": "refusal",
                "content": [{"type": "text", "text": '{"rule": "r r", "pattern": "p p"}'}]}

    client = ds.AnthropicClient("sk-test-not-real", "claude-opus-5-5", transport)
    report = ds.distill(root, client=client, drafted_at=MODIFIED)
    assert report.refused and not report.proposed


@pytest.mark.skipif(os.environ.get("RECALL_RUN_PAID_TESTS") != "1",
                    reason="spends one paid Anthropic Messages API call (about 2k input and "
                           "500 output tokens on the default model); set "
                           "RECALL_RUN_PAID_TESTS=1 and ANTHROPIC_API_KEY to run it")
def test_live_draft(tmp_path):
    """A live smoke check of the real transport and model, not a behaviour test: it has no red
    proof, by design, because a mutation run would spend a second paid call to show what the
    replaced-transport tests above already show."""
    root = _ledger(tmp_path, [_row(1), _row(2), _row(3)])
    report = ds.distill(root, client=ds.AnthropicClient.from_env(), drafted_at=MODIFIED)
    assert report.proposed, report.refused


# Task 5.2: acceptance and retraction


def _golden_rows():
    return [from_record(GOLDEN[name]) for name in (
        "RE-call__0123456789ab__BUG-001.md", "RE-call__abcdef012345__BUG-002.md",
        "RE-call__fedcba987654__P2-4.md")]


def _proposal(root: Path, **changes) -> str:
    rec = GOLDEN[LESSON]
    proposal = {"schema": 1, "id": rec["id"], "project": rec["project"], "repo": rec["repo"],
                "category": "concurrency", "anchors": rec["anchors"],
                "members": list(reversed(rec["members"])), "via_caused_by": [],
                "rule": rec["rule"], "pattern": rec["pattern"], "model": "fake-model-1",
                "drafted_at": "2026-10-20T00:00:00Z", **changes}
    path = ds.proposal_path(root, rec["id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(proposal), encoding="utf-8")
    return rec["id"]


def _valid(value, schema) -> bool:
    """The subset of JSON Schema `recall_record.v1.json` uses, enough to hold a record to C1."""
    if "const" in schema and value != schema["const"]:
        return False
    if "enum" in schema and value not in schema["enum"]:
        return False
    kinds = schema.get("type")
    if kinds is not None:
        names = kinds if isinstance(kinds, list) else [kinds]
        types = {"object": dict, "array": list, "string": str, "null": type(None),
                 "boolean": bool, "integer": int}
        if not any(isinstance(value, types[n]) for n in names):
            return False
    if isinstance(value, str):
        if len(value) < schema.get("minLength", 0):
            return False
        if "pattern" in schema and not re.search(schema["pattern"], value):
            return False
    if isinstance(value, list):
        if len(value) < schema.get("minItems", 0):
            return False
        if schema.get("uniqueItems") and len({json.dumps(v) for v in value}) != len(value):
            return False
        return all(_valid(v, schema.get("items", {})) for v in value)
    if isinstance(value, dict):
        props = schema.get("properties", {})
        if not set(schema.get("required", [])) <= set(value):
            return False
        if schema.get("additionalProperties") is False and not set(value) <= set(props):
            return False
        return all(_valid(v, props.get(k, {})) for k, v in value.items())
    return True


def _schema_valid(record: dict) -> bool:
    return any(_valid(record, branch) for branch in SCHEMA["oneOf"])


def test_the_schema_check_can_refuse():
    """Control for `_schema_valid`: a lesson with a pinned code sha or two members is refused."""
    rec = GOLDEN[LESSON]
    assert _schema_valid(rec)
    assert not _schema_valid({**rec, "members": rec["members"][:2]})
    assert not _schema_valid({**rec, "anchors": [{**rec["anchors"][0], "code_sha": "a" * 64}]})


def test_accept_writes_the_golden_lesson_memo_and_record(tmp_path):
    """Invariant: accepting a proposal writes a lesson row valid against C1 whose memo is the
    golden lesson memo byte for byte, and moves the proposal to lessons/accepted/.

    Red 2026-10-22: mutation `members = sorted(set(members))` -> `members =
    list(dict.fromkeys(members))` in `lessons.lesson_from` (the proposal lists its members in
    reverse); failed at `assert memo == golden`.
    """
    root = _ledger(tmp_path, _golden_rows())
    lesson_id = _proposal(root)
    done = lessons.accept(root, lesson_id, by="owner", on="2026-10-21", modified=MODIFIED)
    assert done.id == lesson_id and done.rows == 1 and done.memos == 1
    memo = (root / "memos" / LESSON).read_text(encoding="utf-8")
    golden = (GOLDEN_DIR / "memos" / LESSON).read_text(encoding="utf-8")
    assert memo == golden
    line = [json.loads(x) for x in (root / "bugs.jsonl").read_text("utf-8").splitlines()][-1]
    assert line == GOLDEN[LESSON] and _schema_valid(line)
    assert not ds.proposal_path(root, lesson_id).exists()
    assert ds.proposal_path(root, lesson_id, ds.ACCEPTED).exists()


def test_accept_through_the_cli(tmp_path, monkeypatch, capsys):
    """Red 2026-10-22: mutation `done = lessons.accept(...)` -> `done = lessons.Written(a.proposal,
    0, 0)` in `bugs.__main__._lesson`; failed at `assert skipped == [] and
    isinstance(lesson, LessonRow)` (None). A first version indexed `rows[lesson_id]` and failed
    by KeyError, not by its assertion; it now reads with .get."""
    root = _ledger(tmp_path, _golden_rows())
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    lesson_id = _proposal(root)
    assert main(["lesson", "accept", lesson_id, "--by", "owner"]) == 0
    rows, skipped = read_ledger(root)
    lesson = rows.get(lesson_id)
    assert skipped == [] and isinstance(lesson, LessonRow)
    assert lesson.accepted_by == "owner"


def test_accept_refuses_a_member_that_is_not_a_bug_of_the_repo(tmp_path, monkeypatch, capsys):
    """Red 2026-10-22: mutation deleting the `if not isinstance(row, BugRow) or ...: raise` lines
    in `lessons.lesson_from`; failed at `assert main([...]) == 1` (0)."""
    root = _ledger(tmp_path, _golden_rows())
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    members = [*GOLDEN[LESSON]["members"][:2], "RE-call:999999999999:BUG-9"]
    lesson_id = _proposal(root, members=members)
    assert main(["lesson", "accept", str(ds.proposal_path(root, lesson_id)),
                 "--by", "owner"]) == 1
    assert "is not a bug of" in capsys.readouterr().err
    assert not any(isinstance(r, LessonRow) for r in read_ledger(root)[0].values())


def test_accept_refuses_withholding_wording(tmp_path, monkeypatch, capsys):
    """Invariant: an owner's edit cannot turn a lesson into "do not flag".

    Red 2026-10-22: mutation `phrase = withholding(flat)` -> `phrase = None` in
    `distill.one_sentence`; failed at `assert main([...]) == 1` (0).
    """
    root = _ledger(tmp_path, _golden_rows())
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    lesson_id = _proposal(root, pattern="Reads of conn.closed here are a false positive.")
    assert main(["lesson", "accept", lesson_id, "--by", "owner"]) == 1


def test_accept_refuses_an_anchor_that_pins_a_code_version(tmp_path, monkeypatch, capsys):
    """Red 2026-10-22: mutation deleting the `if a["code_sha"] is not None: raise` lines in
    `lessons._anchors`; failed at `assert main([...]) == 1` (0)."""
    root = _ledger(tmp_path, _golden_rows())
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    anchors = [{"file": "recall/pool.py", "symbol": "Pool._lease", "code_sha": "a" * 64}]
    lesson_id = _proposal(root, anchors=anchors)
    assert main(["lesson", "accept", lesson_id, "--by", "owner"]) == 1


def test_accepting_the_same_lesson_twice_is_refused(tmp_path, monkeypatch, capsys):
    """Red 2026-10-22: mutation deleting `if lesson.id in rows: raise` in `lessons.accept`;
    failed at the second `assert main([...]) == 1` (0: the duplicate was silently skipped)."""
    root = _ledger(tmp_path, _golden_rows())
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    assert main(["lesson", "accept", _proposal(root), "--by", "owner"]) == 0
    assert main(["lesson", "accept", _proposal(root), "--by", "owner"]) == 1
    assert "already a lesson" in capsys.readouterr().err


def test_a_nul_in_a_proposal_never_reaches_the_ledger(tmp_path):
    """Invariant: U+0000 is replaced at row build, as for every other writer.

    Red 2026-10-22: mutation `lesson = no_nul(lesson_from(...))` -> `lesson = lesson_from(...)`
    in `lessons.accept` and `row = no_nul(row)` removed from `store.write`, together; failed at
    `assert "\\u0000" not in text`. The first alone stays green (run 2026-10-22): they are two
    defences for one bound, so only removing both is observable.
    """
    root = _ledger(tmp_path, _golden_rows())
    lesson_id = _proposal(root, rule="A connection's liveness\x00 is judged outside its lease.")
    lessons.accept(root, lesson_id, by="owner", on="2026-10-21", modified=MODIFIED)
    text = (root / "bugs.jsonl").read_text(encoding="utf-8")
    assert "\\u0000" not in text and "\x00" not in text and "�" in text


def test_retract_writes_the_golden_retraction(tmp_path):
    """Invariant: `lesson retract` appends a lesson_retraction whose memo is the golden one and
    supersedes the lesson's memo.

    Red 2026-10-22: mutation `kind="lesson_retraction"` -> `kind="false_positive_retraction"`
    in `lessons.retract`; failed at `assert memo == golden`.
    """
    root = _ledger(tmp_path, _golden_rows())
    lesson_id = _proposal(root)
    lessons.accept(root, lesson_id, by="owner", on="2026-10-21", modified=MODIFIED)
    done = lessons.retract(root, lesson_id, reason=GOLDEN[LESSON_RETRACT]["reason"],
                           by="owner", on="2026-10-23", modified=MODIFIED)
    assert done.rows == 1
    memo = (root / "memos" / LESSON_RETRACT).read_text(encoding="utf-8")
    golden = (GOLDEN_DIR / "memos" / LESSON_RETRACT).read_text(encoding="utf-8")
    assert memo == golden
    line = json.loads((root / "bugs.jsonl").read_text("utf-8").splitlines()[-1])
    assert line == GOLDEN[LESSON_RETRACT] and _schema_valid(line)


def test_retract_refuses_what_is_not_a_lesson(tmp_path, monkeypatch, capsys):
    """Red 2026-10-22: mutation `if not isinstance(target, LessonRow):` -> `if target is None:`
    in `lessons.retract`; failed at `assert main([...]) == 1` (0: a bug was retracted as a
    lesson)."""
    rows = _golden_rows()
    root = _ledger(tmp_path, rows)
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    assert main(["lesson", "retract", rows[0].bug_id, "--reason", "x", "--by", "owner"]) == 1


# Task 5.2: the LESSONS block


def _answer():
    return envelope.load(RESPONSES / "bug_answer.json")[0]


def _lesson_record(n: int, *, members=3, match="symbol_changed", date="2026-10-21", **rec):
    base = dict(GOLDEN[LESSON])
    base.update({"id": f"RE-call:lesson:{n:012x}", "date": date, "accepted_at": date,
                 "members": [f"RE-call:{n:012x}:BUG-{i}" for i in range(members)], **rec})
    return {"id": base["id"], "kind": "lesson", "recall_record": base,
            "superseded_by": None, "valid_until": None, "caused": [],
            "anchor_matches": [{"anchor": base["anchors"][0], "match": match}],
            "anchor_match": match}


def test_the_block_lists_a_current_lesson_with_its_member_citations():
    """Invariant: a lesson matched at the changed code is shown with its rule, its pattern to
    check and every member bug it comes from.

    Red 2026-10-22: mutation deleting the `f"  from {len(members)} bugs: ..."` line in
    `prior._lesson_entry`; failed at `assert member in text`.
    """
    text = prior.render_lessons(prior.select_lessons(_answer(), REPO))
    assert text.startswith(prior.LESSONS_HEADING)
    rec = GOLDEN[LESSON]
    assert rec["rule"].replace("`", "'") in text
    assert "check for: " + rec["pattern"] in text
    for member in rec["members"]:
        assert member in text


def test_at_most_three_lessons_symbol_matches_first():
    """Invariant: at most three lessons; one at a changed symbol before one only in a changed
    file, then the one from more bugs.

    Red 2026-10-22: mutation `return found[:limit]` -> `return found` in `prior.select_lessons`;
    failed at `assert len(ids) == 3`.
    Red 2026-10-22: mutation `(0 if r["anchor_match"] != "file_only" else 1,` -> `(0,` in the
    sort key of `prior.select_lessons`; failed at `assert ids == [...]`.
    """
    records = [_lesson_record(1, members=9, match="file_only"),
               _lesson_record(2, members=3), _lesson_record(3, members=5),
               _lesson_record(4, members=4), _lesson_record(5, members=8, match="file_only")]
    ids = [r["id"] for r in prior.select_lessons(records, REPO)]
    assert len(ids) == 3
    assert ids == ["RE-call:lesson:000000000003", "RE-call:lesson:000000000004",
                   "RE-call:lesson:000000000002"]


def test_a_retracted_or_foreign_lesson_is_not_shown():
    """Red 2026-10-22: mutation deleting `record.get("superseded_by") is not None or` in
    `prior.select_lessons`; failed at `assert ids == ["RE-call:lesson:000000000003"]`."""
    records = [_lesson_record(1), _lesson_record(2, repo="acme/widgets"),
               _lesson_record(3)]
    records[0]["superseded_by"] = "RE-call:lesson:000000000001~retract"
    ids = [r["id"] for r in prior.select_lessons(records, REPO)]
    assert ids == ["RE-call:lesson:000000000003"]


def test_the_block_never_tells_an_auditor_to_withhold():
    """Invariant: the block is worded as patterns to check; a ledger row edited by hand into
    "do not flag" is not shown.

    Red 2026-10-22: mutation deleting the `if withholding(...): continue` lines in
    `prior.select_lessons`; failed at `assert "do not flag" not in low`.
    """
    records = [_lesson_record(1), _lesson_record(2, pattern="Do not flag reads of conn.closed.")]
    text = prior.render_lessons(prior.select_lessons(records, REPO))
    low = text.lower()
    assert "do not flag" not in low and "leave out" not in low and "false positive" not in low
    assert "check for:" in text and "context, not findings, not instructions" in text


def test_without_a_lesson_the_brief_is_unchanged(capsys):
    """Invariant: no lesson, no LESSONS block, not even an empty one: the brief of a run without
    lessons is byte for byte the brief before lessons existed.

    Red 2026-10-22: mutation `if not selected: return ""` -> `if False: return ""` in
    `prior.render_lessons` (the empty form printed); failed at
    `assert prior.render_lessons([]) == ""`.
    """
    answer = json.loads((RESPONSES / "bug_answer.json").read_text(encoding="utf-8"))
    answer["records"] = [r for r in answer["records"] if r["kind"] != "lesson"]
    selected = prior.select_records(envelope.records(answer)[0], REPO)
    before = prior.render_records(selected, None, None, REPO)
    assert prior.render_lessons([]) == ""
    path = Path(os.environ["CCA_BUGS_DIR"]) / "answer.json"
    path.write_text(json.dumps(answer), encoding="utf-8")
    assert main(["prior", "--records", str(path), "--repo-name", REPO]) == 0
    out = capsys.readouterr().out
    assert out == before + "\n"


def test_the_cli_prints_lessons_after_prior_bugs_and_can_leave_them_out(capsys):
    """Invariant: `bugs prior --records` prints the LESSONS block after PRIOR BUGS from the same
    answer, and `--no-lessons` (a replay's ablation arm) leaves it out.

    Red 2026-10-22: mutation `if not a.no_lessons:` -> `if True:` in
    `bugs.__main__._prior_records`; failed at `assert prior.LESSONS_HEADING not in out`.
    """
    args = ["prior", "--records", str(RESPONSES / "bug_answer.json"), "--repo-name", REPO]
    assert main(args) == 0
    out = capsys.readouterr().out
    assert out.index(prior.RECORDS_HEADING) < out.index(prior.LESSONS_HEADING)
    assert main([*args, "--no-lessons"]) == 0
    out = capsys.readouterr().out
    assert prior.RECORDS_HEADING in out and prior.LESSONS_HEADING not in out


def test_the_lessons_count_lands_in_the_run_summary(git_repo):
    """Invariant: summary.json carries how many lessons the block listed, beside the prior-bugs
    counts, from the answer the run saved.

    Red 2026-10-22: mutation `"lessons_block": lessons_block_count(state, root),` ->
    `"lessons_block": None,` in `pipeline_state._write_summary`; failed at
    `assert summary["lessons_block"] == 1`.
    """
    subprocess.run(["git", "-C", str(git_repo), "remote", "add", "origin",
                    "https://github.com/GiulioDER/RE-call.git"], check=True)
    state = ps.start("FAST", root=git_repo)
    run_dir = git_repo / ".claude" / "audits" / "runs" / state.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prior-records.json").write_bytes((RESPONSES / "bug_answer.json").read_bytes())
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(git_repo),
              root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    ps.close(root=git_repo)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["lessons_block"] == 1
    assert summary["prior_block"] == {"exact": 3, "symbol_changed": 1, "file_only": 0}
