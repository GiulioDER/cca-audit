"""The fix audit journal, its side ledger and the shape of the L5.4 verdict file."""

from __future__ import annotations

import json

import pytest

from cca_checks import fixaudit_record as fr
from cca_checks import layer_outputs as lo
from cca_checks import pipeline_state
from cca_checks.bugs import ledgers

RUN = "run-1"


def _j(root, finding, verdict, pass_no=1, fix_id="FIX-1"):
    fr.journal(root, RUN, finding=finding, fix_id=fix_id, pass_no=pass_no, verdict=verdict,
               reason="r")


def _audit(pass_no=1):
    return {"pass": pass_no, "audited_tree": "abc", "hunks": ["h1"], "findings": [{
        "id": f"FA-{pass_no}-1", "fix_id": "FIX-1", "finding": "F-1", "priority": "P2",
        "verdict": "CONFIRMED", "file": "a.py", "line": 3, "claim": "c"}]}


def test_cycles_count_confirmed_fix_audit_findings_per_original_finding(tmp_path):
    """Red: mutation in `cycles` `== "CONFIRMED"` became `!= "CONFIRMED"`; the count assertion
    failed (see report). Invariant: only CONFIRMED rows count, per original finding."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "CONFIRMED", 2)
    _j(tmp_path, "F-1", "FALSE_POSITIVE", 3)
    _j(tmp_path, "F-2", "CONFIRMED", 1)
    assert fr.cycles(tmp_path, RUN) == {"F-1": 2, "F-2": 1}


def test_the_third_cycle_parks_the_finding(tmp_path):
    """Red: mutation in `parked` `n >= CAP` became `n > CAP`; the first assertion failed.
    Also a PARKED row parks outright."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "CONFIRMED", 2)
    assert fr.parked(tmp_path, RUN) == set()
    _j(tmp_path, "F-1", "CONFIRMED", 3)
    _j(tmp_path, "F-2", "PARKED", 1)
    assert fr.parked(tmp_path, RUN) == {"F-1", "F-2"}


def test_pass_numbering_continues_after_a_pause(tmp_path):
    """Red: mutation in `next_pass` `1 + max(...)` became `max(...)`; the 3 vs 2 assertion
    failed. An empty journal starts at 1."""
    assert fr.next_pass(tmp_path, RUN) == 1
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "RESOLVED", 2)
    assert fr.next_pass(tmp_path, RUN) == 3


def test_side_ledger_rows_never_reach_the_disposition_ledger(tmp_path):
    """Red: mutation in `append_verdicts` writing to `pipeline_state.LEDGER` instead of
    `LEDGER`; the disposition tally assertion failed."""
    scorecard = tmp_path / pipeline_state.LEDGER
    scorecard.parent.mkdir(parents=True)
    scorecard.write_text(json.dumps({"run_id": RUN, "verdict": "CONFIRMED", "fix_id": "FIX-1"})
                         + "\n", encoding="utf-8")
    assert fr.append_verdicts(tmp_path, RUN, _audit()) == 1
    side = (tmp_path / fr.LEDGER).read_text(encoding="utf-8").splitlines()
    row = json.loads(side[0])
    assert (row["run_id"], row["layer"], row["id"]) == (RUN, "L5.4", "FA-1-1")
    assert pipeline_state._ledger_rows(tmp_path, RUN) == ({"CONFIRMED": 1}, 0)
    report = ledgers.LoadReport()
    runs = ledgers.load_ledger(scorecard, "r", report)
    assert "FA-1-1" not in json.dumps([r.__dict__ for r in runs], default=str)


@pytest.mark.parametrize("mutate", [
    lambda a: a["findings"][0].update(id="FA-2-1"),
    lambda a: a["findings"][0].update(id="F-1"),
    lambda a: a["findings"][0].update(verdict="DUPLICATE"),
    lambda a: a["findings"][0].update(priority="P0"),
    lambda a: a.update({"pass": 0}),
])
def test_a_verdict_file_with_a_bad_id_is_refused(mutate):
    """Red: mutation in `l54_audit` removing the `int(match.group(1)) != pass_no` test let the
    FA-2-1 case through; `pytest.raises` failed with DID NOT RAISE. The good file passes."""
    assert lo.l54_audit(_audit())["pass"] == 1
    bad = _audit()
    mutate(bad)
    with pytest.raises(lo.ShapeError):
        lo.l54_audit(bad)
