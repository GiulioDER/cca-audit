"""Confirmed L1 findings per run, runs with a memory brief against runs without.

Input is the run directories under `.claude/audits/runs/<run_id>/` on this machine. A run has a
brief when its receipt shows at least one injected record: `local-prior.json` (a block with a
`BUG ...` or `LESSON ...` line) or, for a hosted run, `prior-records.json` (the summary's
`prior_block` and `lessons_block` counts). Only DIFF runs at STANDARD or DEEP tier are compared.
Confirmed means an L2.5 CONFIRMED verdict on an L1 finding id (l1_precision.PREFIX).

    python benchmarks/harness/memory_gap.py --root ~/Documents \n        --since 20261029T000000Z [--until ...]
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from l1_precision import PREFIX  # noqa: E402

BRIEF_LINE = re.compile(r"^(BUG|LESSON) ", re.MULTILINE)
REASONS = ("hunt", "deferred", "fast", "no_receipt", "no_verdicts", "unreadable")


def _load(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except (OSError, ValueError):
        return None


def had_brief(run_dir: pathlib.Path, summary: dict) -> bool | None:
    """True when the run's receipt shows an injected record, None when no receipt exists."""
    local = run_dir / "local-prior.json"
    if local.exists():
        data = _load(local)
        block = data.get("block") if isinstance(data, dict) else None
        return bool(BRIEF_LINE.search(block)) if isinstance(block, str) else False
    if (run_dir / "prior-records.json").exists():
        prior = summary.get("prior_block")
        values = prior.values() if isinstance(prior, dict) else []
        counts = [v for v in values if isinstance(v, int)]
        lessons = summary.get("lessons_block")
        return sum(counts) + (lessons if isinstance(lessons, int) else 0) > 0
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
        return _row(run_id, None, None, "no_receipt")
    confirmed = confirmed_l1(run_dir)
    if confirmed is None:
        if (run_dir / "L1.json").exists() and _load(run_dir / "L1.json") == []:
            confirmed = 0
        else:
            return _row(run_id, brief, None, "no_verdicts")
    return _row(run_id, brief, confirmed, None)


def runs(root: pathlib.Path, since: str, until: str) -> list[dict]:
    """One row per run id, deduplicated (worktrees copy runs). `excluded` names why a run is out."""
    seen, out = set(), []
    for path in sorted(root.rglob("summary.json")):
        run_dir = path.parent
        if "node_modules" in path.parts or run_dir.parent.name != "runs":
            continue
        run_id = run_dir.name
        if not since <= run_id < until or run_id in seen:
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
    ap.add_argument("--until", default="~")
    a = ap.parse_args()
    print(json.dumps(report(runs(a.root.expanduser(), a.since, a.until)), indent=1))


if __name__ == "__main__":
    main()
