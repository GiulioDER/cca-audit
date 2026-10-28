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
- test_gap_is_brief_minus_none: report computes `mn - mb`; fails on `out["gap"] == 3.0` (-3.0).
- test_runs_deduplicates_by_run_id_and_honours_the_window: `or run_id in seen` removed from runs;
  fails on `== ["r1"]` (["r1", "r1"]).
"""
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "harness"))

from memory_gap import confirmed_l1, had_brief, report, runs  # noqa: E402

HEADING = "## Prior bugs\n"


def make_run(root, run_id, summary=None, prior=None, l1=None, verdicts=None, hosted=False,
             raw_summary=None):
    d = root / "proj" / ".claude" / "audits" / "runs" / run_id
    d.mkdir(parents=True)
    if raw_summary is not None:
        (d / "summary.json").write_text(raw_summary)
    else:
        (d / "summary.json").write_text(json.dumps(
            {"mode": "DIFF", "tier": "STANDARD", "prior_block": None, "lessons_block": None,
             **(summary or {})}))
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


def test_only_confirmed_l1_findings_count(tmp_path):
    d = make_run(tmp_path, "a", verdicts=[("BUG-001", "CONFIRMED"), ("SEC-002", "FALSE_POSITIVE"),
                                          ("FIX-1", "CONFIRMED")])
    assert confirmed_l1(d) == 1
    assert confirmed_l1(make_run(tmp_path, "b")) is None


def test_a_clean_run_counts_zero(tmp_path):
    make_run(tmp_path, "a", prior=HEADING, l1=[])
    make_run(tmp_path, "b", prior=HEADING, l1=[{"id": "BUG-1"}])
    rows = {r["run_id"]: r for r in runs(tmp_path, "", "~")}
    assert rows["a"]["confirmed"] == 0
    assert rows["a"]["brief"] is False
    assert rows["b"]["excluded"] == "no_verdicts"


def test_hunt_deferred_and_fast_are_excluded_and_counted(tmp_path):
    make_run(tmp_path, "a", {"mode": "HUNT", "tier": "DEEP"}, prior=HEADING, verdicts=[])
    make_run(tmp_path, "b", {"mode": "DEFERRED"}, prior=HEADING, verdicts=[])
    make_run(tmp_path, "c", {"tier": "FAST"}, prior=HEADING, verdicts=[])
    make_run(tmp_path, "d", prior=HEADING, verdicts=[("BUG-1", "CONFIRMED")])
    make_run(tmp_path, "e", verdicts=[])
    out = report(runs(tmp_path, "", "~"))
    assert out["excluded"] == {"hunt": 1, "deferred": 1, "fast": 1, "no_receipt": 1,
                               "no_verdicts": 0, "unreadable": 0}
    assert out["n_none"] == 1 and out["n_brief"] == 0 and out["mean_none"] == 1.0


def test_a_summary_that_cannot_be_read_is_counted(tmp_path):
    make_run(tmp_path, "a", raw_summary="{not json")
    make_run(tmp_path, "b", {"mode": None})
    assert report(runs(tmp_path, "", "~"))["excluded"]["unreadable"] == 2


def test_gap_is_brief_minus_none():
    rows = [{"run_id": str(i), "brief": b, "confirmed": c, "excluded": None}
            for i, (b, c) in enumerate([(True, 3), (True, 5), (False, 1), (False, 1)])]
    out = report(rows)
    assert out["gap"] == 3.0
    assert (out["n_brief"], out["n_none"]) == (2, 2)
    assert report([])["gap"] is None


def test_runs_deduplicates_by_run_id_and_honours_the_window(tmp_path):
    make_run(tmp_path / "x", "r1", prior=HEADING, verdicts=[])
    make_run(tmp_path / "y", "r1", prior=HEADING, verdicts=[])
    make_run(tmp_path, "r0", prior=HEADING, verdicts=[])
    assert [r["run_id"] for r in runs(tmp_path, "r1", "~")] == ["r1"]
    assert [r["run_id"] for r in runs(tmp_path, "", "r1")] == ["r0"]
