"""Which findings a closed run deferred, and whether the second pass is offered and advised."""

from __future__ import annotations

import json

import pytest

from cca_checks import deferred


def _s(run_id="r1", **over):
    s = {"schema": 1, "run_id": run_id, "tier": "STANDARD", "mode": "DIFF", "no_fix": False,
         "base_commit": "b", "commit": "c", "parent": None,
         "completed_utc": "2026-10-11T10:00:00Z", "pool": ["F-1"], "human_pending": 0}
    s.update(over)
    return s


def _f(i, priority="P3"):
    return {"id": f"F-{i}", "priority": priority, "file": "app.py", "line": i, "claim": f"c{i}"}


@pytest.mark.parametrize("over, reason", [
    ({"pool": []}, "nothing was deferred"),
    ({"no_fix": True}, "no-fix"),
    ({"mode": "DEFERRED"}, "third pass"),
    ({"mode": "HUNT"}, "hunt"),
])
def test_these_runs_are_never_offered_a_pass(over, reason):
    """Red 2026-10-11, one per case: mutation the matching `if` deleted in why_not_offered;
    each case failed at `assert offer.offered is False` (True is False)."""
    s = _s(**over)
    offer = deferred.assess(s, [s], [_f(1)] if s["pool"] else [], commit_reachable=True)
    assert offer.offered is False and offer.recommendation is None
    assert reason in offer.reasons[0]


def test_a_pool_a_completed_deferred_run_took_is_not_offered_again():
    """Red 2026-10-11: mutation the loop of consumed_by replaced by `return None`; failed at
    `assert not offer.offered`."""
    parent, child = _s("p"), _s("c", mode="DEFERRED", parent="p", pool=[])
    offer = deferred.assess(parent, [parent, child], [_f(1)], commit_reachable=True)
    assert not offer.offered and "deferred run c already took" in offer.reasons[0]


def test_findings_awaiting_a_human_recommend_skip_even_with_a_p2():
    """Red 2026-10-11: mutation the `if human > 0:` branch deleted in assess; failed at
    `assert offer.offered and offer.recommendation == "SKIP"` (got RUN)."""
    s = _s(pool=["F-1"], human_pending=1)
    offer = deferred.assess(s, [s], [_f(1, "P2")], commit_reachable=True)
    assert offer.offered and offer.recommendation == "SKIP"
    assert "await a human decision" in offer.reasons[0]


def test_any_p2_recommends_run():
    """Red 2026-10-11: mutation `if counts["P2"]:` became `if False:` in assess; failed at
    `assert offer.recommendation == "RUN" and ...` (got SKIP)."""
    s = _s(pool=["F-1"])
    offer = deferred.assess(s, [s], [_f(1, "P2")], commit_reachable=True)
    assert offer.recommendation == "RUN" and offer.counts == {"P1": 0, "P2": 1, "P3": 0}


def test_three_p3_recommend_run_and_two_recommend_skip():
    """Red 2026-10-11: mutation `>= DEFERRED_MIN_P3` became `> DEFERRED_MIN_P3` in assess;
    failed at `assert three.recommendation == "RUN"` (got SKIP)."""
    s3, s2 = _s(pool=["F-1", "F-2", "F-3"]), _s(pool=["F-1", "F-2"])
    three = deferred.assess(s3, [s3], [_f(1), _f(2), _f(3)], commit_reachable=True)
    two = deferred.assess(s2, [s2], [_f(1), _f(2)], commit_reachable=True)
    assert three.recommendation == "RUN"
    assert two.recommendation == "SKIP" and "four agent passes" in two.reasons[0]


def test_an_unreachable_commit_is_reported():
    """Red 2026-10-11: mutation `notes = []` in assess; failed at
    `assert any("not an ancestor of HEAD" in r for r in offer.reasons)`."""
    s = _s()
    offer = deferred.assess(s, [s], [_f(1)], commit_reachable=False)
    assert offer.commit_reachable is False
    assert any("not an ancestor of HEAD" in r for r in offer.reasons)


def test_an_older_unconsumed_pool_is_listed_beside_the_newest():
    """Red 2026-10-11: mutation `others = []` in assess; failed at
    `assert offer.other_pools == ["old"]` ([] == ['old'])."""
    old, new = _s("old"), _s("new", pool=[], completed_utc="2026-10-11T11:00:00Z")
    offer = deferred.assess(new, [old, new], [], commit_reachable=True)
    assert offer.other_pools == ["old"]


