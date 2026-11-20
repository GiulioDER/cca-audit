"""The memory holdout's secondary metrics, brief arm against holdout arm.

Pre-registered in docs/preregistrations/2026-11-20-memory-holdout-secondary.md, and read only at
the looks of docs/preregistrations/2026-10-31-auditor-memory-holdout.md. The runs and arms are
exactly memory_gap's `by_arm` set: `memory_gap.runs_over`, then `memory_gap.arm_rows`.

- S1 false positives per run: L1 rows of the run's L2.5.json with verdict FALSE_POSITIVE.
- S2 L1 precision per arm: CONFIRMED over CONFIRMED plus FALSE_POSITIVE, summed over the arm's
  runs, with a Wilson 95% interval; every other verdict is counted in `other_verdicts`.
- S3 L1 findings verified per run: every L1 row of L2.5.json, whatever its verdict.
- S4 wall minutes per run: summary.json `completed_utc` minus `memory_gap.run_time`. A run without
  either is out of S4 only, counted in `s4_untimed`.

S1, S3 and S4 carry the gap (brief mean minus holdout mean) and its Welch 95% interval
(`memory_gap.welch_interval`). An L1 row is one whose id prefix is in `l1_precision.PREFIX`.

    python benchmarks/harness/memory_secondary.py --root ~/Documents --root ~/.codex/worktrees \\
        --since 2026-11-04T12:58:53Z

`--run DIR` prints one run's metrics, with no arm, to check the apparatus on a known run.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from interval import wilson  # noqa: E402
from l1_precision import PREFIX, moment  # noqa: E402
from memory_gap import (  # noqa: E402
    ARM_REASONS,
    ARMS,
    _load,
    _mean,
    arm_reason,
    arm_rows,
    run_time,
    runs_over,
    welch_interval,
)


def l1_verdicts(run_dir: pathlib.Path) -> list[str] | None:
    """Verdicts on L1 ids in L2.5.json; [] for a run whose stored L1 is empty (memory_gap's
    clean run); None when the run has no L2.5 output to read."""
    data = _load(run_dir / "L2.5.json")
    if isinstance(data, list):
        return [str(v.get("verdict")) for v in data
                if isinstance(v, dict) and str(v.get("id", "")).split("-")[0].upper() in PREFIX]
    return [] if _load(run_dir / "L1.json") == [] else None


def wall_minutes(run_dir: pathlib.Path) -> float | None:
    summary = _load(run_dir / "summary.json")
    end = moment(summary.get("completed_utc")) if isinstance(summary, dict) else None
    start = run_time(run_dir.name, run_dir)
    if end is None or start is None:
        return None
    return round((end - start).total_seconds() / 60, 1)


def run_metrics(run_dir: pathlib.Path) -> dict | None:
    verdicts = l1_verdicts(run_dir)
    if verdicts is None:
        return None
    counts = collections.Counter(verdicts)
    other = {v: n for v, n in counts.items() if v not in ("CONFIRMED", "FALSE_POSITIVE")}
    return {"fp": counts["FALSE_POSITIVE"], "confirmed": counts["CONFIRMED"],
            "verified": len(verdicts), "other": other, "minutes": wall_minutes(run_dir)}


def _arm_summary(metrics: list[dict]) -> dict:
    confirmed = sum(m["confirmed"] for m in metrics)
    decided = confirmed + sum(m["fp"] for m in metrics)
    other: collections.Counter = collections.Counter()
    for m in metrics:
        other.update(m["other"])
    minutes = [m["minutes"] for m in metrics if m["minutes"] is not None]
    return {"n": len(metrics),
            "fp_per_run": _mean([m["fp"] for m in metrics]),
            "verified_per_run": _mean([m["verified"] for m in metrics]),
            "minutes_per_run": _mean(minutes),
            "s4_untimed": len(metrics) - len(minutes),
            "decided": decided,
            "precision": round(confirmed / decided, 3) if decided else None,
            "precision_interval": [round(x, 3) for x in wilson(confirmed, decided)]
            if decided else None,
            "other_verdicts": dict(other)}


def _gap(metrics: dict, key: str) -> dict:
    a, b = ([m[key] for m in metrics[arm] if m[key] is not None] for arm in ARMS)
    gap = round(statistics.fmean(a) - statistics.fmean(b), 3) if a and b else None
    return {"gap": gap, "interval": welch_interval(a, b)}


def report(rows: list[dict]) -> dict:
    # arm_rows keeps only runs with a confirmed count, so each has verdicts to read.
    metrics = {arm: [run_metrics(pathlib.Path(r["dir"])) for r in arm_rows(rows) if r["arm"] == arm]
               for arm in ARMS}
    out: dict = {arm: _arm_summary(metrics[arm]) for arm in ARMS}
    out["s1_fp_per_run"] = _gap(metrics, "fp")
    out["s3_verified_per_run"] = _gap(metrics, "verified")
    out["s4_minutes"] = _gap(metrics, "minutes")
    out["excluded"] = {reason: sum(1 for r in rows if arm_reason(r) == reason)
                       for reason in ARM_REASONS}
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path, action="append",
                    help="repeatable; read in order, first row per run id kept "
                         "(default ~/Documents)")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--run", type=pathlib.Path, help="print one run directory's metrics only")
    a = ap.parse_args(argv)
    if a.run is not None:
        metrics = run_metrics(a.run.expanduser())
        if metrics is None:
            raise SystemExit(f"{a.run} has no L2.5 verdicts and no empty L1")
        print(json.dumps(metrics))
        return
    roots = [r.expanduser() for r in (a.root or [pathlib.Path.home() / "Documents"])]
    print(json.dumps(report(runs_over(roots, a.since, a.until)), indent=1))


if __name__ == "__main__":
    main()
