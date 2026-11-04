"""Ledger rows carry the fixed defect class: from L2 for new fixes, from defect_classes.jsonl
for findings recorded before `record L2` required one (`cca_checks/bugs/class_log.py`).

A row without a class must stay byte-identical: RE-call before #1182 refuses the class keys,
and its memo loader then drops the whole record from the auditor brief.

Each test names the mutation that turned it red, alone, against this file's other tests green.
"""
from __future__ import annotations

import json
from pathlib import Path

from cca_checks.bugs import class_log, records
from cca_checks.bugs.forward import _defect_class
from cca_checks.bugs.model import EVIDENCED, BugRow
from cca_checks.bugs.records import from_record, to_record


def _bug(**over) -> BugRow:
    base = dict(
        project="widgets", repo="acme/widgets", finding_id="BUG-7",
        fix_commit="0123456789abcdef0123456789abcdef01234567", date="2026-09-01",
        files=["src/pool.py"], summary="the lease raced", severity="P2", auditor="bug-auditor",
        category="concurrency", run_id="run-1",
        evidence={"confirmed_l25": EVIDENCED, "fixed": EVIDENCED, "l6_approved": EVIDENCED,
                  "red_proof": EVIDENCED},
    )
    base.update(over)
    return BugRow(**base)


def _log(root: Path, *rows: dict) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / class_log.NAME).write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")


def _note(**over) -> dict:
    return {"repo": "acme/widgets", "run": "run-1__0123456789ab", "finding_id": "BUG-7",
            "defect_class": "race", "defect_class_reason": "", **over}


def test_a_row_without_a_class_writes_no_class_keys():
    """Red 2026-11-04 on the remote test host: `BugRow.to_json` writing the class keys unconditionally."""
    assert "defect_class" not in _bug().to_json()
    assert "defect_class" not in to_record(_bug())


def test_a_classed_row_round_trips_through_its_record():
    """Red 2026-11-04 on the remote test host: `records._fields` ignoring OPTIONAL (refused as unknown)."""
    row = _bug(defect_class="other", defect_class_reason="a font defect fits no class")
    record = to_record(row)
    assert record["defect_class"] == "other"
    assert from_record(record) == row


def test_an_unknown_field_is_still_refused():
    """The optional class keys widen the record by exactly two names. Red 2026-11-04 on the remote test host:
    `unknown = set()` in `records._fields`."""
    record = to_record(_bug())
    record["tags"] = []
    try:
        from_record(record)
    except ValueError as exc:
        assert "unknown ['tags']" in str(exc)
    else:
        raise AssertionError("a record with an unknown field was accepted")


def test_the_backfill_gives_a_class_to_a_row_that_has_none(tmp_path: Path):
    """Covered by the projection mutation below (no mutation of its own)."""
    _log(tmp_path, _note())
    assert class_log.apply(_bug(), class_log.read(tmp_path)).defect_class == "race"


def test_a_class_from_l2_wins_over_the_backfill(tmp_path: Path):
    """Red 2026-11-04 on the remote test host: `class_log.apply` no longer skipping a row that has a class."""
    _log(tmp_path, _note())
    row = _bug(defect_class="fail-open")
    assert class_log.apply(row, class_log.read(tmp_path)).defect_class == "fail-open"


def test_a_finding_classed_twice_differently_gets_no_class(tmp_path: Path):
    """The same run captured at two commits can reuse a finding id for two findings.
    Red 2026-11-04 on the remote test host: the conflicted keys kept in `class_log.read`."""
    _log(tmp_path, _note(), _note(run="run-1__fedcba987654", defect_class="doc-drift"))
    assert class_log.apply(_bug(), class_log.read(tmp_path)).defect_class is None


def test_an_unlisted_class_in_the_backfill_is_ignored(tmp_path: Path):
    """Red 2026-11-04 on the remote test host: `class_log.read` without the DEFECT_CLASSES check."""
    _log(tmp_path, _note(defect_class="dry"))
    assert class_log.read(tmp_path) == {}


def test_read_ledger_projects_the_backfill(tmp_path: Path):
    """The class reaches the rows every reader, memo and record is built from.
    Red 2026-11-04 on the remote test host: `read_ledger` without `class_log.apply`."""
    row = _bug()
    (tmp_path / "bugs.jsonl").write_text(json.dumps(row.to_json()) + "\n", encoding="utf-8")
    _log(tmp_path, _note())
    rows, skipped = records.read_ledger(tmp_path)
    assert not skipped and rows[row.bug_id].defect_class == "race"


def test_a_malformed_identity_is_left_alone_not_raised(tmp_path: Path):
    """A ledger line whose run_id is a list must not become a dictionary key: that raised
    TypeError and crashed anchor recovery for the whole ledger (caught by
    test_anchor_recovery_audit::test_malformed_run_has_a_structured_public_result, which went red
    on the first version of this file). Red 2026-11-04 on the remote test host: the identity check
    reverted to `if not row.run_id`; failed at the AssertionError naming the TypeError."""
    _log(tmp_path, _note())
    row = _bug(run_id=["run-1"])
    try:
        projected = class_log.apply(row, class_log.read(tmp_path))
    except TypeError as exc:
        raise AssertionError(f"a malformed run_id crashed the projection: {exc}") from exc
    assert projected is row


def test_l2_classes_outside_the_list_do_not_reach_the_ledger():
    """Red 2026-11-04 on the remote test host: `forward._defect_class` returning its input unchecked."""
    assert _defect_class("race") == "race"
    assert _defect_class("dry") is None and _defect_class(None) is None
