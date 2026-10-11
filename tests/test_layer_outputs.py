"""The L2, L2.5 and L3 work files must agree with the counts recorded beside them.

A later run reads these files back: the deferred pass finds its findings by id in L2 and its
pool in L2.5 and L3. A file that disagreed with its counts would hand that run a different pool
from the one the gate counted.
"""

from __future__ import annotations

import pytest

from cca_checks import layer_outputs as lo


def _f(i, priority="P2"):
    return {"id": f"F-{i}", "priority": priority}


def test_l2_accepts_findings_that_match_unique():
    """Red 2026-10-11: mutation `return data` became `return []` in l2_findings; failed at the
    assert (`assert [] == [{'id': 'F-1', ...}, ...]`)."""
    assert lo.l2_findings([_f(1), _f(2, "P3")], 2) == [_f(1), _f(2, "P3")]


def test_l2_refuses_a_count_that_disagrees_with_the_file():
    """Red 2026-10-11: mutation `if len(data) != unique:` became `if False:` in l2_findings;
    failed at pytest.raises (DID NOT RAISE ShapeError)."""
    with pytest.raises(lo.ShapeError, match="holds 1 findings but --unique is 2"):
        lo.l2_findings([_f(1)], 2)


def test_l2_refuses_a_finding_without_a_priority():
    """Red 2026-10-11: mutation the priority loop deleted in l2_findings; failed at
    pytest.raises (DID NOT RAISE ShapeError)."""
    with pytest.raises(lo.ShapeError, match="priority"):
        lo.l2_findings([{"id": "F-1"}], 1)


def test_l2_refuses_an_id_named_twice():
    """Red 2026-10-11: mutation `if dupes:` became `if False:` in _ids; failed at
    pytest.raises (DID NOT RAISE ShapeError)."""
    with pytest.raises(lo.ShapeError, match="more than once"):
        lo.l2_findings([_f(1), _f(1)], 2)


def test_l2_refuses_a_file_that_is_not_an_array():
    """Red 2026-10-11: mutation the `isinstance(data, list)` check deleted in l2_findings;
    failed at pytest.raises(match="JSON array"): the ShapeError raised was 'L2 entry 0 needs a
    non-empty string "id"', so the regex did not match."""
    with pytest.raises(lo.ShapeError, match="JSON array"):
        lo.l2_findings({"F-1": {}}, 1)


def test_l2_5_maps_ids_to_verdicts_when_the_tally_matches():
    """Red 2026-10-11: mutation `return verdicts` became `return {}` in l25_verdicts; failed at
    the assert (`assert {} == {'F-1': 'CONFIRMED', 'F-2': 'DEFERRED'}`)."""
    data = [{"id": "F-1", "verdict": "CONFIRMED"}, {"id": "F-2", "verdict": "DEFERRED"}]
    got = lo.l25_verdicts(data, {"confirmed": 1, "unverified": 1}, ["F-1", "F-2"])
    assert got == {"F-1": "CONFIRMED", "F-2": "DEFERRED"}


def test_l2_5_refuses_a_tally_that_disagrees_with_the_counts():
    """Red 2026-10-11: mutation `wrong = [...]` became `wrong = []` in l25_verdicts; failed at
    pytest.raises (DID NOT RAISE ShapeError)."""
    data = [{"id": "F-1", "verdict": "CONFIRMED"}, {"id": "F-2", "verdict": "CONFIRMED"}]
    with pytest.raises(lo.ShapeError, match="disagrees with its counts"):
        lo.l25_verdicts(data, {"confirmed": 1, "fp": 1}, ["F-1", "F-2"])


def test_l2_5_refuses_an_id_l2_did_not_record():
    """Red 2026-10-11: mutation the `row["id"] not in known` check deleted in l25_verdicts;
    failed at pytest.raises (DID NOT RAISE ShapeError)."""
    with pytest.raises(lo.ShapeError, match="L2 did not record"):
        lo.l25_verdicts([{"id": "X-9", "verdict": "CONFIRMED"}], {"confirmed": 1}, ["F-1"])


def test_l2_5_refuses_an_unknown_verdict():
    """Red 2026-10-11: mutation the verdict-membership check deleted in l25_verdicts; failed
    with the uncaught `KeyError: 'MAYBE'` (from `tally[verdict] += 1`) escaping
    pytest.raises(ShapeError), which is how this mutation manifests."""
    with pytest.raises(lo.ShapeError, match="expected one of"):
        lo.l25_verdicts([{"id": "F-1", "verdict": "MAYBE"}], {}, ["F-1"])


def test_l2_5_refuses_a_verdict_that_is_not_a_string():
    """Red 2026-10-11: mutation `if not isinstance(verdict, str) or verdict not in COUNT_OF:`
    reverted to `if verdict not in COUNT_OF:` in l25_verdicts; failed with the uncaught
    `TypeError: cannot use 'list' as a dict key (unhashable type: 'list')` escaping
    pytest.raises(ShapeError), which is how this mutation manifests."""
    with pytest.raises(lo.ShapeError, match="expected one of"):
        lo.l25_verdicts([{"id": "F-1", "verdict": ["CONFIRMED"]}], {}, ["F-1"])


def test_l3_accepts_a_plan_over_confirmed_ids():
    """Red 2026-10-11: mutation `return plan` became `return {}` in l3_plan; failed at the
    assert (`assert {} == {'to_fix': ['F-1'], 'deferred': ['F-2']}`)."""
    l25 = {"F-1": "CONFIRMED", "F-2": "CONFIRMED", "F-3": "DEFERRED"}
    plan = lo.l3_plan({"to_fix": ["F-1"], "deferred": ["F-2"]}, 1, 1, l25)
    assert plan == {"to_fix": ["F-1"], "deferred": ["F-2"]}


def test_l3_refuses_an_id_l2_5_did_not_confirm():
    """Red 2026-10-11: mutation `unconfirmed = [...]` became `unconfirmed = []` in l3_plan;
    failed at pytest.raises (DID NOT RAISE ShapeError)."""
    with pytest.raises(lo.ShapeError, match="did not confirm"):
        lo.l3_plan({"to_fix": ["F-3"], "deferred": []}, 1, 0, {"F-3": "DEFERRED"})


def test_l3_refuses_an_id_both_fixed_and_deferred():
    """Red 2026-10-11: mutation `if both:` became `if False:` in l3_plan; failed at
    pytest.raises (DID NOT RAISE ShapeError)."""
    l25 = {"F-1": "CONFIRMED"}
    with pytest.raises(lo.ShapeError, match="both fixes and defers"):
        lo.l3_plan({"to_fix": ["F-1"], "deferred": ["F-1"]}, 1, 1, l25)


def test_l3_refuses_a_list_length_that_disagrees_with_its_count():
    """Red 2026-10-11: mutation `if len(ids) != want:` became `if False:` in l3_plan; failed
    at pytest.raises (DID NOT RAISE ShapeError)."""
    with pytest.raises(lo.ShapeError, match="lists 1 ids but --deferred is 2"):
        lo.l3_plan({"to_fix": [], "deferred": ["F-1"]}, 0, 2, {"F-1": "CONFIRMED"})


def test_l3_refuses_a_file_that_is_not_the_plan_object():
    """Red 2026-10-11: mutation the `isinstance(data, dict)` check deleted in l3_plan; failed
    with the uncaught `TypeError: list indices must be integers or slices, not str` escaping
    pytest.raises(ShapeError), which is how this mutation manifests."""
    with pytest.raises(lo.ShapeError, match='"to_fix" and "deferred"'):
        lo.l3_plan([], 0, 0, {})
