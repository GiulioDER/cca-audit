# Pre-registration: does the prior-bugs brief change false positives, verification load or run time, by random holdout

**Date:** 2026-11-20   **Status:** predicted, not yet measured

Companion to `2026-10-31-auditor-memory-holdout.md` (the parent record). Same runs, same arms,
same looks; this record adds the metrics the parent does not name. It is committed before any of
them has been computed by arm.

## The question

In the parent record's included runs, does the brief arm differ from the holdout arm in (S1) false
positive L1 findings per run, (S2) L1 precision, (S3) L1 findings verified per run, or (S4) run
wall time, and by how much?

## What I predict

1. **S1, false positives per run, brief minus holdout: about -0.2** (rough range -1.0 to +0.5).
   The brief tells auditors which bug classes were real near this code, which could steer them away
   from weak claims a little; it says nothing about false positives directly.
2. **S2, L1 precision: brief about 0.88, holdout about 0.86**, difference about +0.02 (rough range
   -0.05 to +0.08).
3. **S3, L1 findings verified per run, brief minus holdout: about +1.0** (rough range -1 to +3),
   moving with the parent's confirmed count, since most verified findings confirm.
4. **S4, wall minutes per run, brief minus holdout: within plus or minus 60 minutes.** The brief
   is a few hundred tokens in each auditor prompt; run wall time is dominated by fix layers and by
   waits for the owner.
5. **At the first look every 95% interval includes 0** (S1, S3, S4 Welch; S2 the per-arm Wilson
   intervals overlap). With about 15 holdout runs nothing smaller than a large effect is
   detectable, and I expect "inconclusive" on all four.

## What would falsify this

- S1: a point estimate below -1.0 or above +0.5.
- S2: a difference below -0.05 or above +0.08.
- S3: a point estimate below -1 or above +3.
- S4: a point estimate beyond plus or minus 60 minutes.
- Prediction 5: any S1, S3 or S4 interval that excludes 0, or S2 Wilson intervals that do not
  overlap, at the first look.

A claim that the brief **reduces** false positives, verification load or run time needs the
interval to exclude 0 in that direction. Four metrics are read at once, so one interval excluding 0
by chance is likely (about 19% across four independent 95% intervals): a single such interval is
reported as a lead, not a finding, unless it also excludes 0 at the parent's second look, if that
look happens.

## How it will be measured

**Runs and arms.** Exactly the parent record's included set: `memory_gap.runs_over` over
`--root ~/Documents` then `--root ~/.codex/worktrees`, `--since 2026-11-04T12:58:53Z`, filtered by
`memory_gap.arm_rows`, analysed by assigned arm. Exclusions are the parent's, counted by reason.

**Looks.** Only at the parent's looks (15 holdout runs; 30 only if the parent's primary interval
includes 0 at 15), on the same data pull. No other reading by arm.

**Metric names** (per run directory; an L1 finding is an id matching `l1_precision.PREFIX`):
- **S1 false positives per run**: L1 rows in the run's `L2.5.json` with verdict FALSE_POSITIVE; a
  run with none counts 0. Gap and Welch 95% interval by `memory_gap.welch_interval`.
- **S2 L1 precision**: per arm, the sum of CONFIRMED over the sum of CONFIRMED plus
  FALSE_POSITIVE, L1 rows only, with a Wilson 95% interval. UNCERTAIN, DEFERRED, DUPLICATE and STALE
  rows are not in the denominator and are counted separately.
- **S3 L1 findings verified per run**: all L1 rows in `L2.5.json`, any verdict. This is the cost
  proxy: each row is one fp-check verification. Gap and Welch interval.
- **S4 wall minutes per run**: `summary.json` `completed_utc` minus the run's start, where the
  start is `memory_gap.run_time` (earliest dispatch `checked_utc`, else the run id as a stamp). A
  run with no start or no `completed_utc` is out of S4 only, counted. Gap and Welch interval.

**Apparatus.** A script `benchmarks/harness/memory_secondary.py` will compute these, reusing the
`memory_gap` and `l1_precision` functions named above, committed before the look. Before it reads
any in-window run, it must reproduce this known case: run `20261028T133834Z` (RE-call worktree
`cca-pr788-ops`, outside the window) gives S1 0, S3 1, S4 76.9 minutes.

**Not measured: token cost.** No run file records tokens or agent count; S3 stands in for it.

## What I already know

- Parent record baseline (PR 104 window, 24 runs, all briefed): 5.708 confirmed per run, sd 4.80.
- `2026-10-28-l1-auditor-instructions.md`, 30-run look (2026-10-31): L1 precision 0.880 (243/276,
  Wilson 0.837 to 0.914), confirmed per run 8.10. Implied false positives about 1.1 per run.
- Four DIFF runs before the window that still exist on disk, measured 2026-11-20: wall minutes
  76.9, 406.8, 343.4, 36.8; S3 1, 7, 6, 13; S1 0 in all four. Wall time varies by a factor of ten
  for reasons that have nothing to do with the brief.
- In those runs `L1.json`, `L2.json` and `L2.5.json` were written within seconds of each other, so
  file times cannot time the audit and verification layers on their own. A speed measurement of bug
  triage itself needs per layer timestamps the pipeline does not record.
- On 2026-11-20 I ran the parent's `memory_gap.py` to check progress and so saw the parent's
  primary gap at 3 holdout runs (interval -9.8 to +13.4). I computed none of S1 to S4 by arm. The
  arm assignment was checked on that day: every included run's recorded arm equals its hash, on
  both clients.
- Earlier replays (`2026-10-20-prior-bugs-in-brief.md`): fp rate 3.7% without the block, 1.7% with
  it, under the old suppression rule and inside run to run noise.

## Confounds I can name now

- **S4 is mostly waiting.** Fix layers, test runs on VPS2 and the owner's approvals dominate wall
  time; a gap in S4 is far more likely to come from the diff mix than from the brief.
- **Diff mix and small n**, as in the parent: a few large runs in one arm move every metric.
- **fp-check is the judge**, and the brief does not reach it differently by arm, so S1 and S2 move
  only if the auditors file different findings.
- **S3 couples to the parent's primary**: if the brief adds confirmed findings it adds verified
  ones, so S3 is not independent evidence of cost.
- **Model mix** inside the window moves all four metrics in both arms.

## Correction (2026-11-20), appended after the record was committed; the text above is unchanged

Under "What I already know", the fourth wall time, 36.8 minutes, belongs to run
`20261025T194500Z-recovery-audit` and was computed by reading the id's first sixteen characters as
a stamp. The registered S4 start is `memory_gap.run_time`, which reads that whole id as no time and
finds no dispatch file, so under the registered definition that run has no S4 (it would be counted
in `s4_untimed`). The other three wall times and the known case (`20261028T133834Z`: S1 0, S3 1,
S4 76.9) are reproduced by `benchmarks/harness/memory_secondary.py --run`. No prediction depends on
the 36.8 figure.
