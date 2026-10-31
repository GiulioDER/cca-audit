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

## Result (2026-10-30), the look at 15 runs

**Status:** measured; inconclusive on precision, so the record's second look at 30 runs applies.

Command, at master `64747b8` (#118):
`python benchmarks/harness/l1_precision.py --root ~/Documents --since 2026-10-28T20:01:05Z --first 15`.
Window: the 15 runs whose earliest verdict row falls after the install, from `20261028T204543Z`
(2026-10-28T20:59Z) to `pr639-630-20261030` (2026-10-30T09:10Z); one run with no parseable time
was left out, and no window run's worktree carried the old `cca-bug-auditor.md` (0 stale
installs to exclude; a worktree with no own install runs the user-wide one, which was current). 130 L1 verdict rows; 104 CONFIRMED, 22 FALSE_POSITIVE.

| Prediction | Baseline (as pre-registered) | Measured | Verdict |
|---|---|---|---|
| 1. out_of_scope share of L1 FPs below 0.25 | 0.456 (26 of 57) | **0.045** (1 of 22) | met |
| 2. precision at least 0.85 | 0.810 (243 of 300) | **0.825** (104 of 126), Wilson 95% 0.750 to 0.882 | not met; not falsified (above 0.81); the interval includes 0.81, so inconclusive |
| 3. confirmed per run at least 3.2 | 5.17 | **6.93** | met |
| 4. perf-auditor fewer findings per run | 0.43 (20 in 47) | **0.80** (12 in 15) | **wrong direction** |

**Gap.** The scope rule did what it was for: out_of_scope false positives went from about half of
all L1 false positives to one. Precision moved little because the false positives that remain are
mostly `not_real` (16 of 22: the defect is handled elsewhere) and `settled` (5), which the scope
rule does not touch and which the evidence bar's mitigation search has not yet measurably reduced.
I expected removing a whole class of false positives to lift precision by about 0.04; it lifted it
by 0.015, because the class it removed was replaced in part by `not_real` ones (25 of 57 in the
baseline, 16 of 22 now). Prediction 4 was wrong: the hot-path rule did not cut perf volume; 9 of
its 12 findings were confirmed.

**Apparatus defect, found at this look.** At the committed version (`4aed87d`) `--since`
compared run ids as text, so ids that are names (`pr659-650-...`, `cca-deep-20260906`) passed any
`--since`: the first count of this look took in six runs from August to 2026-10-12. #118 selects
runs by their earliest verdict time and keeps the old comparison behind `--by-id`. The
pre-registered baseline used only `--until`, which every named id fails, so its numbers above
stand. Re-run today, the baseline moves slightly because more scorecard copies are on disk:
`--until 20261029T000000Z --by-id` gives 52 runs, precision 0.814, out_of_scope 26 of 64; by
time, `--since 2026-10-25 --until` the install, 49 runs, 0.810, 26 of 58. The pre-registered
snapshot (`~/.claude/cca-measurements/2026-10-28-l1-baseline.jsonl`, sha256 above) is the baseline.

**Confounds that happened.** Inside the window: run-archive, single-file and leading-stem anchors
were written to the ledger (2026-10-28 evening to 2026-10-29 afternoon, 46 + 149 + 93 + 72
annotations), and the
RE-call auditor-brief reader was redeployed about 2026-10-29T12:30Z (file-level records rank below
symbol records), so what the brief showed changed during the window. One deferred pass
(`pr709-700-deferred-20261029`) is among the 15; the record does not exclude them. CCA was
reinstalled from `b15bae0` and `8d6f4f7` with no change to any prompt, command or hook.

## Result (2026-10-31), the look at 30 runs

**Status:** measured; the record's last look. Predictions 1 and 3 met, prediction 2 met on the
point estimate with the interval clear of the baseline, prediction 4 wrong.

Command, at master `11829af`:
`python benchmarks/harness/l1_precision.py --root ~/Documents --since 2026-10-28T20:01:05Z --first 30`.
Window: the first 15 runs above, then 15 more to `pr529-520-20261031` (2026-10-31T11:06Z); one
run with no parseable time (`mtrag-salvage-1`) was left out. No window run's worktree carried its
own copy of the agent files (0 stale installs to exclude), and the user-wide copies equal master's
for all nine L1 agents. 294 L1 verdict rows; 243 CONFIRMED, 33 FALSE_POSITIVE.

| Prediction | Baseline (as pre-registered) | Measured | Verdict |
|---|---|---|---|
| 1. out_of_scope share of L1 FPs below 0.25 | 0.456 (26 of 57) | **0.121** (4 of 33) | met |
| 2. precision at least 0.85 | 0.810 (243 of 300) | **0.880** (243 of 276), Wilson 95% 0.837 to 0.914 | met on the point estimate; the interval excludes 0.81 but includes 0.85 |
| 3. confirmed per run at least 3.2 | 5.17 | **8.10** | met |
| 4. perf-auditor fewer findings per run | 0.43 (20 in 47) | **0.80** (24 in 30) | **wrong direction** |

Per role (n, precision; baseline in brackets): code 67, 0.815 (0.750); doc 46, 0.911 (0.833); bug
45, 1.000 (0.914); security 25, 0.810 (0.769); perf 24, 0.789 (0.625); env 22, 0.762 (0.824); data
21, 1.000 (0.933); deploy 14, 1.000 (0.750); numeric 13, 1.000 (1.000); stakes 12, 0.917 (0.923);
dep 5, 0.400 (0.667). FP reasons: not_real 22, settled 7, out_of_scope 4.

**Gap.** The two looks disagree more than the record's design assumed. Runs 1 to 15 gave 0.825
(104 of 126); runs 16 to 30 alone give 0.927 (139 of 150). I do not read the rise as the
instructions improving with time, because the prompts did not change between the halves. The
likelier cause is the diff mix: the second half holds ten descending audits of RE-call PR ranges
(`pr619-610` to `pr529-520`, including one deferred pass), which together produced 57 confirmed
findings and no false positive, while all 11 of the half's false positives came from four
timestamp-named runs. The baseline's run mix differs from both halves, so the 0.07 gain is the
instructions plus a run mix I did not control. The scope rule result holds at both looks. perf
volume stayed at 0.80 per run, and its precision rose (15 of 19 decided), so the hot-path rule
made perf findings better, not fewer.

**Confounds that happened, after the first look.** No prompt, command or hook changed (the
2026-10-29T21:24Z rewrite of the agent files was the reinstall from `8d6f4f7`, content equal to
`cd8a204`). A second deferred pass (`pr639-630-deferred-20261030`) is in the window; the record
does not exclude them. The confounds listed under the first look still apply to every run after
them, and the ledger kept growing (bugs.jsonl last written 2026-10-31T15:12Z), so what the brief
showed kept changing.
