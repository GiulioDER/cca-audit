# I tried three times to make CCA cry wolf. It read the code every time and refused.

Everyone's real complaint about AI code review is the same: **it hallucinates.** It flags a
"possible null deref" that's guarded three lines up, a "SQL injection" the ORM already
parameterizes, a "race condition" that can't happen — and then confidently "fixes" things that
weren't broken. So the honest test of an auditor isn't *"can it find bugs?"* It's *"will it shut up
when there's nothing to find?"*

This is that test, run for real. One tiny position-sizer with **one subtle, money-losing bug** and
**three planted traps** designed to bait a false positive. Every run below is a real CCA auditor
agent on the real files — full transcripts in [RECEIPTS.md](RECEIPTS.md).

**Result: it caught the money bug (that the tests pass right over), and refused all three traps.**

---

## The bug (that a skim-review and a green test suite both miss)

`sizer.py` converts a basis-point risk limit into a position size:

```python
BPS_PER_UNIT = 10_000  # 1.0 == 10_000 basis points

def position_size(req: SizingRequest) -> float:
    risk_budget   = req.equity_usd * (req.risk_limit_bps / 100)          # <-- bug: bps ÷ 100 (percent), not ÷ 10_000
    per_unit_risk = req.price * (req.stop_distance_bps / BPS_PER_UNIT)   # correct
    return risk_budget / per_unit_risk
```

`risk_limit_bps` is in **basis points** — dividing by `100` treats it as a *percent*. It should
divide by `10_000` (and the very next line does exactly that for the sibling quantity). The result
is **100× too large**. And the test only asserts `size > 0`, so it stays **green**.

CCA's numeric auditor caught it and quantified the blast radius:

> **NUM-001 — Critical — `sizer.py:18`.** *"Contrast line 19, which correctly divides the other bps
> quantity by `BPS_PER_UNIT`. The two conversions are inconsistent, and line 18 is the wrong one."*
> Intended: **500 units** ($25k notional). Actual: **50,000 units** ($2.5M notional — **25× a $100k
> account**). *"Catastrophic over-sizing / blown-account bug on the money path."*

When the full pipeline runs, the `fp-check` gate re-derives this with its own worked example and
**CONFIRMS** it ([RECEIPTS](RECEIPTS.md) §5). That's the catch. Now the three traps.

---

## Trap 1 — a guarded division that looks like a `ZeroDivisionError`

`return risk_budget / per_unit_risk` *looks* like it can divide by zero. A lazy auditor flags it. But
`per_unit_risk` derives from a `price` and `stop_distance_bps` that a Pydantic model validates
`> 0` / `>= 1`. The bug auditor traced it and **declined**:

> *"The potential division-by-zero is fully guarded by the Pydantic `Field` constraints... **No
> runtime bugs detected.**"*

## Trap 2 — same division, but the guard is in a *different, off-diff* file

To rule out "it only worked because the validator was right there," Trap 2 moves the sizer to raw
arguments and puts the validation in a **pre-existing caller** the PR doesn't touch. The bug auditor
walked the call graph anyway:

> *"`service.size_position` validates every payload through `SizingRequest` before any math runs...
> Within this PR's actual call graph that path does not exist, so it is not a bug today — flagging
> only as a robustness caveat, **not a finding.**"*

## Trap 3 — a config key that looks undefined (the classic false positive)

Trap 3 reads a required cap with `RISK_LIMITS.get("max_risk_budget_usd")` (no default) while the key
is defined in a **pre-existing, off-diff** `settings.py`. The env-validator **read the config,
passed the completeness check**, and raised only *real* hardening findings — one of which
independently re-flagged the bps bug from a constants angle. Then the `fp-check` gate:

> **All findings CONFIRMED, zero dropped.** ENV-002 CONFIRMED **but flagged as a duplicate of
> NUM-001** (*"same line, same fix — apply once, don't double-count"*). ENV-001 CONFIRMED but
> **calibrated to High, not Critical**, *"given the key is presently defined."*

No hallucination. Instead: cross-auditor convergence on the real bug, automatic de-duplication, and
severity calibrated against the actual code.

---

## Why this is the whole point

| Trap | What a lazy reviewer does | What CCA did |
|------|---------------------------|--------------|
| Guarded div-by-zero | Flags it, adds a needless guard | Read the validator, **declined** |
| Cross-file guard | Flags it (can't see the caller) | Traced the call graph, **declined** |
| Off-diff config key | "Key undefined → None → crash" | Read the config, **passed completeness** |
| The real bps bug | **Skims past it** (math "looks fine") | **Caught + quantified** ($2.5M, 25:1) |

The differentiator isn't a fancy agent count. It's that CCA **verifies findings against the real
code before it ever touches your files** — so it catches the expensive bug *and* keeps quiet about
the fake ones.

---

## Reproduce it yourself

```bash
git clone https://github.com/GiulioDER/cca-audit
cd cca-audit && ./claude-code/install.sh      # Windows: ./claude-code/install.ps1
git checkout demo/bps-sizing
# then, in an interactive Claude Code session at the repo root:
/audit-fix commit 1
```

`commit 1` audits the last commit (the sizer PR). You'll watch the numeric auditor catch NUM-001,
the bug auditor decline the div-by-zero, `fp-check` confirm the real finding, the one-line fix
applied (`/ 100` → `/ BPS_PER_UNIT`), and the architect gate APPROVE.

To reset and re-run: `git reset --hard demo-start`.

Traps 2 and 3 are their own runnable examples — `examples/bps-sizing-b/` and
`examples/bps-sizing-c/` — audit them the same way. Full raw transcripts: [RECEIPTS.md](RECEIPTS.md).

## Record a clean take

```bash
asciinema rec cca-demo.cast     # start recording
/audit-fix commit 1             # run it live (real, ~2-4 min, several agents)
# Ctrl-D to stop, then:
agg cca-demo.cast cca-demo.gif  # render to GIF; trim to the key beats
```

Key beats to keep in the GIF: NUM-001 (the $2.5M catch) → the bug auditor *declining* the
div-by-zero with its reasoning → `fp-check` CONFIRMED → the one-line fix → architect APPROVED.
