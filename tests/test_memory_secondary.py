"""Tests for benchmarks/harness/memory_secondary.py (pre-registration
docs/preregistrations/2026-11-20-memory-holdout-secondary.md).

Red proof, 2026-11-20 (the module is new, so each test was run alone against one mutation of the
production line it protects, then the file was restored; every failure was the intended assertion):

- test_only_l1_ids_count: `l1_verdicts` loses its `in PREFIX` condition; fails on
  `(m["fp"], m["verified"], m["confirmed"]) == (1, 3, 1)` ((2, 4, 1), the FIX- row counted).
- test_a_clean_run_counts_zero_and_no_verdicts_is_none: the `L1.json == []` fallback of
  `l1_verdicts` returns None; fails on `m is not None`.
- test_wall_minutes_run_from_the_start_to_completion: `wall_minutes` computes `start - end`;
  fails on `wall_minutes(done) == 90.0` (-90.0).
- test_runs_rows_carry_their_directory: `memory_gap.runs` sets `"dir": str(root)`; fails on the
  `== [str(d)]` comparison.
- test_report_compares_by_assigned_arm: three mutations, each alone: (a) `_gap` computes holdout
  minus brief, fails on the S1 gap (1.0 == -1.0); (b) other verdicts added to the precision
  denominator, fails on `precision == 0.833` (0.714); (c) `report` reads every row with a `dir`
  instead of `arm_rows`, fails on `out["brief"]["n"] == 2` (3, the HUNT run counted).
- test_s4_leaves_out_runs_without_a_time: two mutations, each alone: (a) `_arm_summary` reads a
  missing time as 0, fails on `s4_untimed == 1` (0); (b) `_gap` reads a missing time as 0, fails
  on the S4 gap (50.0 == 30.0).
- test_run_option_checks_one_known_run: the no-verdicts guard in `main` removed; fails with
  `DID NOT RAISE SystemExit`.
"""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "benchmarks" / "harness"))

from memory_gap import runs  # noqa: E402
from memory_secondary import main, report, run_metrics, wall_minutes  # noqa: E402
from test_memory_gap import HEADING, make_run  # noqa: E402


def arm_run(root, run_id, arm, verdicts, summary=None):
    d = make_run(root, run_id, prior=HEADING, verdicts=verdicts, summary=summary)
    (d / "memory-arm.json").write_text(json.dumps({"run_id": run_id, "arm": arm, "rule": "test"}))
    return d


def test_only_l1_ids_count(tmp_path):
    """Invariant: S1 and S3 count L2.5 rows on L1 finding ids only; a FIX- row is not an L1
    finding. S2's precision counts CONFIRMED and FALSE_POSITIVE; other verdicts stand apart."""
    d = make_run(tmp_path, "a", verdicts=[("BUG-1", "CONFIRMED"), ("BUG-2", "FALSE_POSITIVE"),
                                          ("FIX-1", "FALSE_POSITIVE"), ("PERF-1", "UNCERTAIN")])
    m = run_metrics(d)
    assert (m["fp"], m["verified"], m["confirmed"]) == (1, 3, 1)
    assert m["other"] == {"UNCERTAIN": 1}


def test_a_clean_run_counts_zero_and_no_verdicts_is_none(tmp_path):
    """Invariant: a run whose stored L1 is empty counts 0 everywhere, as memory_gap counts it;
    a run with L1 findings but no L2.5 output has no metrics at all."""
    clean = make_run(tmp_path, "a", l1=[])
    pending = make_run(tmp_path, "b", l1=[{"id": "BUG-1"}])
    m = run_metrics(clean)
    assert m is not None and (m["fp"], m["verified"], m["confirmed"]) == (0, 0, 0)
    assert run_metrics(pending) is None


