"""Confirmed L1 findings per run, runs with a memory brief against runs without.

Input is the run directories under `.claude/audits/runs/<run_id>/` on this machine. A run has a
brief when its receipt shows at least one injected record: `local-prior.json` (a block with a
`BUG ...` or `LESSON ...` line) or, for a hosted run, `prior-records.json` (the summary's
`prior_block` and `lessons_block` counts). Only DIFF runs at STANDARD or DEEP tier are compared.
Confirmed means an L2.5 CONFIRMED verdict on an L1 finding id (l1_precision.PREFIX).

    python benchmarks/harness/memory_gap.py --root ~/Documents --since 2026-10-29T00:00:00Z

Each row also carries `arm`, the memory holdout arm `memory-arm.json` assigned (`brief`,
`holdout`, or None without a readable file naming the run), and the report's `by_arm` compares
the arms by assignment: brief minus holdout, whatever a brief arm run's brief happened to hold.
`by_arm` therefore reads the receipt not at all: a run whose brief cannot be read (top-level
`unknown_brief` or `no_receipt`) still counts in its arm when it has an arm and a confirmed count.
Its own `excluded` table names why a run is out of the arm comparison (hunt, deferred, fast,
untimed, no_verdicts, unreadable, no_arm). `by_arm.interval` is the Welch 95% interval of brief
mean minus holdout mean from the per-run counts, null when either arm has fewer than 2 runs. Its
t quantile is the two-sided 95% Student t value from a table (`T975`) at the largest tabled
degrees of freedom not above the Welch-Satterthwaite value, so the interval is never narrower
than the exact one.

`--root` may be given more than once: roots are read in the order given, and the first row seen
for a run id wins (the pre-registration's "rows merged by run_id, first seen kept"). `--rows`
prints, instead of the report, one JSON object per run included in `by_arm` (`run_id`, `arm`,
`brief`, `confirmed`, `root`), one per line.

`--since` and `--until` take an ISO time or a stamp such as 20261029T000000Z. A run's time is its
earliest dispatch `checked_utc`, else its run id read as a stamp; a run with neither is reported as
excluded "untimed" and never counted.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import statistics
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from l1_precision import PREFIX, moment  # noqa: E402

BRIEF_LINE = re.compile(r"^(BUG|LESSON) ", re.MULTILINE)
REASONS = ("hunt", "deferred", "fast", "no_receipt", "unknown_brief", "no_verdicts",
           "unreadable", "untimed")


def _load(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def had_brief(run_dir: pathlib.Path, summary: dict) -> bool | None:
    """True or False when the receipt shows whether records were injected, else None.

    None means unknown: no receipt, an unreadable one, a reader status other than ok or empty,
    or hosted counts that are not all present. Unknown is never counted as "no brief".
    """
    local = run_dir / "local-prior.json"
    if local.exists():
        data = _load(local)
        if not isinstance(data, dict) or data.get("status") not in ("ok", "empty"):
            return None
        block = data.get("block")
        return bool(BRIEF_LINE.search(block)) if isinstance(block, str) else None
    if (run_dir / "prior-records.json").exists():
        prior, lessons = summary.get("prior_block"), summary.get("lessons_block")
        if not isinstance(prior, dict) or not all(isinstance(v, int) for v in prior.values()):
            return None
        if not isinstance(lessons, int):
            return None
        return sum(prior.values()) + lessons > 0
    return None


def confirmed_l1(run_dir: pathlib.Path) -> int | None:
    """CONFIRMED verdicts on L1 finding ids; None when the run stored no L2.5 output."""
    path = run_dir / "L2.5.json"
    if not path.exists():
        return None
    data = _load(path)
    if not isinstance(data, list):
        return None
    return sum(1 for v in data
               if isinstance(v, dict) and v.get("verdict") == "CONFIRMED"
               and str(v.get("id", "")).split("-")[0].upper() in PREFIX)


ARMS = ("brief", "holdout")
ARM_REASONS = ("hunt", "deferred", "fast", "untimed", "no_verdicts", "unreadable", "no_arm")


def memory_arm(run_dir: pathlib.Path) -> str | None:
    """The arm in the run's memory-arm.json; None when absent, unreadable or another run's."""
    data = _load(run_dir / "memory-arm.json")
    if not isinstance(data, dict) or data.get("run_id") != run_dir.name:
        return None
    arm = data.get("arm")
    return arm if arm in ARMS else None


def _row(run_id: str, brief, confirmed, excluded, arm=None) -> dict:
    return {"run_id": run_id, "brief": brief, "confirmed": confirmed, "excluded": excluded,
            "arm": arm}


def _confirmed(run_dir: pathlib.Path) -> int | None:
    """Confirmed L1 findings, 0 for a run whose stored L1 is empty, None without verdicts."""
    confirmed = confirmed_l1(run_dir)
    if confirmed is None and (run_dir / "L1.json").exists() and _load(run_dir / "L1.json") == []:
        return 0
    return confirmed


def _classify(run_id: str, run_dir: pathlib.Path) -> dict:
    row = _classify_brief(run_id, run_dir)
    row["arm"] = memory_arm(run_dir)
    return row


def _classify_brief(run_id: str, run_dir: pathlib.Path) -> dict:
    summary = _load(run_dir / "summary.json")
    if not isinstance(summary, dict):
        return _row(run_id, None, None, "unreadable")
    mode, tier = summary.get("mode"), summary.get("tier")
    if mode == "HUNT":
        return _row(run_id, None, None, "hunt")
    if mode == "DEFERRED":
        return _row(run_id, None, None, "deferred")
    if mode != "DIFF" or tier not in ("FAST", "STANDARD", "DEEP"):
        return _row(run_id, None, None, "unreadable")
    if tier == "FAST":
        return _row(run_id, None, None, "fast")
    brief = had_brief(run_dir, summary)
    confirmed = _confirmed(run_dir)
    if brief is None:
        has_receipt = (run_dir / "local-prior.json").exists() or (
            run_dir / "prior-records.json").exists()
        # Still carries its count: by_arm compares by assigned arm, whatever the receipt says.
        return _row(run_id, None, confirmed, "unknown_brief" if has_receipt else "no_receipt")
    if confirmed is None:
        return _row(run_id, brief, None, "no_verdicts")
    return _row(run_id, brief, confirmed, None)


def run_time(run_id: str, run_dir: pathlib.Path):
    """Earliest dispatch `checked_utc`, else the run id as a stamp, else None."""
    times = []
    for path in sorted(run_dir.glob("dispatch-*.json")):
        data = _load(path)
        t = moment(data.get("checked_utc")) if isinstance(data, dict) else None
        if t is not None:
            times.append(t)
    return min(times) if times else moment(run_id)


def _bound(value: str, name: str):
    if not value:
        return None
    parsed = moment(value)
    if parsed is None:
        raise SystemExit(
            f"{name} takes an ISO time or a stamp like 20261028T200105Z, got {value!r}")
    return parsed


def runs(root: pathlib.Path, since: str, until: str) -> list[dict]:
    """One row per run id, deduplicated (worktrees copy runs). `excluded` names why a run is out.

    The window is on the run's time, `since <= t < until`; an empty bound is open. A run with no
    time is returned as an "untimed" row and is never inside the window.
    """
    lo, hi = _bound(since, "--since"), _bound(until, "--until")
    seen, out = set(), []
    for path in sorted(root.rglob("summary.json")):
        run_dir = path.parent
        if "node_modules" in path.parts or run_dir.parent.name != "runs":
            continue
        run_id = run_dir.name
        if run_id in seen:
            continue
        t = run_time(run_id, run_dir)
        if t is None:
            seen.add(run_id)
            out.append({**_row(run_id, None, None, "untimed"), "root": str(root)})
            continue
        if (lo is not None and t < lo) or (hi is not None and t >= hi):
            continue
        seen.add(run_id)
        out.append({**_classify(run_id, run_dir), "root": str(root)})
    return out


def runs_over(roots: list[pathlib.Path], since: str, until: str) -> list[dict]:
    """`runs` over each root in order, merged by run id with the first row seen kept."""
    seen, out = set(), []
    for root in roots:
        for row in runs(root, since, until):
            if row["run_id"] not in seen:
                seen.add(row["run_id"])
                out.append(row)
    return out


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


#: Two-sided 95% Student t quantiles (the 0.975 point) by degrees of freedom; math.inf is the
#: normal value. Standard table values, to three decimals.
T975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
        9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120,
        17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 21: 2.080, 22: 2.074, 23: 2.069,
        24: 2.064, 25: 2.060, 26: 2.056, 27: 2.052, 28: 2.048, 29: 2.045, 30: 2.042,
        40: 2.021, 60: 2.000, 120: 1.980, math.inf: 1.960}


def _t975(df: float) -> float:
    """The tabled quantile at the largest tabled df not above `df` (never below 1)."""
    return T975[max(k for k in T975 if k <= max(df, 1))]


def welch_interval(a: list[int], b: list[int]) -> dict | None:
    """Welch 95% interval of mean(a) minus mean(b): {"low", "high", "df", "t"}; None when either
    side has fewer than 2 values. With no spread on either side the interval is the point and
    `df` is None."""
    if len(a) < 2 or len(b) < 2:
        return None
    va, vb = statistics.variance(a) / len(a), statistics.variance(b) / len(b)
    diff = statistics.fmean(a) - statistics.fmean(b)
    se = math.sqrt(va + vb)
    if se == 0:
        return {"low": round(diff, 3), "high": round(diff, 3), "df": None, "t": None}
    df = (va + vb) ** 2 / (va ** 2 / (len(a) - 1) + vb ** 2 / (len(b) - 1))
    t = _t975(df)
    return {"low": round(diff - t * se, 3), "high": round(diff + t * se, 3),
            "df": round(df, 3), "t": t}


#: Top-level reasons that say only that the receipt could not be read; by_arm ignores them.
_RECEIPT_ONLY = (None, "unknown_brief", "no_receipt")


def arm_reason(row: dict) -> str | None:
    """Why `row` is out of the arm comparison, or None when it is in. By assigned arm: the
    brief receipt is not read, so a brief arm run whose reader was unavailable still counts."""
    if row.get("excluded") not in _RECEIPT_ONLY:
        return row["excluded"]
    if row.get("confirmed") is None:
        return "no_verdicts"
    return None if row.get("arm") in ARMS else "no_arm"


def arm_rows(rows: list[dict]) -> list[dict]:
    """The rows `by_arm` counts."""
    return [r for r in rows if arm_reason(r) is None]


def report(rows: list[dict]) -> dict:
    used = [r for r in rows if not r.get("excluded")]
    brief = [r["confirmed"] for r in used if r["brief"]]
    none = [r["confirmed"] for r in used if not r["brief"]]
    mb, mn = _mean(brief), _mean(none)
    excluded = {reason: 0 for reason in REASONS}
    for r in rows:
        if r.get("excluded"):
            excluded[r["excluded"]] += 1
    by_arm: dict = {}
    included = arm_rows(rows)
    values = {arm: [r["confirmed"] for r in included if r["arm"] == arm] for arm in ARMS}
    for arm in ARMS:
        by_arm[arm] = {"n": len(values[arm]), "mean": _mean(values[arm])}
    ab, ah = by_arm["brief"]["mean"], by_arm["holdout"]["mean"]
    by_arm["gap"] = round(ab - ah, 3) if ab is not None and ah is not None else None
    by_arm["interval"] = welch_interval(values["brief"], values["holdout"])
    # Why each run is out of the arm comparison; `no_arm` runs still count in n_brief and n_none.
    arm_excluded = {reason: 0 for reason in ARM_REASONS}
    for r in rows:
        reason = arm_reason(r)
        if reason is not None:
            arm_excluded[reason] += 1
    by_arm["excluded"] = arm_excluded
    return {"n_brief": len(brief), "n_none": len(none), "mean_brief": mb, "mean_none": mn,
            "gap": round(mb - mn, 3) if mb is not None and mn is not None else None,
            "excluded": excluded, "by_arm": by_arm}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path, action="append",
                    help="repeatable; read in order, first row per run id kept "
                         "(default ~/Documents)")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--rows", action="store_true",
                    help="print one JSON object per run in by_arm instead of the report")
    a = ap.parse_args(argv)
    roots = [r.expanduser() for r in (a.root or [pathlib.Path.home() / "Documents"])]
    rows = runs_over(roots, a.since, a.until)
    if a.rows:
        for r in arm_rows(rows):
            print(json.dumps({k: r[k] for k in ("run_id", "arm", "brief", "confirmed", "root")}))
        return
    print(json.dumps(report(rows), indent=1))


if __name__ == "__main__":
    main()