def test_the_pool_is_l2_5_deferred_then_l3_deferred_each_once():
    """Red 2026-10-11, two mutations in pool_ids: the l25 half deleted failed at
    `assert deferred.pool_ids(l25, l3) == ["F-3", "F-2"]` (got ['F-2', 'F-3']); `return
    list(dict.fromkeys(ids))` became `return ids`, which failed at the same assert (got
    ['F-3', 'F-2', 'F-3'])."""
    l25 = [{"id": "F-3", "verdict": "DEFERRED"}, {"id": "F-1", "verdict": "CONFIRMED"}]
    l3 = {"to_fix": ["F-1"], "deferred": ["F-2", "F-3"]}
    assert deferred.pool_ids(l25, l3) == ["F-3", "F-2"]
    assert deferred.pool_ids(l25, None) == ["F-3"]


def test_findings_for_returns_the_pool_objects_in_pool_order(tmp_path):
    """Red 2026-10-11: mutation `for i in summary["pool"]` became `for i in
    sorted(summary["pool"])` in findings_for; failed at the id-order assert (['F-1', 'F-3']
    against ['F-3', 'F-1'])."""
    run = tmp_path / ".claude/audits/runs/r1"
    run.mkdir(parents=True)
    (run / "L2.json").write_text(json.dumps([_f(1), _f(2), _f(3)]), encoding="utf-8")
    assert [f["id"] for f in deferred.findings_for(tmp_path, _s(pool=["F-3", "F-1"]))] == \
        ["F-3", "F-1"]


def test_findings_for_names_a_missing_l2_file(tmp_path):
    """Review Focus 1. Red 2026-10-11: mutation the `except` body replaced by `return []` in
    findings_for; failed at pytest.raises (DID NOT RAISE LookupError)."""
    (tmp_path / ".claude/audits/runs/r1").mkdir(parents=True)
    with pytest.raises(LookupError, match="L2.json"):
        deferred.findings_for(tmp_path, _s())


def test_findings_for_names_pool_ids_the_l2_file_lacks(tmp_path):
    """Red 2026-10-11: mutation the `if missing:` raise deleted in findings_for; failed at
    pytest.raises (the lookup of the absent id died with a bare KeyError, which is a
    LookupError too, so it is the match on the message that fails)."""
    run = tmp_path / ".claude/audits/runs/r1"
    run.mkdir(parents=True)
    (run / "L2.json").write_text(json.dumps([_f(1)]), encoding="utf-8")
    with pytest.raises(LookupError, match="L2 file lacks pool ids F-9"):
        deferred.findings_for(tmp_path, _s(pool=["F-1", "F-9"]))


def test_an_unreadable_summary_is_reported_not_skipped(tmp_path):
    """Review Focus 2. Red 2026-10-11: mutation `unreadable.append(...)` removed from the
    OSError/ValueError branch of load_summaries; failed at `assert len(unreadable) == 1`
    (0 == 1)."""
    good = tmp_path / ".claude/audits/runs/a"
    bad = tmp_path / ".claude/audits/runs/b"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    (good / "summary.json").write_text(json.dumps(_s("a")), encoding="utf-8")
    (bad / "summary.json").write_text("{torn", encoding="utf-8")
    summaries, unreadable = deferred.load_summaries(tmp_path)
    assert [s["run_id"] for s in summaries] == ["a"]
    assert len(unreadable) == 1 and unreadable[0].endswith("b/summary.json")


def test_a_bom_prefixed_ledger_still_counts_its_parked_rows(tmp_path):
    """A ledger Windows PowerShell 5.1 created starts with a BOM; its first row still counts.

    Red 2026-10-11: mutation `read_text(encoding="utf-8-sig")` became
    `read_text(encoding="utf-8")` in parked_count; failed at
    `assert deferred.parked_count(tmp_path, "r1") == 2` (1 == 2). Each of the three row filters
    (`"verdict" not in row`, the run id, the result) was also removed in turn; each failed at
    the same assert (3 == 2).
    """
    ledger = tmp_path / ".claude/audits/AUDITOR_SCORECARD.jsonl"
    ledger.parent.mkdir(parents=True)
    rows = [{"run_id": "r1", "fix_id": "FIX-0", "result": "parked"},
            {"run_id": "r1", "fix_id": "FIX-1", "result": "parked"},
            {"run_id": "r2", "fix_id": "FIX-0", "result": "parked"},
            {"run_id": "r1", "fix_id": "FIX-2", "result": "fixed"},
            {"run_id": "r1", "auditor": "a", "verdict": "CONFIRMED", "result": "parked"}]
    body = "\n".join(json.dumps(r) for r in rows) + "\n"
    ledger.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    assert deferred.parked_count(tmp_path, "r1") == 2
