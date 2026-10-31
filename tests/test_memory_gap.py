"""Tests for benchmarks/harness/memory_gap.py.

Red proof (one mutation per test, run alone, restored afterwards; each failed on the intended
assertion, not on import or collection):

- test_brief_is_read_from_the_receipt: BRIEF_LINE changed to r"^(BUG) " (had_brief ignores
  LESSON lines); fails on `assert had_brief(lesson, {}) is True` (False is True).
- test_hosted_brief_uses_the_block_counts: had_brief returns `sum(counts) > 0` (lessons_block
  dropped); fails on the `lessons_block: 2` case (False is True).
- test_only_confirmed_l1_findings_count: confirmed_l1 loses its `in PREFIX` condition; fails on
  `assert confirmed_l1(d) == 1` (2 == 1, FIX-1 counted).
- test_a_clean_run_counts_zero: the `L1.json == []` clean-run check in _classify made never true;
  fails on `assert rows["a"]["confirmed"] == 0` (None == 0).
- test_hunt_deferred_and_fast_are_excluded_and_counted: the `tier == "FAST"` check in _classify
  made never true; fails on the `out["excluded"] == {...}` comparison.
- test_a_summary_that_cannot_be_read_is_counted: the non-dict summary branch of _classify returns
  "hunt"; fails on `...["unreadable"] == 2` (1 == 2).
- test_unknown_brief_is_none_not_false: three mutations, each run alone: (a) the status check in
  had_brief removed, fails on `had_brief(down, {}) is None`; (b) the isinstance(lessons, int)
  guard removed (None summed as 0 via `lessons or 0`), fails on the null-counts hosted assertion;
  (c) the `not isinstance(data, dict)` check made to return False, fails on `had_brief(bad, {})`.
- test_unknown_brief_is_excluded_apart_from_no_receipt: the has_receipt test in _classify forced
  to False; fails on `excluded["unknown_brief"] == 1`.
- test_gap_is_brief_minus_none: report computes `mn - mb`; fails on `out["gap"] == 3.0` (-3.0).
- test_runs_deduplicates_by_run_id_and_honours_the_window: `or run_id in seen` removed from runs;
  fails on `== ["r1"]` (["r1", "r1"]).
- test_a_named_run_outside_the_bound_is_excluded: baseline is the pre-change memory_gap.py at
  65710c3 (text comparison of run ids), with `until` defaulted to "~" in a temp copy so the empty
  bound does not exclude everything; fails on `assert runs(...) == []` (one row, cca-deep-20260906).
- test_the_earliest_dispatch_time_is_the_run_time: run_time takes `max(times)`; fails on
  `assert [...] == ["r"]` ([] == ["r"]).
- test_a_stamp_run_without_dispatch_uses_its_id: the `moment(run_id)` fallback replaced by None;
  fails on `assert runs(...) == [...]` (an "untimed" row is returned instead).
- test_an_untimed_run_is_reported_not_counted: an untimed run is given an in-window time instead of
  an untimed row; fails on `rows[0]["excluded"] == "untimed"` (None).
- test_a_bad_bound_is_an_error: _bound returns None instead of raising; fails with
  `DID NOT RAISE SystemExit`.
- test_a_named_run_inside_the_window_counts_by_its_dispatch_time: the `until` test removed from
  runs; fails on `assert runs(tmp_path, "", "2026-10-31T00:00:00Z") == []` (the run is returned).
- test_gap_by_arm (2026-10-31): baseline is this branch at 0f0b62e (rows carry no `arm`); fails on
  `assert rows["h1"].get("arm") == "holdout" ...` (None). Mutation: `_classify` sets
  `row["arm"] = None`; fails on the same assertion. Mutation: `by_arm["gap"]` computed as
  `ah - ab`; fails on `assert out["by_arm"]["gap"] == 3.0` (-3.0).
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "harness"))

from memory_gap import confirmed_l1, had_brief, report, runs  # noqa: E402

HEADING = "## Prior bugs\n"


def make_run(root, run_id, summary=None, prior=None, l1=None, verdicts=None, hosted=False,
             raw_summary=None, dispatch=("2026-10-30T12:00:00Z",)):
    d = root / "proj" / ".claude" / "audits" / "runs" / run_id
    d.mkdir(parents=True)
    if raw_summary is not None:
        (d / "summary.json").write_text(raw_summary)
    else:
        (d / "summary.json").write_text(json.dumps(
            {"mode": "DIFF", "tier": "STANDARD", "prior_block": None, "lessons_block": None,
             **(summary or {})}))
    for n, checked in enumerate(dispatch):
        (d / f"dispatch-{n}.json").write_text(json.dumps({"checked_utc": checked}))
    if prior is not None:
        (d / "local-prior.json").write_text(json.dumps({"status": "ok", "block": prior}))
    if hosted:
        (d / "prior-records.json").write_text("[]")
    if l1 is not None:
        (d / "L1.json").write_text(json.dumps(l1))
    if verdicts is not None:
        (d / "L2.5.json").write_text(json.dumps(
            [{"id": i, "verdict": v} for i, v in verdicts]))
    return d


def test_brief_is_read_from_the_receipt(tmp_path):
    bug = make_run(tmp_path, "a", prior=HEADING + "BUG x:1:BUG-001 [exact] a.py f: boom\n")
    lesson = make_run(tmp_path, "b", prior=HEADING + "LESSON L-1 [exact] keep it small\n")
    empty = make_run(tmp_path, "c", prior=HEADING)
    none = make_run(tmp_path, "d")
    assert had_brief(bug, {}) is True
    assert had_brief(lesson, {}) is True
    assert had_brief(empty, {}) is False
    assert had_brief(none, {}) is None


def test_hosted_brief_uses_the_block_counts(tmp_path):
    d = make_run(tmp_path, "a", hosted=True)
    prior = {"exact": 0, "symbol_changed": 0, "file_only": 1}
    assert had_brief(d, {"prior_block": prior, "lessons_block": 0}) is True
    zeros = {"exact": 0, "symbol_changed": 0, "file_only": 0}
    assert had_brief(d, {"prior_block": zeros, "lessons_block": 0}) is False
    assert had_brief(d, {"prior_block": zeros, "lessons_block": 2}) is True


def test_unknown_brief_is_none_not_false(tmp_path):
    bad = make_run(tmp_path, "a")
    (bad / "local-prior.json").write_text("{not json")
    down = make_run(tmp_path, "b")
    (down / "local-prior.json").write_text(json.dumps({"status": "unavailable", "block": ""}))
    empty = make_run(tmp_path, "c")
    (empty / "local-prior.json").write_text(json.dumps({"status": "empty", "block": HEADING}))
    hosted = make_run(tmp_path, "d", hosted=True)
    assert had_brief(bad, {}) is None
    assert had_brief(down, {}) is None
    assert had_brief(empty, {}) is False
    assert had_brief(hosted, {"prior_block": None, "lessons_block": None}) is None
    assert had_brief(hosted, {"prior_block": {"exact": 0}, "lessons_block": None}) is None
    assert had_brief(hosted, {"prior_block": {"exact": 0}, "lessons_block": 0}) is False


def test_unknown_brief_is_excluded_apart_from_no_receipt(tmp_path):
    d = make_run(tmp_path, "a", verdicts=[("BUG-1", "CONFIRMED")])
    (d / "local-prior.json").write_text(json.dumps({"status": "unavailable", "block": ""}))
    make_run(tmp_path, "b", verdicts=[("BUG-1", "CONFIRMED")])
    out = report(runs(tmp_path, "", ""))
    assert out["excluded"]["unknown_brief"] == 1 and out["excluded"]["no_receipt"] == 1
    assert out["n_none"] == 0


def test_only_confirmed_l1_findings_count(tmp_path):
    d = make_run(tmp_path, "a", verdicts=[("BUG-001", "CONFIRMED"), ("SEC-002", "FALSE_POSITIVE"),
                                          ("FIX-1", "CONFIRMED")])
    assert confirmed_l1(d) == 1
    assert confirmed_l1(make_run(tmp_path, "b")) is None


def test_a_clean_run_counts_zero(tmp_path):
    make_run(tmp_path, "a", prior=HEADING, l1=[])
    make_run(tmp_path, "b", prior=HEADING, l1=[{"id": "BUG-1"}])
    rows = {r["run_id"]: r for r in runs(tmp_path, "", "")}
    assert rows["a"]["confirmed"] == 0
    assert rows["a"]["brief"] is False
    assert rows["b"]["excluded"] == "no_verdicts"


def test_hunt_deferred_and_fast_are_excluded_and_counted(tmp_path):
    make_run(tmp_path, "a", {"mode": "HUNT", "tier": "DEEP"}, prior=HEADING, verdicts=[])
    make_run(tmp_path, "b", {"mode": "DEFERRED"}, prior=HEADING, verdicts=[])
    make_run(tmp_path, "c", {"tier": "FAST"}, prior=HEADING, verdicts=[])
    make_run(tmp_path, "d", prior=HEADING, verdicts=[("BUG-1", "CONFIRMED")])
    make_run(tmp_path, "e", verdicts=[])
    out = report(runs(tmp_path, "", ""))
    assert out["excluded"] == {"hunt": 1, "deferred": 1, "fast": 1, "no_receipt": 1,
                               "unknown_brief": 0, "no_verdicts": 0, "unreadable": 0,
                               "untimed": 0}
    assert out["n_none"] == 1 and out["n_brief"] == 0 and out["mean_none"] == 1.0


def test_a_summary_that_cannot_be_read_is_counted(tmp_path):
    make_run(tmp_path, "a", raw_summary="{not json")
    make_run(tmp_path, "b", {"mode": None})
    assert report(runs(tmp_path, "", ""))["excluded"]["unreadable"] == 2


def test_gap_is_brief_minus_none():
    rows = [{"run_id": str(i), "brief": b, "confirmed": c, "excluded": None}
            for i, (b, c) in enumerate([(True, 3), (True, 5), (False, 1), (False, 1)])]
    out = report(rows)
    assert out["gap"] == 3.0
    assert (out["n_brief"], out["n_none"]) == (2, 2)
    assert report([])["gap"] is None


def test_runs_deduplicates_by_run_id_and_honours_the_window(tmp_path):
    late, early = ("2026-10-30T12:00:00Z",), ("2026-10-29T12:00:00Z",)
    make_run(tmp_path / "x", "r1", prior=HEADING, verdicts=[], dispatch=late)
    make_run(tmp_path / "y", "r1", prior=HEADING, verdicts=[], dispatch=late)
    make_run(tmp_path, "r0", prior=HEADING, verdicts=[], dispatch=early)
    assert [r["run_id"] for r in runs(tmp_path, "2026-10-30T00:00:00Z", "")] == ["r1"]
    assert [r["run_id"] for r in runs(tmp_path, "", "2026-10-30T00:00:00Z")] == ["r0"]


def test_a_named_run_outside_the_bound_is_excluded(tmp_path):
    make_run(tmp_path, "cca-deep-20260906", prior=HEADING, verdicts=[],
             dispatch=("2026-09-06T10:00:00Z",))
    assert runs(tmp_path, "2026-10-28T20:01:05Z", "") == []


def test_a_named_run_inside_the_window_counts_by_its_dispatch_time(tmp_path):
    make_run(tmp_path, "pr529-520-20261031", prior=HEADING, verdicts=[],
             dispatch=("2026-10-31T10:49:33Z",))
    assert [r["run_id"] for r in runs(tmp_path, "2026-10-28T20:01:05Z", "")] == [
        "pr529-520-20261031"]
    assert runs(tmp_path, "", "2026-10-31T00:00:00Z") == []


def test_the_earliest_dispatch_time_is_the_run_time(tmp_path):
    make_run(tmp_path, "r", prior=HEADING, verdicts=[],
             dispatch=("2026-10-31T00:05:00Z", "2026-10-30T23:59:00Z"))
    assert [r["run_id"] for r in runs(tmp_path, "", "2026-10-31T00:00:00Z")] == ["r"]


def test_a_stamp_run_without_dispatch_uses_its_id(tmp_path):
    make_run(tmp_path, "20261030T085223Z", prior=HEADING, verdicts=[], dispatch=())
    assert [r["run_id"] for r in runs(tmp_path, "2026-10-30T00:00:00Z", "")] == [
        "20261030T085223Z"]
    assert runs(tmp_path, "2026-10-31T00:00:00Z", "") == []


def test_an_untimed_run_is_reported_not_counted(tmp_path):
    make_run(tmp_path, "mtrag-salvage-1", prior=HEADING, verdicts=[("BUG-1", "CONFIRMED")],
             dispatch=())
    rows = runs(tmp_path, "", "")
    assert len(rows) == 1 and rows[0]["excluded"] == "untimed"
    out = report(rows)
    assert out["excluded"]["untimed"] == 1
    assert (out["n_brief"], out["n_none"]) == (0, 0)
    assert out["mean_brief"] is None and out["mean_none"] is None


def test_a_bad_bound_is_an_error(tmp_path):
    make_run(tmp_path, "r", prior=HEADING, verdicts=[])
    with pytest.raises(SystemExit):
        runs(tmp_path, "yesterday", "")
    with pytest.raises(SystemExit):
        runs(tmp_path, "", "yesterday")


def test_gap_by_arm(tmp_path):
    """Invariant: `by_arm` compares runs by the arm their memory-arm.json assigned, brief minus
    holdout, whatever their brief held; a run with no arm file is left out of `by_arm` only."""
    for run_id, arm, confirmed in [("b1", "brief", 4), ("b2", "brief", 6), ("h1", "holdout", 2),
                                   ("h2", "holdout", 2), ("n1", None, 9)]:
        d = make_run(tmp_path, run_id, prior=HEADING,
                     verdicts=[(f"BUG-{n}", "CONFIRMED") for n in range(confirmed)])
        if arm is not None:
            (d / "memory-arm.json").write_text(json.dumps(
                {"run_id": run_id, "arm": arm, "rule": "int(sha256(run_id), 16) % 3 == 0"}))
    rows = {r["run_id"]: r for r in runs(tmp_path, "", "")}
    assert rows["h1"].get("arm") == "holdout" and rows["n1"].get("arm") is None
    out = report(list(rows.values()))
    assert "by_arm" in out
    assert out["by_arm"]["gap"] == 3.0
    assert out["by_arm"]["brief"] == {"n": 2, "mean": 5.0}
    assert out["by_arm"]["holdout"] == {"n": 2, "mean": 2.0}
    assert out["by_arm"]["excluded"] == {"no_arm": 1}
    assert out["n_none"] == 5 and out["excluded"]["hunt"] == 0
