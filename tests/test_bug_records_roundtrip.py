"""Tests for cca_checks.bugs.records: one record format, and nothing lost CCA to RE-call to CCA.

The golden memos and the records RE-call must return for them are RE-call's contract (C4),
copied byte for byte. `render_memo` must reproduce every golden memo exactly, and a row must
survive row -> record -> row and row -> memo -> row unchanged, for every kind.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from cca_checks.bugs import records
from cca_checks.bugs.__main__ import main
from cca_checks.bugs.anchors import Anchor
from cca_checks.bugs.model import EVIDENCED, BugRow, memo_filename
from cca_checks.bugs.records import (
    FalsePositiveRow,
    LessonRow,
    RetractionRow,
    from_record,
    parse_memo,
    record_json,
    render_memo,
    to_record,
)

FIXTURES = Path(__file__).parent / "fixtures" / "audit_memory"
MEMOS = FIXTURES / "memos"
EXPECTED = json.loads((FIXTURES / "expected_records.json").read_text(encoding="utf-8"))
SCHEMA = json.loads((FIXTURES / "recall_record.v1.json").read_text(encoding="utf-8"))
MODIFIED = "2026-10-21T00:00:00Z"

#: sha256 of each contract file as `git show <RECALL_CONTRACT_COMMIT>:<path>` printed it in
#: RE-call on 2026-10-21, so an edit to a copy, or a re-pin without a re-copy, fails here.
PINNED = {
    "recall_record.v1.json": "b820181d015a3aec249d8e4952a62cb78eea8cddad1f6f44f1286d9d79dabc92",
    "anchor_vectors.json": "59ef286415d13654fd9c2220f983ce58e8b5e46dd8b93d3655b2d0a9e6cb6adb",
    "expected_records.json": "750961133631db9522eb4d489f1b568256b9de9208e974d6cefbb717fbc1bec5",
    "memos/RE-call__0123456789ab__BUG-001.md":
        "cd305b5f7588261ec7f1708da923d2850b8c49c5177d39583b9f301ca850d4fe",
    "memos/RE-call__111111111111__SEC-003.md":
        "6635834b885821a31959fc57623213c006dd4d9b1f0dc3ab2828f5a432c0b09a",
    "memos/RE-call__222222222222__PERF-001~2.md":
        "17edc1cd98efae49ab506b35223a0593041b771882dfa8df911bfab2ec57a388",
    "memos/RE-call__abcdef012345__BUG-002.md":
        "11b5cfc8ce916c0715e787ccfce0b3f9d133864d9d620d974c1beb0a00c936d3",
    "memos/RE-call__fedcba987654__P2-4.md":
        "4e07694fca30e627a65a1d55ac9487a1b177c4c3148f70234bfb66aedf4c9adc",
    "memos/RE-call__fp__run-20261021-9f8e7d__BUG-003.md":
        "b8172943aea2aca65772180eb673715799a9310d3d360dbee8f934dcc3353a86",
    "memos/RE-call__fp__run-20261021-9f8e7d__BUG-003~retract.md":
        "7ed655fa3cef52d8af6f39fe59b9efb766b92535fc127e641cca92a2f7122d75",
    "memos/RE-call__lesson__6a3ae35062b9.md":
        "d96ae456581e94205d1e0ac6e8bc9146b56cbb2299f11c42e04f2e461649e4ce",
    "memos/RE-call__lesson__6a3ae35062b9~retract.md":
        "d9d70f93eef9d58ecad7e0c477cca3a08bb7a8ca3a4a196f07543c1c547f9ecf",
}

SHA_A = "a" * 64
SHA_B = "b" * 64


def _bug(**over) -> BugRow:
    base = dict(
        project="widgets", repo="acme/widgets", finding_id="BUG-7",
        fix_commit="0123456789abcdef0123456789abcdef01234567", date="2026-09-01",
        files=["src/pool.py"], summary="the lease raced", severity="P2", auditor="bug-auditor",
        category="concurrency", run_id="run-1",
        evidence={"confirmed_l25": EVIDENCED, "fixed": EVIDENCED, "l6_approved": EVIDENCED,
                  "red_proof": EVIDENCED},
        sources=["git:0123456789abcdef0123456789abcdef01234567"],
        anchors=[Anchor("src/pool.py", "Pool._lease", SHA_A)],
    )
    base.update(over)
    return BugRow(**base)


def _hard_rows() -> list:
    """One row per kind, plus every hard case the round-trip contract names."""
    weird = ("Ünïcödé “quotes” 'single' \"double\": a key: value\n---\nnot a fence; "
             "line separator, paragraph separator, next\x85line, emoji ✅")
    return [
        _bug(),
        _bug(finding_id="BUG-8", ordinal=2, supersedes="widgets:0123456789ab:BUG-7",
             caused_by=["widgets:fedcba987654:P2-1", "widgets:111111111111:SEC-2"]),
        _bug(finding_id="SEC-9", summary=weird, severity=None, auditor=None, category=None,
             run_id=None, anchors=[Anchor("src/pool.py", None, SHA_B)]),
        _bug(finding_id="PERF-1", summary="x " * 6000, files=[],
             anchors=[Anchor(f"src/m{i}.py", f"C{i}.f", SHA_A) for i in range(20)]),
        _bug(finding_id="OLD-1", anchors=[], caused_by=[]),
        FalsePositiveRow(
            project="widgets", repo="acme/widgets", date="2026-10-21", run_id="run-2",
            finding_id="BUG-3", category="concurrency", severity="P2", priority="P2",
            high_stakes=False, auditor="bug-auditor", claim=weird, evidence_refs=["a.py:1"],
            verify_source="tool",
            anchors=[Anchor("src/pool.py", "Pool._lease", SHA_A), Anchor("a.py", None, SHA_B)]),
        LessonRow(project="widgets", repo="acme/widgets", date="2026-10-21", rule=weird,
                  pattern="a read after release", members=["c", "a", "b"],
                  anchors=[Anchor("src/pool.py", None, None)], accepted_by="owner",
                  accepted_at="2026-10-21"),
        RetractionRow(kind="false_positive_retraction", project="widgets",
                      repo="acme/widgets", date="2026-10-22",
                      target_id="widgets:fp:run-2:BUG-3", reason=weird, retracted_by="owner"),
        RetractionRow(kind="lesson_retraction", project="widgets", repo="acme/widgets",
                      date="2026-10-23", target_id="widgets:lesson:0123456789ab",
                      reason="gone", retracted_by="owner"),
    ]


def test_contract_copies_match_pinned_recall_commit():
    """Every copied contract file (schema, vectors, memos, records) is the pinned commit's bytes.

    The plan's `test_schema_copy_matches_pinned_recall_commit`, widened to every copied file.

    Red 2026-10-21: mutation adding one space after the `title` entry of the copied
    `recall_record.v1.json`; failed at `assert got == PINNED`, the schema's digest differing
    (af2545e6... != b820181d...).
    """
    assert records.RECALL_CONTRACT_COMMIT == "c526ced82c08ff59da3ce15599cde90aa5d7b0eb"
    got = {p.relative_to(FIXTURES).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
           for p in FIXTURES.rglob("*") if p.is_file()}
    assert got == PINNED


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_render_matches_golden(name):
    """`render_memo` reproduces RE-call's golden memo byte for byte.

    Red 2026-10-21, node `[RE-call__111111111111__SEC-003.md]`: mutation `ensure_ascii=True`
    in `record_json`; failed at `assert rendered == golden` (the summary's Unicode escaped).
    """
    row = from_record(EXPECTED[name])
    assert memo_filename(row.id) == name
    golden = (MEMOS / name).read_bytes().decode("utf-8")
    rendered = render_memo(row, modified=MODIFIED)
    assert rendered == golden


@pytest.mark.parametrize("name", sorted(EXPECTED))
def test_golden_record_round_trips(name):
    """record -> row -> record is the identity on every golden record, and parse_memo reads it.

    Red 2026-10-21: the `ordinal` mutation of `test_row_record_row_identity`; failed at
    `assert to_record(row) == record` ("Right contains 1 more item: {'ordinal': 2}").
    """
    record = EXPECTED[name]
    row = from_record(record)
    assert to_record(row) == record
    assert parse_memo((MEMOS / name).read_text(encoding="utf-8")) == row


@pytest.mark.parametrize("row", _hard_rows(), ids=lambda r: f"{r.kind}:{r.id}")
def test_row_record_row_identity(row):
    """row -> record -> row and row -> memo -> row are the identity, for every kind and hard case.

    The record line passes through canonical JSON, so the comparison is on what RE-call gets.

    Red 2026-10-21: mutation in `to_record`, `ordinal` omitted from the bug record (the plan's
    mutation); failed at `assert from_record(json.loads(line)) == row`, where from_record's
    strict field check refused the record: "ValueError: bug record: missing ['ordinal']".
    """
    line = record_json(to_record(row))
    assert "\n" not in line and " " not in line and "\x85" not in line
    assert from_record(json.loads(line)) == row
    memo = render_memo(row, modified=MODIFIED)
    assert parse_memo(memo) == row
    head = memo.split("\n---\n", 1)[0]
    assert head.splitlines() == head.split("\n")  # no reader can split the front matter apart


def test_record_fields_are_the_schema_fields():
    """Each kind's record holds exactly the fields the schema requires, and no other.

    Red 2026-10-21: mutation adding `"tags": []` to the false-positive record; failed at
    `assert set(record) == required[...]` for kind false_positive.
    """
    required = {}
    for branch in SCHEMA["oneOf"]:
        kinds = branch["properties"]["kind"]
        for kind in kinds.get("enum", [kinds.get("const")]):
            required[kind] = set(branch["required"])
            assert set(branch["properties"]) == set(branch["required"])
    for row in _hard_rows():
        record = to_record(row)
        assert set(record) == required[record["kind"]], record["kind"]


def test_supersedes_is_written():
    """A row that supersedes another names it in the front matter and in the record.

    Red 2026-10-21: mutation dropping the `supersedes:` front matter line from `_front_matter`;
    failed at `assert "\\nsupersedes: widgets__0123456789ab__BUG-7\\n" in memo`.
    """
    row = _bug(finding_id="BUG-8", supersedes="widgets:0123456789ab:BUG-7")
    memo = render_memo(row, modified=MODIFIED)
    head = memo.split("\n---\n", 1)[0]
    assert "\nsupersedes: widgets__0123456789ab__BUG-7\n" in memo
    assert head.index("valid_from:") < head.index("supersedes:") < head.index("recall_record:")
    assert json.loads(head.split("recall_record: ", 1)[1].split("\n", 1)[0])["supersedes"] \
        == "widgets:0123456789ab:BUG-7"
    retraction = _hard_rows()[7]
    assert "\nsupersedes: widgets__fp__run-2__BUG-3\n" in render_memo(retraction,
                                                                     modified=MODIFIED)
    assert "\nsupersedes:" not in render_memo(_bug(), modified=MODIFIED)


def test_from_record_refuses_what_it_cannot_rebuild_exactly():
    """An unknown field, a missing field, another version or a forged id is refused, never read.

    Red 2026-10-21: mutation skipping the field-set check in `_fields`; failed at
    `with pytest.raises(ValueError)` for the record with an extra field (DID NOT RAISE).
    """
    good = EXPECTED["RE-call__fp__run-20261021-9f8e7d__BUG-003.md"]
    for bad in ({**good, "extra": 1},
                {k: v for k, v in good.items() if k != "claim"},
                {**good, "v": 2},
                {**good, "id": "RE-call:fp:other:BUG-003"},
                {**good, "kind": "nonsense"}):
        with pytest.raises(ValueError):
            from_record(bad)
    bug = EXPECTED["RE-call__0123456789ab__BUG-001.md"]
    with pytest.raises(ValueError):
        from_record({**bug, "tier": "historical"})


def test_legacy_ledger_line_reads_with_no_anchors():
    """A ledger line written before this change (no anchors, caused_by or kind) still reads.

    Red 2026-10-21: mutation `for a in row["anchors"]` in `BugRow.from_json` (no default);
    the call raised KeyError: 'anchors', the defect itself: every row written before this change unreadable.
    """
    row = _bug(anchors=[], caused_by=[])
    line = row.to_json()
    for key in ("anchors", "caused_by", "kind"):
        line.pop(key)
    assert records.row_from_ledger(line) == row


def test_ledger_lines_survive_splitlines(tmp_path):
    """A row whose text holds U+2028, U+2029 or NEL stays one ledger line for every reader.

    `existing_ids`, `prior.load_rows` and `resolve` all split the ledger with `splitlines()`,
    which breaks a line at those characters; `json.dumps(ensure_ascii=False)` leaves them raw.

    Red 2026-10-21: mutation removing `one_line(...)` around the ledger line in `store.write`;
    failed at `assert existing_ids(root / "bugs.jsonl") == {r.id for r in rows}` (the weird
    rows' ids missing).
    """
    from cca_checks.bugs.prior import load_rows
    from cca_checks.bugs.store import existing_ids, write

    root = tmp_path / "bugs"
    rows = _hard_rows()
    assert write(rows, root, MODIFIED) == (len(rows), len(rows))
    assert existing_ids(root / "bugs.jsonl") == {r.id for r in rows}
    assert len(load_rows(root / "bugs.jsonl")) == len(rows)
    lines = (root / "bugs.jsonl").read_text(encoding="utf-8").splitlines()
    assert [records.row_from_ledger(json.loads(x)) for x in lines] == rows


def _ledger(root: Path, rows, extra: list[str] = ()) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    lines = [records.one_line(json.dumps(records.ledger_line(r), ensure_ascii=False,
                                         sort_keys=True)) for r in rows]
    legacy = _bug(finding_id="OLD-2", anchors=[], caused_by=[]).to_json()
    for key in ("anchors", "caused_by", "kind"):
        legacy.pop(key)
    lines.append(json.dumps(legacy, sort_keys=True))
    path = root / "bugs.jsonl"
    path.write_text("".join(f"{x}\n" for x in [*lines, *extra]), encoding="utf-8",
                    newline="\n")
    return path


def test_rerender_rewrites_every_memo_from_the_ledger(tmp_path, monkeypatch):
    """`bugs rerender --all` rewrites every memo from its ledger row and changes no row.

    The ledger is the system of record: a stale memo, a hand-edited memo and a missing memo all
    end up equal to `render_memo(row)`, and the ledger's bytes are unchanged.

    Red 2026-10-21: mutation in `rerender_all`, `if not target.exists():` guarding the write
    (the append-only rule of `store.write`); failed at `assert memo.read_text(...) == want`
    for the stale memo.
    """
    root = tmp_path / "bugs"
    rows = _hard_rows()
    ledger = _ledger(root, rows, extra=["{not json", ""])
    before = ledger.read_bytes()
    memos = root / "memos"
    memos.mkdir()
    (memos / memo_filename(rows[0].id)).write_text("stale\n", encoding="utf-8")
    (memos / memo_filename(rows[5].id)).write_text("hand note\n", encoding="utf-8")
    (memos / "unrelated.md").write_text("keep me\n", encoding="utf-8")
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))

    code = main(["rerender", "--all", "--modified", MODIFIED])

    assert ledger.read_bytes() == before
    assert code == 1  # the malformed line is reported, and it makes the exit non-zero
    every = [*rows, records.row_from_ledger(json.loads(before.decode().splitlines()[len(rows)]))]
    for row in every:
        memo = memos / memo_filename(row.id)
        want = render_memo(row, modified=MODIFIED)
        assert memo.read_text(encoding="utf-8") == want
    assert (memos / "unrelated.md").read_text(encoding="utf-8") == "keep me\n"
    assert sorted(p.name for p in memos.iterdir()) == sorted(
        [memo_filename(r.id) for r in every] + ["unrelated.md"])


def test_rerender_requires_all(tmp_path, monkeypatch):
    """`--all` is required: rewriting memos is never a side effect of a bare command.

    Red 2026-10-21: mutation `required=False` on `--all`; failed at `pytest.raises(SystemExit)`
    ("DID NOT RAISE SystemExit").
    """
    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path))
    with pytest.raises(SystemExit) as exc:
        main(["rerender"])
    assert exc.value.code == 2
