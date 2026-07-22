---
title: "Fluency isn't evidence: settling arithmetic bugs with counterexamples, not re-reads"
published: false
tags: testing, python, hypothesis, devtools
---

> **CCA-Audit** — the multi-agent code auditor in this post — is open source (MIT): https://github.com/GiulioDER/cca-audit

A risk-neutral drift term was written `(mu + 0.5 * vol**2) * t`. The correct form is `(mu - 0.5 * vol**2) * t`.

The expression is well-formed. The variable names are right. Only the *meaning* is inverted — variance drag should reduce expected growth, and this raised it. A reviewer read that exact line and hand-verified it as correct.

It was caught later, and only by comparing against a second, independently-derived implementation of the same formula.

That is the whole problem in one line of code. A sign error is the bug class that survives review, because review is reading, and the code reads fine.

## Why re-reading can't fix this

CCA-Audit settles findings mechanically wherever it can. A finding comes in as a claim, a deterministic tool checks it, and a verdict comes out carrying an artifact:

| Claim type | Settled by |
|---|---|
| `definedness`, `nullability`, `type` | pyright |
| `taint` | semgrep |
| `crash_impact` | a pytest repro |

Numeric findings — wrong sign, mixed units, bad scaling, non-inverting conversions — had nothing. They fell through to semantic adjudication, where a language model re-reads the arithmetic and renders a judgment.

That is the one method this bug class defeats. Asking a second model to re-read a plausible-looking expression doesn't get you a second opinion; it gets you the same opinion with more confidence.

## Properties, not a twin implementation

The obvious move is to implement the logic twice and diff the two. It's also the wrong one here: a twin written by the same model that raised the finding inherits the same misreading, and it's expensive to author.

So the auditor declares the **intended relation** instead — something that cannot be read off the implementation under test — and Hypothesis searches for an input that breaks it.

```python
from hypothesis import given, strategies as st
from cca_checks.hypo import cca_settings
from cca_checks.properties import assert_monotonic_in
from growth import expected_log_growth

@cca_settings
@given(mu=st.floats(-0.5, 0.5), vol=st.floats(0.01, 1.0), t=st.floats(0.01, 5.0))
def test_growth_decreases_with_volatility(mu, vol, t):
    # The intended relation, stated independently of the code:
    # more volatility must not raise expected log growth.
    assert_monotonic_in(expected_log_growth, (mu, vol, t),
                        index=1, direction="decreasing", delta=0.1)
```

```bash
pip install -e ".[numeric]"   # from a clone -- cca_checks is not on PyPI
python -m cca_checks numeric --finding-id NUM-001 --test t_NUM-001_props.py
```

```json
{"finding_id": "NUM-001", "verdict": "CONFIRMED", "evidence": "property violated:\nFalsifying example: test_growth_decreases_with_volatility(\n    mu=0.0,\n    vol=1.0,\n    t=1.0,\n)\ncca_checks.properties.PropertyViolation: PROPERTY monotonic violated | inputs=(0.0, 1.0, 1.0) | observed=(0.5, 0.6050000000000001) | required=result non-increasing in arg 1", "source": "hypothesis"}
```

The two numbers in `observed` are the whole case: hold `mu` and `t` fixed, raise volatility, and the result went **up**, from `0.5` to `0.605`. It should have gone down.

That's an artifact, not a judgment. And it reproduces — runs are derandomized, so the same audit returns the same falsifying input every time. An audit that reports a different failing input on each run isn't evidence of anything.

(The snippet above is the shape; the runnable version, including the import wiring, is `examples/sign-trap/t_NUM-001_props.py`.)

The helper vocabulary is fixed and small: `assert_bounded`, `assert_monotonic_in`, `assert_limit`, `assert_scale_invariant`, `assert_sign_symmetric`, `assert_round_trips`. Every one takes the intended relation as an explicit argument, which is what makes a tautological property — one that merely restates the implementation — impossible to write through it.

> *Update:* this post describes v3.4. v3.5 added a seventh helper, `assert_substrate_agrees` —
> no authored relation at all, just the same target run twice (float64 vs a 50-digit `mpmath`
> reference) so the substrates disagree where an author couldn't. See the
> [design spec](superpowers/specs/2026-07-21-substrate-differential-design.md).

