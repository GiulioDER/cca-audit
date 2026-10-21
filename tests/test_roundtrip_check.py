"""Tests for `bugs roundtrip-check`: the ledger against RE-call's dump, nothing lost or added.

The dump is built in the C5 envelope `recall records --all --json` prints, from the same rows
the ledger holds, then damaged one way per test.
"""
from __future__ import annotations

import copy
import json

import pytest
from test_bug_records_roundtrip import _hard_rows

from cca_checks.bugs.__main__ import main
from cca_checks.bugs.records import to_record
from cca_checks.bugs.store import write

STAMP = "2026-10-21T00:00:00Z"


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    root = tmp_path / "bugs"
    rows = _hard_rows()
    write(rows, root, STAMP)
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    return rows


def _entry(record: dict) -> dict:
    return {"id": record["id"], "kind": record["kind"], "recall_record": record,
            "name": "n", "description": "d", "source": "s", "chunk_id": "c",
            "valid_from": record["date"], "supersedes": None, "superseded_by": None,
            "valid_until": None, "caused": [], "anchor_matches": [], "anchor_match": "none"}


def _dump(tmp_path, records: list[dict], truncated: bool = False, name: str = "dump.json"):
    path = tmp_path / name
    path.write_text(json.dumps({"tenant_id": "cca", "generation_id": "g1",
                                "truncated": truncated,
                                "records": [_entry(r) for r in records]}), encoding="utf-8")
    return path


def _run(capsys, *paths) -> tuple[int, dict]:
    code = main(["roundtrip-check", "--dump", *map(str, paths), "--json"])
    return code, json.loads(capsys.readouterr().out)


def test_an_identical_dump_passes(tmp_path, ledger, capsys):
    """Every row served unchanged, across two pages and beside a code chunk: exit 0.

    Red 2026-10-21: mutation `if True:` for the skip test in `served_records` (nothing read);
    failed at `assert code == 0` (exit 1, every row missing).
    """
    records = [to_record(r) for r in ledger]
    first = _dump(tmp_path, records[:4], name="p1.json")
    second = json.loads(_dump(tmp_path, records[4:], name="p2.json").read_text())
    second["records"].append({"id": "x", "kind": "code",
                              "recall_record": {"file": "a.py", "symbol": None}})
    (tmp_path / "p2.json").write_text(json.dumps(second), encoding="utf-8")
    code, out = _run(capsys, first, tmp_path / "p2.json")
    assert code == 0
    assert out["ok"] and out["rows"] == out["records"] == len(ledger)


def test_a_dropped_field_is_changed_by_name(tmp_path, ledger, capsys):
    """A record that lost one field reports `<id>: <field>`.

    Red 2026-10-21: mutation in `check`, fields compared over `set(want) & set(got)` (only
    the keys both hold); failed at `assert code == 1` (exit 0, the drop unseen).
    """
    records = [to_record(r) for r in ledger]
    del records[1]["caused_by"]
    code, out = _run(capsys, _dump(tmp_path, records))
    assert code == 1
    assert out["changed"] == [f"{ledger[1].id}: caused_by"]
    assert out["missing"] == out["orphan"] == []


def test_a_nested_difference_is_changed(tmp_path, ledger, capsys):
    """An anchor hash or an evidence condition that changed inside a list or object is caught.

    Red 2026-10-21 (the plan's mutation): `check` comparing only scalar fields, dict and
    list values skipped; failed at `assert code == 1` (exit 0, nothing reported).
    """
    records = [to_record(r) for r in ledger]
    records[0]["anchors"][0]["code_sha"] = "f" * 64
    records[3]["evidence"]["red_proof"] = "absent"
    code, out = _run(capsys, _dump(tmp_path, records))
    assert code == 1
    assert out["changed"] == [f"{ledger[0].id}: anchors", f"{ledger[3].id}: evidence"]


def test_a_json_type_change_is_changed(tmp_path, ledger, capsys):
    """`false` served as `0` is a different JSON value, though Python calls them equal.

    Red 2026-10-21: mutation in `check` comparing with `!=` on the parsed values instead of
    canonical JSON; failed at `assert code == 1` (exit 0, `0 == False` in Python).
    """
    records = [to_record(r) for r in ledger]
    fp = next(i for i, r in enumerate(records) if r["kind"] == "false_positive")
    records[fp]["high_stakes"] = 0
    code, out = _run(capsys, _dump(tmp_path, records))
    assert code == 1
    assert out["changed"] == [f"{records[fp]['id']}: high_stakes"]


def test_a_missing_record_and_an_orphan(tmp_path, ledger, capsys):
    """A row the dump lacks is missing; a record no row holds is an orphan.

    Red 2026-10-21: mutation `pass` in place of `result.missing.append(row_id)`; failed at
    `assert out["missing"] == [gone["id"]]` (empty list).
    Red 2026-10-21: mutation `result.orphan = []`; failed at
    `assert out["orphan"] == ["widgets:0123456789ab:BUG-404"]` (empty list).
    """
    records = [to_record(r) for r in ledger]
    gone = records.pop(2)
    stranger = copy.deepcopy(records[0])
    stranger["id"] = stranger["bug_id"] = "widgets:0123456789ab:BUG-404"
    code, out = _run(capsys, _dump(tmp_path, [*records, stranger]))
    assert code == 1
    assert out["missing"] == [gone["id"]]
    assert out["orphan"] == ["widgets:0123456789ab:BUG-404"]


def test_a_memo_without_a_record_counts_as_missing(tmp_path, ledger, capsys):
    """A pre-change memo is served with no `recall_record`: its row is missing, never passing.

    Red 2026-10-21: mutation in `served_records` accepting an entry without `recall_record`
    as present (its `id` taken from the entry); failed at `assert out["missing"] == [...]`.
    """
    records = [to_record(r) for r in ledger]
    dump = json.loads(_dump(tmp_path, records).read_text())
    dump["records"][0]["recall_record"] = None
    (tmp_path / "dump.json").write_text(json.dumps(dump), encoding="utf-8")
    code, out = _run(capsys, tmp_path / "dump.json")
    assert code == 1
    assert out["missing"] == [ledger[0].id]


def test_a_truncated_dump_is_refused(tmp_path, ledger, capsys):
    """An incomplete dump cannot support any claim: exit 2, no lists printed.

    Red 2026-10-21: mutation `if False:` for the truncation test in `served_records`; failed
    at `assert code == 2` (exit 0: a truncated dump passed).
    """
    code = main(["roundtrip-check", "--dump",
                 str(_dump(tmp_path, [to_record(r) for r in ledger], truncated=True))])
    assert code == 2
    assert "truncated" in capsys.readouterr().err
