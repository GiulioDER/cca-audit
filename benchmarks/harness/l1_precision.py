"""L1 auditor precision from the disposition ledgers (AUDITOR_SCORECARD.jsonl) on this machine.

Pre-registered use: docs/preregistrations/2026-10-28-l1-auditor-instructions.md. Precision is
CONFIRMED / (CONFIRMED + FALSE_POSITIVE) over L2.5 verdict rows, an fp-check judgement rather than
ground truth. Rows are deduplicated by (run_id, finding_id), because worktrees copy a ledger.

    python benchmarks/harness/l1_precision.py --root ~/Documents \
        [--since 2026-10-28T20:01:05Z] [--until ...] [--first 15] [--by-id]

A run's time is its earliest verdict row's `ts`. `--since` and `--until` take an ISO time or a
run-id stamp (`20261028T200105Z`) and bound that time; `--first N` keeps the N earliest runs in
the window. Until 2026-10-30 the bounds compared run ids as text, so a run named rather than
stamped (`pr659-650-20261029`, `cca-deep-20260906`) passed any `--since`: the first count of the
15-run look took in six runs from August to 2026-10-12. `--by-id` keeps that old comparison, which
is how the pre-registered baseline was computed (`--until 20261029T000000Z`, which excludes every
named id), so the baseline stays reproducible.
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib
import re
from datetime import datetime, timezone

PREFIX = {"STAKES": "stakes", "DAT": "data", "NUM": "numeric", "DEPLOY": "deploy", "DEP": "dep",
          "SEC": "security", "BUG": "bug", "CODE": "code", "PERF": "perf", "DOC": "doc",
          "ENV": "env"}
_STAMP = re.compile(r"(\d{8}T\d{6})Z")


def moment(value: object) -> datetime | None:
    """An aware UTC time from an ISO string or a run-id stamp; None when neither parses."""
    text = str(value or "")
    stamp = _STAMP.fullmatch(text)
    if stamp:
        return datetime.strptime(stamp.group(1), "%Y%m%dT%H%M%S").replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.astimezone(timezone.utc) if parsed.tzinfo else None


def _all_rows(root: pathlib.Path) -> list[dict]:
    seen, out = set(), []
    for path in root.rglob("AUDITOR_SCORECARD.jsonl"):
        if "node_modules" in path.parts:
            continue
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if not isinstance(row, dict) or "verdict" not in row:
                continue
            key = (str(row.get("run_id") or ""), row.get("finding_id"))
            if key not in seen:
                seen.add(key)
                out.append(row)
    return out


def run_times(found: list[dict]) -> dict[str, datetime | None]:
    """Each run's earliest verdict row time; None for a run with no parseable `ts`."""
    times: dict[str, datetime | None] = {}
    for row in found:
        run = str(row.get("run_id") or "")
        t = moment(row.get("ts"))
        if run not in times or (t is not None and (times[run] is None or t < times[run])):
            times[run] = t
    return times


def window(found: list[dict], since: str = "", until: str = "", first: int | None = None,
           by_id: bool = False) -> tuple[list[dict], dict]:
    """(the rows of the runs in the window, a note of which runs were left out and why)."""
    if by_id:
        upper = until or "~"
        kept = [r for r in found if since <= str(r.get("run_id") or "") < upper]
        return kept, {"mode": "by-id"}
    until = "" if until == "~" else until  # the old "no upper bound" spelling
    lo, hi = moment(since) if since else None, moment(until) if until else None
    if (since and lo is None) or (until and hi is None):
        raise ValueError("--since and --until take an ISO time or a stamp like 20261028T200105Z")
    times = run_times(found)
    untimed = sorted(r for r, t in times.items() if t is None)
    runs = sorted((r for r, t in times.items() if t is not None
                   and (lo is None or t >= lo) and (hi is None or t < hi)),
                  key=lambda r: (times[r], r))
    if first is not None:
        runs = runs[:first]
    chosen = set(runs)
    note = {"mode": "by-time", "untimed_runs": untimed,
            "first": runs[0] if runs else None, "last": runs[-1] if runs else None,
            "first_time": times[runs[0]].isoformat() if runs else None,
            "last_time": times[runs[-1]].isoformat() if runs else None}
    return [r for r in found if str(r.get("run_id") or "") in chosen], note


def rows(root: pathlib.Path, since: str = "", until: str = "", first: int | None = None,
         by_id: bool = False) -> list[dict]:
    return window(_all_rows(root), since, until, first, by_id)[0]


def auditor(row: dict) -> str | None:
    """The L1 role, from the finding id's prefix; None for a row no L1 role raised."""
    return PREFIX.get(str(row.get("finding_id", "")).split("-")[0].upper())


def report(found: list[dict]) -> dict:
    by = collections.defaultdict(collections.Counter)
    reasons = collections.Counter()
    for row in found:
        role = auditor(row)
        if role is None:
            continue
        by[role][row["verdict"]] += 1
        by["ALL"][row["verdict"]] += 1
        if row["verdict"] == "FALSE_POSITIVE":
            reasons[row.get("fp_reason") or "unrecorded"] += 1
    runs = {row.get("run_id") for row in found if auditor(row)}
    table = {}
    for role, v in by.items():
        c, f = v["CONFIRMED"], v["FALSE_POSITIVE"]
        table[role] = {"n": sum(v.values()), "confirmed": c, "fp": f,
                       "precision": round(c / (c + f), 3) if c + f else None}
    return {"runs": len(runs), "table": table, "fp_reasons": dict(reasons),
            "confirmed_per_run": round(by["ALL"]["CONFIRMED"] / len(runs), 2) if runs else None}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", type=pathlib.Path, default=pathlib.Path.home() / "Documents")
    ap.add_argument("--since", default="")
    ap.add_argument("--until", default="")
    ap.add_argument("--first", type=int, default=None)
    ap.add_argument("--by-id", action="store_true",
                    help="compare run ids as text, as the pre-registered baseline was computed")
    a = ap.parse_args()
    found, note = window(_all_rows(a.root.expanduser()), a.since, a.until, a.first, a.by_id)
    print(json.dumps({**report(found), "window": note}, indent=1))


if __name__ == "__main__":
    main()
