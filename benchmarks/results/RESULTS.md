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
| — of those drops, **wrong** (landed on ground truth) | 1 / 4 | 2 / 8 |
| — **bugs lost to the gate** (wrong drop, nothing else caught it) | **0** | **1** |
| — **false alarms prevented by the gate** | 0 | 1 |
| recall — raw (pre-gate), CLEAN bugs | 0 / 0 | 4 / 7 (57%) |

## The two findings that matter

1. **The 40-point memorization gap.** Confirmed recall is **83% on the classic benchmark the model
   has memorized (100% recognized) vs 43% on code it has not.** Anyone citing a raw AI-auditor
   benchmark score without measuring recognition is reporting a number inflated by memorization.
   We measured it.

2. **The anti-hallucination gate is busiest exactly where it matters — but a drop rate is not a
   score.** fp-check dropped **42% of raw findings on novel code vs 17% on memorized code**: on
   unfamiliar code the auditors speculate more, and the gate is doing more work. What that rate
   cannot tell you is whether the work was *right*. A gate that suppresses correctly and a gate that
   suppresses a real bug produce the same number, so 42% is evidence of activity, not of accuracy.

   Because this benchmark has ground truth, the drops are adjudicated instead of assumed
   ([`score.py`](../harness/score.py)): of the 8 fresh drops, **2 landed on a real fix site**, and
   **1 of those cost a bug outright** (satpy #3367 — nothing else caught it). One drop on a *fixed*
   file prevented a false alarm. So the gate's fresh-corpus ledger is **−1 bug, +1 specificity save**,
   with 5 drops off-target. That is a defensible trade, and it is a much smaller claim than "42% of
   findings were hallucinations."

## The honest headline number
On real bugs in code the model provably had **not** memorized, CCA:
- **caught 3 / 7** at exact-line localization (aiohttp #12264 @673, clu-comics #389 @694, pipx #1860 @183),
- stayed **quiet on 9 / 10** fixed versions,
- and its gate **dropped 42%** of raw findings before they could reach you — of which exactly
  **one drop cost a real bug** and one prevented a false alarm.

Pre-gate, the same auditors caught **4 / 7**. The gate is the difference between 57% and 43% recall,
and it bought one specificity point for it.

## Honest caveats (state these in any write-up)
- **Pilot scale.** 7 clean bugs is small; "43%" is 3/7 with wide error bars. Scaling to ~30–50 clean
  bugs is the obvious next step to tighten it.
- **The gate costs catches too.** On satpy #3367 a raw finding localized to the fix site but fp-check
  **dropped it** (raw hit → confirmed miss). The anti-hallucination gate is not free — it removed one
  real localization among the 8 it dropped. This is now measured rather than hand-noted: `score.py`
  flags it `FATAL` from the ground-truth index, so a future run cannot regress here quietly.
- **Drop *reasons* are missing from these two runs.** The `drop_reason` enum (refuted vs merely
  unprovable-from-this-file) was added after these results were produced, so both runs score as
  `unlabeled` and the refuted/inconclusive split is `0/0`. Until the corpora are re-run, we can say
  which drops were *wrong* (ground truth answers that) but not which were *refutations* versus
  *coverage limits*. Treat the split as unmeasured, not as zero.
- **One false alarm.** clu-comics #389 (a 318 KB app.py) produced a confirmed finding on the fixed
  version inside the fix window — possibly a genuine nearby issue rather than a pure false positive.
- **Localization tolerance ±3 lines**; fresh fix hunks run larger (9–56 lines) than BugsInPy's, so
  the fresh localization target is somewhat more lenient.
- **Detection & localization only** — not fix-correctness (that needs each project's test harness).

## Reproates
Seeded sampler + `gh`-based materializer + parallel workflow + deterministic scorer, all in
`cca-bench/harness/`. Fresh run: workflow `wf_d337c98f-0b9`, 61 agents, 4.27M tokens, ~34 min.
