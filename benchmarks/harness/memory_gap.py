"""Confirmed L1 findings per run, runs with a memory brief against runs without.

Input is the run directories under `.claude/audits/runs/<run_id>/` on this machine. A run has a
brief when its receipt shows at least one injected record: `local-prior.json` (a block with a
`BUG ...` or `LESSON ...` line) or, for a hosted run, `prior-records.json` (the summary's
`prior_block` and `lessons_block` counts). Only DIFF runs at STANDARD or DEEP tier are compared.
Confirmed means an L2.5 CONFIRMED verdict on an L1 finding id (l1_precision.PREFIX).

    python benchmarks/harness/memory_gap.py --root ~/Documents --since 2026-10-29T00:00:00Z

`--since` and `--until` take an ISO time or a stamp such as 20261029T000000Z. A run's time is its
earliest dispatch `checked_utc`, else its run id read as a stamp; a run with neither is reported as
excluded "untimed" and never counted.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
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


def _row(run_id: str, brief, confirmed, excluded) -> dict:
    return {"run_id": run_id, "brief": brief, "confirmed": confirmed, "excluded": excluded}


def _classify(run_id: str, run_dir: pathlib.Path) -> dict:
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
    if brief is None:
        has_receipt = (run_dir / "local-prior.json").exists() or (
            run_dir / "prior-records.json").exists()
        return _row(run_id, None, None, "unknown_brief" if has_receipt else "no_receipt")
    confirmed = confirmed_l1(run_dir)
    if confirmed is None:
        if (run_dir / "L1.json").exists() and _load(run_dir / "L1.json") == []:
            confirmed = 0
        else:
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
            out.append(_row(run_id, None, None, "untimed"))
            continue
        if (lo is not None and t < lo) or (hi is not None and t >= hi):
            continue
        seen.add(run_id)
        out.append(_classify(run_id, run_dir))
    return out


def _mean(values: list[int]) -> float | None:
    return round(sum(values) / len(values), 3) if values else None


def report(rows: list[dict]) -> dict:
    used = [r for r in rows if not r.get("excluded")]
    brief = [r["confirmed"] for r in used if r["brief"]]
    none = [r["confirmed"] for r in used if not r["brief"]]
    mb, mn = _mean(brief), _mean(none)
    excluded = {reason: 0 for reason in REASONS}
    for r in rows:
        if r.get("excluded"):
            excluded[r["excluded"]] += 1
    return {"n_brief": len(brief), "n_none": len(none), "mean_brief": mb, "mean_none": mn,
            "gap": round(mb - mn, 3) if mb is not None and mn is not None else None,
            "excluded": excluded}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path.home() / "Documents")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    a = ap.parse_args()
    print(json.dumps(report(runs(a.root.expanduser(), a.since, a.until)), indent=1))


if __name__ == "__main__":
    main()