def test_wall_minutes_run_from_the_start_to_completion(tmp_path):
    """Invariant: S4 is completed_utc minus memory_gap.run_time (the earliest dispatch here);
    a run without completed_utc has no S4."""
    done = make_run(tmp_path, "a", summary={"completed_utc": "2026-10-30T13:30:00Z"},
                    dispatch=("2026-10-30T12:30:00Z", "2026-10-30T12:00:00Z"))
    open_run = make_run(tmp_path, "b")
    assert wall_minutes(done) == 90.0
    assert wall_minutes(open_run) is None


def test_runs_rows_carry_their_directory(tmp_path):
    """Invariant: each memory_gap row names its run directory, which is where these metrics
    are read from; the root alone does not locate a run."""
    d = make_run(tmp_path, "a", verdicts=[("BUG-1", "CONFIRMED")])
    assert [r["dir"] for r in runs(tmp_path, "", "")] == [str(d)]


def test_report_compares_by_assigned_arm(tmp_path):
    """Invariant: every metric is brief arm minus holdout arm, over memory_gap's by_arm runs
    only; a HUNT run is out and counted."""
    c, f, u = "CONFIRMED", "FALSE_POSITIVE", "UNCERTAIN"
    end = {"completed_utc": "2026-10-30T13:00:00Z"}
    arm_run(tmp_path, "b1", "brief", [("BUG-1", c), ("BUG-2", c), ("BUG-3", f)], end)
    arm_run(tmp_path, "b2", "brief", [("BUG-1", c), ("BUG-2", c), ("BUG-3", c), ("BUG-4", u)], end)
    arm_run(tmp_path, "h1", "holdout", [("BUG-1", f), ("BUG-2", f), ("BUG-3", c)])
    arm_run(tmp_path, "h2", "holdout", [("BUG-1", f), ("BUG-2", c)])
    arm_run(tmp_path, "x1", "brief", [("BUG-1", f)], {"mode": "HUNT"})
    out = report(runs(tmp_path, "", ""))
    assert out["brief"]["n"] == 2 and out["holdout"]["n"] == 2
    assert out["s1_fp_per_run"]["gap"] == -1.0
    assert out["s3_verified_per_run"]["gap"] == 1.0
    assert out["brief"]["precision"] == 0.833 and out["holdout"]["precision"] == 0.4
    assert out["brief"]["other_verdicts"] == {"UNCERTAIN": 1}
    assert out["excluded"]["hunt"] == 1


def test_s4_leaves_out_runs_without_a_time(tmp_path):
    """Invariant: a run with no completed_utc is out of S4 only, counted, and never read as
    zero minutes."""
    c = "CONFIRMED"
    for run_id, arm, end in [("b1", "brief", "2026-10-30T13:00:00Z"),
                             ("b2", "brief", "2026-10-30T14:00:00Z"),
                             ("h1", "holdout", "2026-10-30T12:30:00Z"),
                             ("h2", "holdout", "2026-10-30T13:30:00Z"),
                             ("h3", "holdout", None)]:
        arm_run(tmp_path, run_id, arm, [("BUG-1", c)], {"completed_utc": end})
    out = report(runs(tmp_path, "", ""))
    assert out["holdout"]["n"] == 3 and out["holdout"]["s4_untimed"] == 1
    assert out["holdout"]["minutes_per_run"] == 60.0
    assert out["s4_minutes"]["gap"] == 30.0


def test_run_option_checks_one_known_run(tmp_path, capsys):
    """Invariant: --run prints one run's metrics with no arm, and refuses a run with no
    verdicts rather than printing nothing as zero."""
    d = make_run(tmp_path, "a", verdicts=[("BUG-1", "CONFIRMED")],
                 summary={"completed_utc": "2026-10-30T12:10:00Z"})
    main(["--run", str(d)])
    printed = json.loads(capsys.readouterr().out)
    assert (printed["fp"], printed["verified"], printed["minutes"]) == (0, 1, 10.0)
    with pytest.raises(SystemExit):
        main(["--run", str(make_run(tmp_path, "b", l1=[{"id": "BUG-1"}]))])
