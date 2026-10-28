# Pre-registration: rewritten L1 auditor instructions

**Date:** 2026-10-28   **Status:** predicted, not yet measured

## The question

After the L1 auditor instructions change (one output contract, scope tied to the run's mode, an
evidence bar with a trigger and a mitigation check, logic and contract checks for bug-auditor, a
hot-path rule for perf-auditor), does L1 precision rise and does the `out_of_scope` share of L1
false positives fall, without fewer confirmed findings per run?

## What I predict

1. The `out_of_scope` share of L1 false positives falls from 0.456 (26 of 57) to **below 0.25**.
   This is the change I am most confident in: those findings come from checklists the agent files
   apply repo-wide while the command says changed code only.
2. Overall L1 precision rises from 0.810 (243 of 300) to **at least 0.85**.
3. Confirmed findings per run do **not** fall below 3.2 (baseline mean 5.17, per-run standard
   deviation 3.98 over 47 runs; 3.2 is about two standard errors below the mean at n = 15).
4. perf-auditor produces fewer findings per run than its baseline 20 in 47 runs (0.43 per run).

## What would falsify this

- Prediction 1: an `out_of_scope` share of 0.40 or more.
- Prediction 2: precision at or below 0.81.
- Prediction 3: a mean below 3.2 confirmed findings per run, which would mean the evidence bar or
  the narrower scope suppresses real findings rather than false ones.

## How it will be measured

`python benchmarks/harness/l1_precision.py --root ~/Documents --since <install timestamp>`, at the
committed version of that script. Metric names:

- **precision**: CONFIRMED / (CONFIRMED + FALSE_POSITIVE) over L2.5 verdict rows whose finding id
  carries an L1 prefix (BUG, STAKES, DAT, NUM, SEC, CODE, PERF, DOC, ENV, DEP, DEPLOY). UNCERTAIN,
  DUPLICATE, STALE and PRIOR_FP rows are not in the denominator.
- **out_of_scope share**: FALSE_POSITIVE rows with `fp_reason: out_of_scope` over all L1
  FALSE_POSITIVE rows.
- **confirmed per run**: L1 CONFIRMED rows over distinct `run_id`s with at least one L1 row.

The window opens at the `run_id` of the first run after the new agent files are installed in
`~/.claude/agents` (and in Codex), recorded in the result. One look at the first **15** runs. If a
95% Wilson interval for precision includes 0.81, that look reports "inconclusive", and one further
fixed look follows at 30 runs. No other looks.

Baseline: every scorecard row before `20261029T000000Z`, 316 rows, 47 runs, `20261025T194500Z` to
`20261028T185351Z`, snapshot in `~/.claude/cca-measurements/2026-10-28-l1-baseline.jsonl` (sha256
`72c3a6a7ffd4f132dcaa411989d96adb58d39826f1e19b944dd4e35fbeebed9b`). It holds claims about private
repositories, so it stays outside this repository.

Baseline per role (n, precision): code 94, 0.750; doc 74, 0.833; bug 35, 0.914; security 27,
0.769; env 20, 0.824; perf 20, 0.625; data 16, 0.933; stakes 15, 0.923; numeric 7, 1.000;
deploy 5, 0.750; dep 3, 0.667. FP reasons: out_of_scope 26, not_real 25, settled 6.

## What I already know

- 2026-10-20 and 2026-10-21 (`2026-10-20-prior-bugs-in-brief.md`,
  `2026-10-20-prior-bugs-rule-and-noise.md`): one rule sentence in the auditor prompt moved
  confirmed findings by about as much as run-to-run noise, and haiku auditors varied by up to 6
  confirmed findings between identical runs. Prompt wording matters, and single runs are noise.
- `recall_search` on the cca tenant for auditor prompt tuning abstained (corpus gap) on
  2026-10-28: no earlier decision on this.
- `benchmarks/` holds a 22-bug recall pilot. This record measures precision only.

## Confounds I can name now

- **Precision is fp-check's verdict, not ground truth.** If the new evidence field makes findings
  easier for fp-check to confirm, precision can rise with no change in real correctness.
- **Different diffs.** The baseline and the window audit different changes, mostly in RE-call;
  a run of small diffs lowers confirmed per run on its own.
- **Recall is not measured.** Fewer false positives may come with more missed bugs, which this
  record cannot see; the ledger replay benchmark is the instrument for that.
- **Install lag.** A worktree with a stale `.claude/` self-install runs the old prompts inside the
  window (see the memo on the local install going stale). Runs whose agent files predate the change
  should be excluded where their install can be told apart; the result states how many were.
- **Model mix.** Auditors run with `model: inherit`; a change of session model inside the window
  moves every number.
