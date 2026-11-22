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
    """Invariant: only CONFIRMED rows count, per original finding.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_cycles_count_confirmed_fix_audit_findings_per_original_finding:
    `cycles` `== "CONFIRMED"` became `!= "CONFIRMED"`; failed `assert {'F-1': 1} == {'F-1': 2, 'F-2': 1}`."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "CONFIRMED", 2)
    _j(tmp_path, "F-1", "FALSE_POSITIVE", 3)
    _j(tmp_path, "F-2", "CONFIRMED", 1)
    assert fr.cycles(tmp_path, RUN) == {"F-1": 2, "F-2": 1}


def test_the_third_cycle_parks_the_finding(tmp_path):
    """Invariant: the third CONFIRMED cycle parks; a PARKED row parks outright.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_the_third_cycle_parks_the_finding:
    `parked` `n >= CAP` became `n > CAP`; the second assertion failed,
    `assert {'F-2'} == {'F-1', 'F-2'}`."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "CONFIRMED", 2)
    assert fr.parked(tmp_path, RUN) == set()
    _j(tmp_path, "F-1", "CONFIRMED", 3)
    _j(tmp_path, "F-2", "PARKED", 1)
    assert fr.parked(tmp_path, RUN) == {"F-1", "F-2"}


def test_pass_numbering_continues_after_a_pause(tmp_path):
    """Invariant: numbering is one past the highest pass journaled; an empty journal starts at 1.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_pass_numbering_continues_after_a_pause:
    `next_pass` `1 + max(` became `0 + max(`; the first assertion failed, `assert 0 == 1`."""
    assert fr.next_pass(tmp_path, RUN) == 1
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "RESOLVED", 2)
    assert fr.next_pass(tmp_path, RUN) == 3


def test_side_ledger_rows_never_reach_the_disposition_ledger(tmp_path):
    """Invariant: fix audit rows go to the side ledger, never the disposition ledger.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_side_ledger_rows_never_reach_the_disposition_ledger:
    `append_verdicts` wrote to AUDITOR_SCORECARD.jsonl instead of `LEDGER`; failed
    `assert ({'CONFIRMED': 2}, 0) == ({'CONFIRMED': 1}, 0)`."""
    scorecard = tmp_path / pipeline_state.LEDGER
    scorecard.parent.mkdir(parents=True)
    scorecard.write_text(json.dumps({"run_id": RUN, "verdict": "CONFIRMED", "fix_id": "FIX-1"})
                         + "\n", encoding="utf-8")
    assert fr.append_verdicts(tmp_path, RUN, _audit()) == 1
    assert pipeline_state._ledger_rows(tmp_path, RUN) == ({"CONFIRMED": 1}, 0)
    runs = ledgers.load_ledger(scorecard, "r", ledgers.LoadReport())
    assert "FA-1-1" not in json.dumps([r.__dict__ for r in runs], default=str)
    side = (tmp_path / fr.LEDGER).read_text(encoding="utf-8").splitlines()
    row = json.loads(side[0])
    assert (row["run_id"], row["layer"], row["id"]) == (RUN, "L5.4", "FA-1-1")


@pytest.mark.parametrize("mutate", [
    lambda a: a["findings"][0].update(id="FA-2-1"),
    lambda a: a["findings"][0].update(id="F-1"),
    lambda a: a["findings"][0].update(id="FA-01-1"),
    lambda a: a["findings"][0].update(verdict="DUPLICATE"),
    lambda a: a["findings"][0].update(priority="P0"),
    lambda a: a.update({"pass": 0, "findings": []}),
    lambda a: a.update({"hunks": ["h1", "h1"]}),
    lambda a: a.update({"hunks": [""]}),
], ids=["wrong-pass", "bad-prefix", "leading-zero", "verdict", "priority", "pass-zero",
        "dup-hunk", "empty-hunk"])
def test_a_malformed_verdict_file_is_refused(mutate):
    """Each case is refused by its own line of `l54_audit`; the good file passes first.
    Red 2026-11-22, each `tests/test_fixaudit_record.py::test_a_malformed_verdict_file_is_refused[<id>]`
    failed `Failed: DID NOT RAISE ShapeError` under its mutation in layer_outputs.py:
    wrong-pass: removed `or int(match.group(1)) != pass_no`; bad-prefix: `if not match or` became
    `if match and`; leading-zero: `_FA_ID` loosened to `FA-([0-9]+)-[0-9]+`; verdict and priority:
    their `if` became `if False:`; pass-zero: removed `or pass_no < 1`; dup-hunk: removed the
    `len(set(hunks))` test; empty-hunk: dropped `and h`."""
    assert lo.l54_audit(_audit())["pass"] == 1
    bad = _audit()
    mutate(bad)
    with pytest.raises(lo.ShapeError):
        lo.l54_audit(bad)


def test_a_torn_journal_line_does_not_swallow_the_next_row(tmp_path):
    """A crash left half a line; the next append starts on a new line, so the fragment stays
    its own line and the later CONFIRMED row is not lost with it.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_torn_journal_line_does_not_swallow_the_next_row:
    `_append` `if torn:` became `if False:`; the last line then held the fragment and the new row
    joined, so `json.loads(lines[-1])` raised JSONDecodeError at the test's own parse of the row."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    path = tmp_path / ".claude" / "audits" / "runs" / RUN / fr.JOURNAL
    with path.open("ab") as handle:
        handle.write(b'{"finding": "F-1", "ver')
    _j(tmp_path, "F-2", "CONFIRMED", 2)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["finding"] == "F-2"
    assert lines[1] == '{"finding": "F-1", "ver'


def test_a_malformed_journal_line_stops_the_run_naming_file_and_line(tmp_path):
    """Deliberately fail closed: skipping the line could hide a CONFIRMED cycle.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_malformed_journal_line_stops_the_run_naming_file_and_line:
    `_rows` `raise ValueError(...)` became `continue`; failed `DID NOT RAISE ValueError`."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    path = tmp_path / ".claude" / "audits" / "runs" / RUN / fr.JOURNAL
    with path.open("ab") as handle:
        handle.write(b"not json\n")
    with pytest.raises(ValueError, match=r"fixaudit-journal\.jsonl line 2"):
        fr.cycles(tmp_path, RUN)
