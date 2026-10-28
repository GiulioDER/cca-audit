"""L1 auditor precision from the disposition ledgers (AUDITOR_SCORECARD.jsonl) on this machine.

Pre-registered use: docs/preregistrations/2026-10-28-l1-auditor-instructions.md. Precision is
CONFIRMED / (CONFIRMED + FALSE_POSITIVE) over L2.5 verdict rows, an fp-check judgement rather than
ground truth. Rows are deduplicated by (run_id, finding_id), because worktrees copy a ledger.

    python benchmarks/harness/l1_precision.py --root ~/Documents [--since 20261029T000000Z] [--until ...]
"""
from __future__ import annotations

import argparse
import collections
import json
import pathlib

PREFIX = {"STAKES": "stakes", "DAT": "data", "NUM": "numeric", "DEPLOY": "deploy", "DEP": "dep",
          "SEC": "security", "BUG": "bug", "CODE": "code", "PERF": "perf", "DOC": "doc",
          "ENV": "env"}


def rows(root: pathlib.Path, since: str, until: str) -> list[dict]:
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
            run = str(row.get("run_id") or "")
            if not since <= run < until:
                continue
            key = (run, row.get("finding_id"))
            if key not in seen:
                seen.add(key)
                out.append(row)
    return out


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
    ap.add_argument("--until", default="~")
    a = ap.parse_args()
    print(json.dumps(report(rows(a.root.expanduser(), a.since, a.until)), indent=1))


if __name__ == "__main__":
    main()
