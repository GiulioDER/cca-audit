# CCA bug-detection benchmark — results (2026-07-15)

Blind whole-file auditors (bug + numeric) on the **buggy and fixed** versions of each
fix-touched file, a **recognition probe** (contamination measurement), and the **fp-check**
anti-hallucination gate. Ground truth = the human fixing-commit diff; a **catch** = a finding
localized within ±3 lines of a fix hunk on the buggy file; a **false alarm** = a finding on the
fixed file at the fix site. Two corpora, same harness.

## Headline comparison

| Metric | **BugsInPy** (2018–2020 libs) | **Fresh** (post-cutoff, 2026-04→07) |
|--------|------------------------------|--------------------------------------|
| bugs | 12 | 10 |
| **recognized by the model** | **12 / 12 (100%)** | **3 / 10 (30%)** |
| recall — confirmed, ALL bugs | 10 / 12 (83%) | 5 / 10 (50%) |
| **recall — confirmed, CLEAN (unrecognized) bugs** | **0 / 0 (no clean data)** | **3 / 7 (43%)** |
| recall — confirmed, recognized bugs | 10 / 12 | 2 / 3 |
| specificity (quiet on the fixed version) | 12 / 12 | 9 / 10 |
| fp-check drop rate (raw→confirmed) | 4 / 23 (17%) | **8 / 19 (42%)** |

## The two findings that matter

1. **The 40-point memorization gap.** Confirmed recall is **83% on the classic benchmark the model
   has memorized (100% recognized) vs 43% on code it has not.** Anyone citing a raw AI-auditor
   benchmark score without measuring recognition is reporting a number inflated by memorization.
   We measured it.

2. **The anti-hallucination gate works hardest exactly where it matters.** fp-check dropped **42% of
   raw findings on novel code vs 17% on memorized code.** On unfamiliar code the auditors speculate
   more; the gate catches it. That is the whole product thesis — "doesn't cry wolf" — shown on the
   corpus where crying wolf is most likely.

## The honest headline number
On real bugs in code the model provably had **not** memorized, CCA:
- **caught 3 / 7** at exact-line localization (aiohttp #12264 @673, clu-comics #389 @694, pipx #1860 @183),
- stayed **quiet on 9 / 10** fixed versions,
- and its gate **dropped 42%** of raw findings before they could reach you.

## Honest caveats (state these in any write-up)
- **Pilot scale.** 7 clean bugs is small; "43%" is 3/7 with wide error bars. Scaling to ~30–50 clean
  bugs is the obvious next step to tighten it.
- **The gate costs catches too.** On satpy #3367 a raw finding localized to the fix site but fp-check
  **dropped it** (raw hit → confirmed miss). The anti-hallucination gate is not free — it removed one
  real localization among the 8 it dropped.
- **One false alarm.** clu-comics #389 (a 318 KB app.py) produced a confirmed finding on the fixed
  version inside the fix window — possibly a genuine nearby issue rather than a pure false positive.
- **Localization tolerance ±3 lines**; fresh fix hunks run larger (9–56 lines) than BugsInPy's, so
  the fresh localization target is somewhat more lenient.
- **Detection & localization only** — not fix-correctness (that needs each project's test harness).

## Reproates
Seeded sampler + `gh`-based materializer + parallel workflow + deterministic scorer, all in
`cca-bench/harness/`. Fresh run: workflow `wf_d337c98f-0b9`, 61 agents, 4.27M tokens, ~34 min.
