#!/usr/bin/env python3
"""CCA auditor scorecard — the statistical-filtering layer (ported from AIDE's
three-layer anti-reward-hacking defence: prompt rules, hard-coded guards, then
statistical filtering).

Reads a disposition ledger (`.claude/audits/AUDITOR_SCORECARD.jsonl`) and decides,
per `(auditor, category)` cell, whether that cell's future findings need EXTRA
verification.

SAFETY PROPERTY — structural, not advisory
------------------------------------------
This module can only ever ADD scrutiny. `Report` has no field capable of
expressing a drop/suppress/exclude decision, and no threshold configuration
produces one. A rarely-right auditor can still be the only one to catch the one
real Critical, so filtering by base rate would Goodhart the auditors — *a
suppression rate is not a score*. `test_never_emits_a_drop` pins this so the
property cannot regress into the code later.

Why this is code and not prose: a statistic an LLM eyeballs from a markdown table
is not a measurement. The n<10 guard in particular has to be enforced mechanically
— it is the whole thing standing between "precision 0.0" and "precision 0.0 on a
sample of one".
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

DEFAULT_LEDGER = Path(".claude/audits/AUDITOR_SCORECARD.jsonl")

N_MIN = 10          # below this a cell is `learning` — never routed, never flagged
P_ROUTE = 0.50      # precision under this ⇒ raise verification (routing must be ON)
P_REVIEW = 0.40     # precision under this ⇒ surface for human prompt-review
YIELD_MIN = 15      # disposed findings before a zero-CONFIRMED auditor is flagged
WINDOW_DAYS = 90

# Only these two verdicts are a judgement the auditor can be right or wrong about.
# UNCERTAIN and DUPLICATE are excluded from the denominator: neither is a wrong call.
_SCORED = ("CONFIRMED", "FALSE_POSITIVE")


@dataclass(frozen=True)
class Cell:
    auditor: str
    category: str
    confirmed: int
    false_positive: int

    @property
    def n(self) -> int:
        return self.confirmed + self.false_positive

    @property
    def precision(self) -> float | None:
        return None if self.n == 0 else self.confirmed / self.n

    @property
    def learning(self) -> bool:
        return self.n < N_MIN

    @property
    def key(self) -> str:
        return f"{self.auditor}/{self.category}"


@dataclass
class Report:
    """The complete decision surface. Note what is absent: there is no `drop`,
    `suppress`, `exclude` or `demote` field, and nothing downstream can synthesise
    one from what is here."""

    cells: list[Cell] = field(default_factory=list)
    route_up: list[str] = field(default_factory=list)   # ADD verification to these
    review: list[str] = field(default_factory=list)     # surface to a human
    yield_flags: list[str] = field(default_factory=list)
    routing_enabled: bool = False
    rows_scored: int = 0
    rows_skipped_outcome: int = 0
    rows_skipped_malformed: int = 0

    @property
    def ready_cells(self) -> list[Cell]:
        return [c for c in self.cells if not c.learning]

    def summary_line(self) -> str:
        return (
            f"Scorecard: {len(self.ready_cells)} cells >={N_MIN}n | "
            f"routed-up: {', '.join(self.route_up) or 'none'} | "
            f"review: {', '.join(self.review) or 'none'} | "
            f"routing={'on' if self.routing_enabled else 'off'}"
        )


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat(raw.replace("Z", "+00:00"))


def load_rows(ledger: Path, now: datetime, window_days: int = WINDOW_DAYS):
    """Yield (row, kind) where kind is 'scored' | 'outcome' | 'malformed'.

    A malformed row is reported, never silently discarded — a corrupted ledger must
    not render identically to a clean one.
    """
    if not ledger.exists():
        return
    cutoff = now - timedelta(days=window_days)
    for line in ledger.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            yield None, "malformed"
            continue
        if "verdict" not in row:
            yield row, "outcome"          # back-filled fix outcome, not a judgement
            continue
        try:
            if _parse_ts(row["ts"]) < cutoff:
                continue
            row["auditor"], row["category"]  # noqa: B018 - presence check
        except (KeyError, TypeError, ValueError):
            yield None, "malformed"
            continue
        yield row, "scored"


def build(ledger: Path = DEFAULT_LEDGER, now: datetime | None = None,
          routing: bool = False, window_days: int = WINDOW_DAYS) -> Report:
    now = now or datetime.now(timezone.utc)
    rep = Report(routing_enabled=routing)
    tally: dict[tuple[str, str], Counter] = defaultdict(Counter)
    per_auditor: dict[str, Counter] = defaultdict(Counter)

    for row, kind in load_rows(ledger, now, window_days):
        if kind == "malformed":
            rep.rows_skipped_malformed += 1
            continue
        if kind == "outcome":
            rep.rows_skipped_outcome += 1
            continue
        rep.rows_scored += 1
        verdict = row["verdict"]
        per_auditor[row["auditor"]]["disposed"] += 1
        if verdict == "CONFIRMED":
            per_auditor[row["auditor"]]["confirmed"] += 1
        if verdict in _SCORED:
            tally[(row["auditor"], row["category"])][verdict] += 1

    for (auditor, category), c in sorted(tally.items()):
        cell = Cell(auditor, category, c["CONFIRMED"], c["FALSE_POSITIVE"])
        rep.cells.append(cell)
        if cell.learning:
            continue                      # the n<X guard: no action on a noisy estimate
        assert cell.precision is not None
        if routing and cell.precision < P_ROUTE:
            rep.route_up.append(cell.key)
        if cell.precision < P_REVIEW:
            rep.review.append(cell.key)

    for auditor, c in sorted(per_auditor.items()):
        if c["disposed"] >= YIELD_MIN and c["confirmed"] == 0:
            rep.yield_flags.append(auditor)
    return rep


def render(rep: Report) -> str:
    out = [f"{'auditor/category':<46} {'n':>3} {'prec':>6}  state"]
    for c in rep.cells:
        if c.learning:
            out.append(f"{c.key:<46} {c.n:>3} {'  -  ':>6}  learning ({c.n}/{N_MIN})")
        else:
            tags = []
            if c.key in rep.route_up:
                tags.append("ROUTE-UP")
            if c.key in rep.review:
                tags.append("REVIEW")
            out.append(f"{c.key:<46} {c.n:>3} {c.precision:>6.2f}  ready {' '.join(tags)}")
    if not rep.cells:
        out.append("(ledger empty — no dispositions recorded yet)")
    out.append("")
    out.append(rep.summary_line())
    if rep.yield_flags:
        out.append(f"  yield-flag (>={YIELD_MIN} disposed, 0 CONFIRMED): "
                   f"{', '.join(rep.yield_flags)}")
    if rep.rows_skipped_malformed:
        out.append(f"  WARNING: {rep.rows_skipped_malformed} malformed ledger row(s) "
                   f"skipped — the statistic is computed on an incomplete sample")
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--ledger", type=Path, default=DEFAULT_LEDGER)
    ap.add_argument("--routing", choices=("on", "off"), default="off",
                    help="ON lets low-precision cells RAISE verification. Never drops.")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    a = ap.parse_args()
    rep = build(a.ledger, routing=(a.routing == "on"))
    if a.json:
        print(json.dumps({
            "route_up": rep.route_up, "review": rep.review,
            "yield_flags": rep.yield_flags, "routing": rep.routing_enabled,
            "rows_scored": rep.rows_scored,
            "rows_malformed": rep.rows_skipped_malformed,
            "cells": [{"auditor": c.auditor, "category": c.category, "n": c.n,
                       "precision": c.precision, "learning": c.learning}
                      for c in rep.cells],
        }, indent=2))
    else:
        print(render(rep))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
