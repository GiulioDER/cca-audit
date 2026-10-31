# Pre-registration: does the prior-bugs brief add confirmed L1 findings, by random holdout

**Date:** 2026-10-31   **Status:** predicted, not yet measured

## The question

In live CCA DIFF runs at STANDARD or DEEP, is the mean number of confirmed L1 findings per run
higher in runs whose auditors get the prior-bugs brief than in runs assigned to the holdout, and
by how much?

Secondary: what share of confirmed L1 findings in brief runs cite a memory record, and do cited
findings confirm at least as often as uncited ones?

## What I predict

1. **Gap, brief arm minus holdout arm, confirmed L1 findings per run: about +1.0** (rough range 0
   to +2). The brief arm is unlikely to lose findings, because the auditor text now says a
   record never raises or lowers the bar; and it is unlikely to gain much, because the brief
   lists at most a handful of records near the change.
2. **At the first look the 95% interval for the gap includes 0.** With a per-run standard
   deviation near 4.8 (measured below) and 15 holdout runs against about 30 brief runs, the
   standard error of the gap is about 1.5, so only a gap above about 3 would be detected. I
   expect "inconclusive", and I am writing that down so it reads as the expected outcome, not as
   a disappointment.
3. **Citation share:** between 10% and 30% of confirmed L1 findings in brief arm runs have a
   non-empty `memory_ids`.
4. **Cited findings confirm at least as often as uncited ones** (L2.5 CONFIRMED over CONFIRMED
   plus FALSE_POSITIVE, brief arm runs).
5. **Unlisted citations** (ids the brief did not print) are under 10% of cited ids.

## What would falsify this

- Prediction 1: a point estimate of the gap at or below 0, or above +3.
- Prediction 2: an interval that excludes 0 at the first look (either direction).
- Prediction 3: a share below 0.10 or above 0.30.
- Prediction 4: cited precision below uncited precision by more than 0.05.
- Prediction 5: unlisted at or above 10% of cited ids.

A claim that the brief **adds** findings needs the interval's lower bound above 0. Nothing in this
record licenses that claim from a positive point estimate alone.

## How it will be measured

**Assignment.** A DIFF run is in the holdout arm when `int(sha256(run_id), 16) % 3 == 0`
(`cca_checks/bugs/holdout.py`), recorded in each run's `memory-arm.json` and `summary.json`
`memory_arm`. Holdout runs render the brief as the empty no-match form; auditors are not told.

**Window.** Opens at the first run after both installs are in place: CCA from the merge commit of
this build (`~/.claude` and Codex), and RE-call's auditor-brief hook with the holdout skip
(GiulioDER/RE-call branch `claude/cca-holdout-skip`) deployed to `~/.claude/hooks/recall_hooks`
and used by the Codex bridge. A run before either install is out. Both timestamps are recorded in
the result.

**Runs.** `python benchmarks/harness/memory_gap.py --root ~/Documents --since <window start>` and
the same with `--root ~/.codex/worktrees`, rows merged by `run_id` (first seen kept). In: closed
DIFF runs at STANDARD or DEEP with a readable arm. Out, counted by reason: HUNT, deferred, FAST,
untimed, no verdicts, unreadable, no arm. Runs are analysed by **assigned** arm: a brief arm run
whose brief happened to be empty stays in the brief arm.

**Metric names.**
- **confirmed per run**: L1-prefixed findings with a CONFIRMED verdict in the run's own stored
  L2.5, over included runs (a run with no L1 finding counts 0), per arm.
- **gap**: brief arm mean minus holdout arm mean; **interval**: Welch 95% interval of that
  difference from the per-run counts.
- **citation share**: over brief arm runs, confirmed L1 findings with a non-empty `memory_ids`
  (`memory_attribution.by_verdict.CONFIRMED`) over all confirmed L1 findings in those runs.
- **cited precision**: `by_verdict.CONFIRMED / (CONFIRMED + FALSE_POSITIVE)`; uncited precision from
  the run's L2.5 rows minus the cited ones.
- **unlisted share**: sum of `memory_attribution.unlisted` over sum of distinct cited ids.

**Looks.** One look when the holdout arm has **15** included runs, with every included brief arm
run in the same window. If the interval includes 0, one further fixed look at **30** holdout runs.
No other looks. The holdout stays on (`holdout.ACTIVE`) until the last look, then a PR turns it
off.

## What I already know

- 2026-10-31, `memory_gap.py` over the PR 104 window (2026-10-28T20:01:05Z to
  2026-10-31T11:06:21Z, both roots): 24 included runs, **all** with a non-empty brief, mean
  **5.708** confirmed per run, standard deviation **4.80**, median 4.5. That is the brief arm's
  likely scale and spread; there was no no-brief run to compare, which is why this record uses a
  holdout (design revision of 2026-10-31).
- `docs/preregistrations/2026-10-20-prior-bugs-in-brief.md` and
  `2026-10-20-prior-bugs-rule-and-noise.md`: in replays, the brief with its old suppression rule
  gave fewer confirmed findings, and the deficit was the same size as run-to-run noise (one arm
  moved by up to 6 between identical runs). The rule sentence, not the history, carried it.
- `recall_search` on the cca tenant for a live brief versus no brief measurement abstained
  (corpus gap) on 2026-10-31.

## Confounds I can name now

- **Diff mix.** Runs differ in size and kind; randomisation balances this only in expectation,
  and 15 holdout runs is small. In the PR 104 window ten descending PR range audits produced 57
  confirmed findings and no false positive, so a few such runs landing in one arm can move the
  gap by more than the effect.
- **Leakage outside the brief.** Codex auditors also get a block from RE-call's SubagentStart
  hook; the holdout skip there is required, and runs before it is deployed are out. In the hosted
  path the orchestrator still sees the records, and run directory files (`prior-records.json`,
  `memory-arm.json`) are readable by an auditor that looks.
- **fp-check is the judge, not ground truth.** CONFIRMED is the L2.5 verdict.
- **Assignment by run id.** Run ids are chosen by the orchestrator (stamps or names); the hash
  makes the arm unpredictable without computing it, but a second run of the same id lands in the
  same arm.
- **Model mix.** Auditors inherit the session model; a change of model inside the window moves
  every number in both arms.
- **Citation inflation.** The auditor text asks auditors to check each record's bug class, so a
  finding near a record has a ready "led to" claim. Prediction 3 and 4 are about attribution as
  reported, not about causation.
