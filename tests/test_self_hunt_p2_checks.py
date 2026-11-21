"""P2 checker and scorecard fixes from the CCA self hunt, run 20261120T201246Z.

Each test names its finding. Red proofs: each behaviour test below, run against the snapshot
taken before these fixes, failed in its assertion.
"""
from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from cca_checks import pipeline_state as ps
from cca_checks.plugin.tools import cca_scorecard as sc
from cca_checks.plugin.tools import cca_tautology_check as tc
from cca_checks.properties import PropertyViolation, assert_bounded

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)


@pytest.mark.parametrize("lo,hi,y", [(0.0, 1e9, -0.9), (-1e12, 0.0, 500.0), (-1e9, 1.0, 1.9)])
def test_a_far_bound_does_not_widen_the_near_one(lo, hi, y):
    """NUM-001. Invariant: a result outside [lo, hi] by more than its own bound's tolerance
    is a violation. Failure mode: one eps sized by max(|lo|, |hi|) let a wrong-sign result
    through. Red: DID NOT RAISE on the pre-fix properties.py."""
    with pytest.raises(PropertyViolation):
        assert_bounded(lambda: y, (), lo, hi)


def test_a_one_ulp_overshoot_at_a_large_bound_is_still_tolerated():
    """Control: the tolerance at the bound the result sits next to is unchanged."""
    assert_bounded(lambda: 1e12 * (1 + 1e-12), (), 0.0, 1e12)


def _row(**fields) -> str:
    base = {"ts": "2026-11-19T10:00:00Z", "run_id": "A", "finding_id": "F-1", "auditor": "a",
            "category": "c", "verdict": "CONFIRMED"}
    return json.dumps({**base, **fields}) + "\n"


def test_a_timestamp_without_an_offset_is_scored(tmp_path):
    """NUM-002. Invariant: a valid ISO timestamp without an offset is a scored row.
    Failure mode: the naive datetime could not be compared with the aware cutoff, so the
    row was counted malformed and left every cell. Red: rows_scored 0 on the pre-fix tool."""
    ledger = tmp_path / "AUDITOR_SCORECARD.jsonl"
    ledger.write_text(_row(ts="2026-11-19T10:00:00") + _row(ts="2026-11-19", finding_id="F-2"),
                      encoding="utf-8")
    rep = sc.build(ledger, now=NOW)
    assert rep.rows_scored == 2 and rep.rows_skipped_malformed == 0


def test_a_deferred_pass_does_not_count_a_finding_twice(tmp_path):
    """NUM-003. Invariant: a finding its parent run already judged counts once in its cell.
    Failure mode: a P2 confirmed and deferred at L3 in run A got a second row in deferred run
    B, credited to the same auditor, so the precision denominator counted it twice.
    Red: cell n 2 on the pre-fix tool."""
    audits = tmp_path / ".claude" / "audits"
    (audits / "runs" / "B").mkdir(parents=True)
    (audits / "runs" / "B" / "summary.json").write_text(json.dumps({"parent": "A"}),
                                                        encoding="utf-8")
    ledger = audits / "AUDITOR_SCORECARD.jsonl"
    ledger.write_text(_row(run_id="A") + _row(run_id="B") + _row(run_id="B", finding_id="F-9"),
                      encoding="utf-8")
    rep = sc.build(ledger, now=NOW)
    assert [(c.key, c.n) for c in rep.cells] == [("a/c", 2)]
    assert rep.rows_skipped_reverified == 1


def test_verify_refuses_a_snapshot_older_than_the_open_run(git_repo, monkeypatch):
    """STAKES-006. Invariant: verify judges proofs only against a snapshot of this run.
    Failure mode: a run that skipped `snapshot` was judged against an earlier run's pre-fix
    content. Red: DID NOT RAISE on the pre-fix tool (it ran the proof)."""
    monkeypatch.chdir(git_repo)
    (git_repo / "app.py").write_text("x = 1\n", encoding="utf-8")
    snapdir = Path(".claude/audits/.prefix_snapshot")
    tc.cmd_snapshot([Path("app.py")], snapdir)
    earlier = time.time() - 120
    os.utime(snapdir / tc.MANIFEST, (earlier, earlier))
    ps.start("STANDARD", root=git_repo)
    with pytest.raises(SystemExit, match="before run"):
        tc.verify([("FIX-1", "tests/test_nothing.py::test_x")], snapdir)


def test_a_non_string_finding_id_is_counted_malformed(tmp_path):
    """BUG-003 of audit run 20261120T225224Z. Invariant: a malformed scored row is counted,
    never fatal. Failure mode: build() put finding_id into a set, so a list there raised
    TypeError and stopped Step 2.6. Red: TypeError (unhashable) on the pre-fix tool."""
    ledger = tmp_path / "AUDITOR_SCORECARD.jsonl"
    ledger.write_text(_row() + _row(finding_id=["F-2"]), encoding="utf-8")
    rep = sc.build(ledger, now=NOW)
    assert rep.rows_scored == 1 and rep.rows_skipped_malformed == 1


@pytest.mark.parametrize("where", ["open-run-state", "row-field"])
def test_a_deferred_pass_counts_once_before_it_closes(tmp_path, where):
    """NUM-001 of audit run 20261120T225224Z. Invariant: a finding re-verified by a deferred
    run counts once whether that run is open, closed or aborted. Failure mode: the parent was
    read only from runs/<id>/summary.json, which only `pipeline close` writes, so the run's own
    Step 2.6 (and any aborted deferred run) counted the finding twice. Red: cell n 2."""
    audits = tmp_path / ".claude" / "audits"
    audits.mkdir(parents=True)
    extra = {}
    if where == "open-run-state":
        (audits / "run-state.json").write_text(json.dumps({"run_id": "B", "parent": "A"}),
                                               encoding="utf-8")
    else:
        extra = {"parent_run_id": "A"}
    ledger = audits / "AUDITOR_SCORECARD.jsonl"
    ledger.write_text(_row(run_id="A") + _row(run_id="B", **extra), encoding="utf-8")
    rep = sc.build(ledger, now=NOW)
    assert [(c.key, c.n) for c in rep.cells] == [("a/c", 1)]


def test_a_wrong_hand_written_parent_cannot_drop_a_first_verdict(tmp_path):
    """Control for NUM-001's row field (found at L5.5): the hand-written parent_run_id never
    overrides a parent the tool recorded, and a row naming its own run is ignored, so a first
    run's own verdicts are not dropped. Finding ids recur across runs (BUG-001 in each)."""
    audits = tmp_path / ".claude" / "audits"
    (audits / "runs" / "B").mkdir(parents=True)
    (audits / "runs" / "B" / "summary.json").write_text(json.dumps({"parent": None}),
                                                        encoding="utf-8")
    ledger = audits / "AUDITOR_SCORECARD.jsonl"
    ledger.write_text(_row(run_id="A") + _row(run_id="B", parent_run_id="A")
                      + _row(run_id="C", parent_run_id="C", finding_id="F-7")
                      + _row(run_id="", finding_id="F-8") + _row(run_id="B", finding_id="F-8"),
                      encoding="utf-8")
    rep = sc.build(ledger, now=NOW)
    assert [(c.key, c.n) for c in rep.cells] == [("a/c", 5)]
    assert rep.rows_skipped_reverified == 0
