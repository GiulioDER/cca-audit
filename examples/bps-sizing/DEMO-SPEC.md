# CCA Proof-Asset Demo — bps Sizing Bug (design of record)

**Goal:** one reproducible artifact proving CCA's edge — it catches a subtle,
money-losing units bug that a single-pass review skims past, AND it *drops its own
false positive* at the L2.5 anti-hallucination gate.

## Scenario
`examples/bps-sizing/` — a position sizer that turns a basis-point risk limit + stop
distance into a position size. The "PR under review" is the feature that adds it.

## Planted REAL bug (P1, money-losing)
`sizer.py`: `req.risk_limit_bps / 100` treats basis points as percent → risk budget
**100x** too large. On the test inputs (equity $100k, price $50, risk 50 bps, stop
200 bps): intended size ≈ **500 units** ($25k notional, 0.25x equity); buggy size =
**50,000 units** ($2.5M notional, **25:1 leverage**) — blows any prop-firm limit.
The adjacent line scales bps correctly via `BPS_PER_UNIT = 10_000`, so the
inconsistency is glaring to a numeric/dimensional auditor and easy for a human to
miss. The test only asserts `size > 0`, so tests stay **GREEN**.

## Bait FALSE POSITIVE (dropped at L2.5)
A bug-auditor flags `ZeroDivisionError` at `return risk_budget / per_unit_risk`. But
`model.py` validates `price > 0` and `stop_distance_bps >= 1`, so `per_unit_risk` is
always > 0. `fp-check` reads the model, finds the validators, and drops it as
FALSE_POSITIVE — the money shot: the tool refusing to add a needless guard.

## Narrative arc (to be captured from a REAL run)
1. `/audit-fix commit 1` on the PR diff
2. L1 auditors: ~4 findings incl. the bps bug (P1) + the div-by-zero (fake)
3. L2.5 fp-check: CONFIRM the bps bug; DROP the div-by-zero with evidence
4. Fix: minimal (`/ 100` → `/ BPS_PER_UNIT`)
5. L5.5 anti-regression: SAFE (only the sizing line changed)
6. Architect gate: APPROVED + finding→fix map
7. Contrast: a single-pass reviewer misses the bps bug or "fixes" the non-bug

## Deliverables
- This demo repo + PR diff (reproducible: clone cca-audit, install, `/audit-fix commit 1`)
- `RUN.md` — real captured transcript
- `DEMO.md` — case study (diff → annotated run → normal-review-vs-CCA → reproduce)
- asciinema cast script for a clean GIF

## Reproduce
_Finalized after rehearsal._
