# Receipts — real CCA auditor-agent runs

Excerpts from real CCA auditor-agent runs during the demo build (2026-07-06). Lightly trimmed for
length, absolute paths normalized to repo-relative; **verdicts and reasoning are verbatim.** Each
agent got a neutral, non-leading prompt (no pointing at the bug or the traps).

---

## 1. Numeric auditor — Variant A (`examples/bps-sizing/`) — CATCHES the money bug

> Let me trace the math with the example inputs: equity=100000, price=50, risk_limit_bps=50,
> stop_distance_bps=200.
>
> - `sizer.py:18` — `risk_budget = req.equity_usd * (req.risk_limit_bps / 100)` = 100000 × 0.5 =
>   **$50,000** (should be $500)
> - `sizer.py:19` — `per_unit_risk = req.price * (req.stop_distance_bps / BPS_PER_UNIT)` =
>   **$1.00/unit** (correct)
> - `sizer.py:20` — returns 50000 / 1.00 = **50,000 units** (should be 500)
>
> **NUM-001 — Critical — `sizer.py:18`.** Dividing a bps value by 100 does not yield a fraction... the value is 100×
> too large. The inline comment "scale bps -> fraction" is wrong: `/100` converts bps→percent.
> (Contrast line 19, which correctly divides the other bps quantity by `BPS_PER_UNIT = 10_000`.)
> ... the requested position ($2.5M) is 25× the $100k account... **This is a catastrophic
> over-sizing / blown-account bug on the money path.** Fix: divide by the same bps scale used on
> line 19.

**Verdict: CONFIRMED (Critical). Zero false positives raised by this auditor.**

---

## 2. Bug auditor — Variant A — DECLINES the guarded div-by-zero (Trap 1)

> Line 20: `return risk_budget / per_unit_risk` — divisor is strictly positive, so no
> division-by-zero.
>
> The one thing that could bypass those guarantees is if `position_size` is ever called with a `req`
> that was NOT constructed through Pydantic validation... But that's a caller-contract concern, not
> a bug in the added code.
>
> **No runtime bugs detected.** ... The potential division-by-zero at `sizer.py:20` is fully guarded
> by the Pydantic `Field` constraints in `model.py`: `price` is `gt=0` and `stop_distance_bps` is
> `ge=1`, so `per_unit_risk` is always strictly positive.

**Verdict: no finding — correctly refused to flag a guarded division.**

---

## 3. Bug auditor — Variant B (`examples/bps-sizing-b/`) — DECLINES again, guard is off-diff (Trap 2)

Here the sizer takes raw arguments (no inline guard) and the validation lives in a **pre-existing**
`schemas.py` the PR doesn't touch. The auditor traced the call graph anyway:

> The only caller of `sizer.position_size` in this PR is `service.size_position`, which validates via
> `SizingRequest` first. `position_size` has no other in-repo caller.
>
> `service.size_position` validates every payload through `SizingRequest` before any math runs, so on
> the sole call path into `sizer.position_size` the arguments are guaranteed: `price > 0`,
> `stop_distance_bps >= 1`... the boundary rules it out.
>
> `position_size` itself has no internal guard, so if any future caller invokes it without going
> through `SizingRequest`... `ZeroDivisionError`. **Within this PR's actual call graph that path does
> not exist, so it is not a bug today — flagging only as a robustness caveat, not a finding.**

**Verdict: no finding — traced a cross-file guard and refused to cry wolf.**

---

## 4. Env / config validator — Variant C (`examples/bps-sizing-c/`) — PASSES completeness (Trap 3)

The cap is read with `RISK_LIMITS.get("max_risk_budget_usd")` (no default); the key is defined in a
**pre-existing, off-diff** `settings.py`. The validator read the config and passed completeness:

> **Completeness: PASS** — `max_risk_budget_usd` is defined in `settings.py:8` with a value
> (`50_000.0`), so the key consumed by the new code exists and is non-empty.
>
> **ENV-001 (High):** `.get()` ... returns None if the key is missing/renamed... *[a real robustness
> hardening finding, not a "the key is missing" hallucination]*
>
> **ENV-002 (Medium):** line 12 scales by hardcoded `100` while line 15 uses the named constant
> `BPS_PER_UNIT`... the comment "scale bps -> fraction" does not match dividing by 100. *[independently
> re-flags the real bug from a constants angle; defers the numeric verdict to the numeric auditor]*

**Verdict: no false positive — verified the key exists, raised only real findings.**

---

## 5. fp-check gate — Variant C — CONFIRMS the real ones, dedups, calibrates, drops nothing

> | Finding | Verdict | Basis |
> |---|---|---|
> | NUM-001 (Critical) | **CONFIRMED** | `/ 100` where in-file `BPS_PER_UNIT=10_000`; 100× over-budget. |
> | ENV-001 (High) | **CONFIRMED** | unguarded `.get()` → None on rename; key happens to be defined today. |
> | ENV-002 (Medium) | **CONFIRMED (duplicate of NUM-001)** | same line, same fix. |
>
> **Dropped (false positives): None.**
>
> Note for the fixer: ENV-002 and NUM-001 target the **same line (12)** with the **same edit**. Apply
> once; do not double-count.

**Verdict: gate confirmed every real finding, de-duplicated the overlap, calibrated severity — and
had nothing false to drop.**

---

## The scoreboard

- **1** real, money-losing bug — caught and quantified ($2.5M notional, 25:1 leverage), while the
  test suite stayed green.
- **3** planted false-positive traps — **0** taken. Guarded division, cross-file guard, off-diff
  config key: read the code, refused each one.
- **4** independent auditor agents, neutral prompts, real files.