## The asymmetry is the point

The checker can confirm. It can never refute.

| Outcome | Verdict | Why |
|---|---|---|
| A property breaks | `CONFIRMED` | carries the falsifying input |
| No counterexample in 200 examples | `UNCERTAIN` | absence is not proof |
| Optional dependency missing | `UNCERTAIN` | a check that couldn't run never passes |
| Collection error, timeout | `UNCERTAIN` | inconclusive, not clean |
| — | `FALSE_POSITIVE` | structurally unreachable |

Properties holding across a bounded search is the absence of a counterexample, not proof of correctness. Encoding that in the verdict vocabulary — rather than letting a clean run quietly read as "verified" — is most of the value.

It also mirrors the existing `taint` settler, where the reachable verdicts run the other way: semgrep can prove a sink is *absent* but never that an injection is real. One asymmetry per tool, in whichever direction that tool can actually justify.

## Three defects its own review caught

Every one of these was written into the implementation plan by the same author who designed the feature. The plan's authorship is not evidence of its correctness — which is the thesis restated, applied to itself.

**A tolerance bug in the tolerance checker.** `assert_monotonic_in` compared with a bare `1e-12` while every other helper used a combined relative-and-absolute comparison. On functions returning large values — prices, notionals — ordinary float noise on a flat region exceeds that threshold, raising a violation against correct code. Fixed with a magnitude-aware epsilon; the follow-up audit found two more, including a boundary check running at literally zero tolerance.

**A verdict that confirmed the wrong thing.** Confirmation was gated on Hypothesis's `Falsifying example:` banner alone. A reviewer demonstrated with a live repro that an incidental `ZeroDivisionError` inside a `@given` test prints the identical banner — confirming a finding that was never actually tested for violation. The fix requires the checker's own `PROPERTY ... violated` line as well. Useful side effect: since only the seven helpers emit that line, a hand-written `assert` can no longer reach confirmation, which turns the anti-tautology guarantee from an authoring convention into a boundary the tool enforces.

**A gate that let three models overrule the artifact.** Numeric findings are high-stakes, so they route to an adversarial three-skeptic panel that defaults to rejection — and the new verdict source was never added to the "you may not overturn a tool artifact" list. Three models re-reading the sign error, which reads as correct, could have voted away a confirmation holding a falsifying example. The feature would have contributed nothing on the exact case it was built for. Found only by a whole-branch review; no per-task gate could see across that seam.

## What it cannot do

The blindness probe ships as a test, not a caveat.

Point the same tooling at the same buggy function using a limit at `vol = 0`, and the flipped term vanishes:

```python
assert_limit(expected_log_growth, (mu, 0.5, t), index=1,
             approaching=0.0, expected=mu * t)
```

Both the buggy and the correct implementation collapse to `mu * t` there. The property passes, the defect is untouched, and the verdict is `UNCERTAIN` — never a refutation. That case is in the acceptance suite, asserting exactly that.

There's a second limit worth naming plainly. The property is written by the auditor, so a *wrong* declared relation yields a genuine falsifying example against correct code. Write `assert_limit` with a literal `expected: 0.0` where the true limit depends on the surviving arguments, and you manufacture a counterexample out of a bad claim. A confirmation therefore obliges you to re-read the declared relation, not just the verdict.

The artifact is only ever as sound as what it encodes. That's a real constraint, and it's better stated than discovered.

## Takeaways

- **A second reading is not a second opinion.** If a bug class defeats careful reading, more careful reading is not the remedy.
- **State the intended relation, never the implementation.** A property derived from the code passes on buggy code and proves nothing.
- **Distinguish "check failed" from "check couldn't run."** A timeout, a missing dependency, a collection error — none of those are a pass.
- **Say where your tool is blind, in a test.** A verification tool that can't name its own blind spots is asking for trust it hasn't earned.

Source, worked example, and the full design spec: [github.com/GiulioDER/cca-audit](https://github.com/GiulioDER/cca-audit) — see `examples/sign-trap/` for a runnable version of the trap above.
