---
title: "Why AI code review hallucinates — and the two gates that fix it"
published: false
tags: ai, codereview, devtools, claude
---

AI code review has a trust problem, and it's not that it misses bugs. It's that it *invents* them.

If you've run an LLM over a diff, you've seen it: a "possible null dereference" on a value that's guarded three lines up. A "SQL injection" your ORM already parameterizes. A "race condition" that can't happen. And then — worse — it confidently rewrites working code to "fix" the thing that was never broken. The real bug, meanwhile, sits quietly in the noise.

The problem isn't intelligence. It's that most AI reviewers **report their first impression as a verdict.** A model reads a diff, pattern-matches "this looks like X," and emits a finding — without ever going back to check whether X is actually reachable in *this* code. Humans do a second pass ("wait, is `price` validated upstream?"). Most AI-review pipelines skip it.

Here are two gates that add that second pass — and a stress test showing what they catch.

## Gate 1: verify findings before you fix (anti-hallucination)

The idea is simple: **no finding is allowed into the fix plan until a separate step re-checks it against the real code.**

After the auditors produce findings, a verification pass takes each one and asks three questions:

1. Does the issue actually exist at the cited line?
2. Is it in the code that *changed*, or a pre-existing thing outside the diff?
3. Is the stated impact real, or already mitigated elsewhere — a guard upstream, a value validated before this point, a config defined in another module?

The key design choice: **bias the verifier toward refuting.** A wrongly-confirmed finding causes a needless (sometimes harmful) fix; a wrongly-dropped one is cheap to recover. So when the evidence isn't clear, drop it or escalate to a human — don't fix on a hunch.

This one step kills the majority of hallucinated findings, because hallucinations rarely survive contact with *"show me the exact line, and prove the impact can occur."*

## Gate 2: prove the fix maps to the finding (anti-regression + provenance)

Catching real bugs is half the job. The other half is not *introducing* one while fixing.

Two cheap checks close this:

- **Regression diff:** after applying fixes, a differential pass confirms the fix changed *nothing* beyond the intent of each finding — no incidental sign flip, no default-value drift, no new path that quietly bypasses a guard.
- **Fix→finding mapping:** a final gate emits a table — every confirmed finding must map to a fix, and every change must map to a finding. An orphan finding (unfixed) or a phantom change (a fix tied to nothing) forces a revision.

Provenance is underrated. If you can't point at *why* each line changed, you can't trust the diff.

## The stress test: one real bug, three traps

Talk is cheap, so here's a run I set up to keep myself honest. I built a tiny position sizer with **one subtle, money-losing bug** and **three planted false-positive traps**, then ran [CCA-Audit](https://github.com/GiulioDER/cca-audit) — a multi-agent audit pipeline for Claude Code — over it.

**The real bug** — a units error:

```python
BPS_PER_UNIT = 10_000  # 1.0 == 10_000 basis points

risk_budget   = equity_usd * (risk_limit_bps / 100)          # <-- bug
per_unit_risk = price * (stop_distance_bps / BPS_PER_UNIT)   # correct
```

`risk_limit_bps` is in basis points — dividing by `100` treats it as a percent. It's **100× too large.** The very next line converts the sibling quantity correctly (`/ 10_000`), so the inconsistency is right there — and the test only asserted `size > 0`, so it stayed green. On the example inputs, the position came out at **$2.5M notional on a $100k account (25:1 leverage)** instead of the intended $25k.

**The three traps** — each designed to bait a false positive:

1. A `return a / b` that *looks* like a `ZeroDivisionError` — but `b` is guarded by a validator (`price > 0`, `stop ≥ 1`).
2. The same division, with the guard moved to a *different, off-diff* file (does the reviewer trace the call graph, or just flag it?).
3. A config key read with `.get()` and no default — which *looks* like it could be `None`, but the key is defined in a pre-existing settings module.

**What happened:**

- The numeric auditor caught the units bug and quantified the blast radius.
- The bug, security, and performance auditors each *looked straight at* the division-by-zero and **declined it**, tracing the guard: *"strictly positive for any validated request — not a bug."*
- On the config trap, the validator **read the settings file, confirmed the key exists, and passed** — no phantom "missing key."
- The verification gate then confirmed the real findings, deduped four of them into one root fix, and even corrected an overstated impact number that one auditor had fumbled.

Six raw findings collapsed to **one one-line fix** (`/ 100` → `/ BPS_PER_UNIT`), the tests stayed green, and a final architect gate mapped every finding to the fix before committing. **Zero hallucinations across the whole run** — and I'd tried three ways to force one. [The full run — every finding, verdict, and unedited agent transcript — is in the repo.](https://github.com/GiulioDER/cca-audit/blob/demo/bps-sizing/examples/bps-sizing/RECEIPTS.md)

## Takeaways you can apply anywhere

You don't need any specific tool to get the benefit. Whatever your AI-review setup:

- **Add a verification step** between "findings" and "fixes." Make it re-derive each finding against the real code, and bias it toward refuting.
- **Diff your fixes** — confirm they changed only what the finding intended.
- **Demand provenance** — every change traceable to a reason.
- **Test your reviewer with traps** — plant a few guaranteed false positives and see if it takes the bait. If it does, you have a noise problem, and no amount of "found 47 issues" is worth it.

An AI reviewer that finds real bugs *and stays quiet about the fake ones* is worth ten that flag everything. These gates are how you get there.

---

*The pipeline in the stress test is [CCA-Audit](https://github.com/GiulioDER/cca-audit) — open source (MIT), installs into Claude Code as one `/audit-fix` command. The full unedited agent transcripts from the run above are in the repo, if you want to check my work. Feedback — especially real cases where it does hallucinate — very welcome.*
