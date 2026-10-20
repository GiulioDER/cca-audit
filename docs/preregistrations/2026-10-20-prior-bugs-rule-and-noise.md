# Pre-registration: is the B deficit the rule, the history, or noise?

**Date:** 2026-10-20   **Status:** predicted, not yet measured

Follows `docs/preregistrations/2026-10-20-prior-bugs-in-brief.md`, whose arm B (the file-overlap
PRIOR FIXES block plus its rule) confirmed 54 P1 plus P2 findings against 71 for arm A (no block)
over eight runs. Two measurements separate the explanations.

## The questions

1. **Rule:** on the six runs where the B block was non-empty, how many CONFIRMED P1 plus P2 findings
   does arm R produce, where R is the B brief with the block's rows removed (the empty PRIOR FIXES
   form and the same rule sentence, exactly what B looked like on the two runs whose block was
   empty)?
2. **Noise:** on the five largest runs, how far does a second run of arm A (A3) land from the first?

## What I predict

1. **The rule explains most of the deficit.** Summed over the six runs (A 63, B 48 in the first
   replay), R lands between 44 and 56, nearer B than A.
2. **The deficit is larger than the noise.** Summed over the five largest runs (A 66, B 49, a
   deficit of 17), the total absolute difference between A3 and A, run by run, is between 4 and 10,
   and no single run differs by more than 5.

## What would falsify this

* Prediction 1: R above 56 (the rows themselves, not the rule, cost the findings) or below 44.
* Prediction 2: a total absolute difference of 11 or more, or any run moving by 6 or more. If the
  total reaches 17, the B deficit of the first replay is within noise and none of its conclusions
  about B stand.

## How it will be measured

The same apparatus as the first replay, unchanged: the replay trees (`base_commit` with
`base_tree` checked out), the same briefs, haiku for every agent, the auditor set per run, the
deterministic consolidation script, one haiku fp-check per run and arm, scored from the files by the
same script.

* **R** on 20261012T223135Z, 20261013T084136Z, 20261018T134705Z, 20261018T210526Z,
  20261020T094942Z, 20261020T124906Z. Brief: arm A's brief with the empty PRIOR FIXES form and the
  rule sentence added, byte for byte as in arm B's brief for 20261013T160707Z.
* **A3** on 20261012T223135Z, 20261013T160707Z, 20261018T134705Z, 20261018T210526Z,
  20261020T124906Z (the five with the most raw findings in arm A). Brief: arm A's, unchanged.
* Metric `confirmed`: CONFIRMED P1 plus P2 at L2.5, as before. Also reported: raw findings and
  `fp_rate`.

Cost: 11 run-arm jobs, about 100 agents.

## What I already know

* The first replay's per-run counts (A, B): 16/14, 4/4, 21/18, 11/3, 1/1, 10/8 on the R runs; and
  16, 8, 21, 11, 10 for A on the A3 runs. Raw findings: A 246, B 209 over eight runs.
* A′ matched A exactly on the two smallest runs (4 and 4, 0 and 0), which says nothing about the
  large ones.
* On 20261013T160707Z, where B differed from A only by the empty block and the rule, A was 8 and B
  was 6: a small hint for prediction 1.

## Confounds I can name now

* **Two runs of A3 against one of A**: the A side of the first replay is itself one sample, so the
  noise estimate compares two samples, and a single large swing can dominate it.
* **The rule and its framing are one change here.** R adds both the empty heading and the rule
  sentence; it cannot say which of the two does the work.
* **Haiku auditors and a lenient fp-check**, as before: `confirmed` is what that verifier accepted.

## Result (2026-10-21)
**Status:** measured

Measured by the same workflow script (run `wf_89b8fb70-678`, 108 agents, none errored, about 8.2M
subagent tokens), scored from the files.

| run | A | B | R | A3 |
|---|---:|---:|---:|---:|
| 20261012T223135Z | 16 | 14 | 10 | 18 |
| 20261013T084136Z | 4 | 4 | 3 | |
| 20261013T160707Z | 8 | 6 | | 7 |
| 20261018T134705Z | 21 | 18 | 23 | 19 |
| 20261018T210526Z | 11 | 3 | 7 | 5 |
| 20261020T094942Z | 1 | 1 | 0 | |
| 20261020T124906Z | 10 | 8 | 5 | 8 |

(Every cell is scored from the files. For 160707Z A3 the verifier reported 8 confirmed in its own
return value, while its L25.json holds 7, all P2; the file is the measurement.)

1. **The rule explains the deficit: held.** Predicted R between 44 and 56, nearer B. Measured
   **R = 48**, equal to B's 48 on the same six runs (A 63). Adding only the empty heading and the
   rule sentence costs as many confirmed findings as the full block did.
2. **The deficit is larger than the noise: falsified.** Predicted a total absolute A3 minus A
   difference of 4 to 10 with no run above 5. Measured per-run differences **+2, -1, -2, -6, -2**:
   total **13**, maximum **6** (20261018T210526Z, A 11 then 5). The noise is below the first
   replay's 17-finding deficit on these runs, but not by much.

**Gap, and what it means.** I expected the noise to be small; it is not. A single arm moves by up to
6 confirmed findings between two identical runs, and the run that carried most of B's loss in the
first replay (210526Z, 11 to 3) is also the noisiest (A 11 then 5). Against the mean of A and A3 on
the five large runs (61.5), B's 49 is a deficit of 12.5, the same size as the noise total of 13. So
the first replay's "B confirms fewer" is not established beyond noise. What does stand is
prediction 1: the rule alone reproduces B to the finding, which points at the rule, not at the
history rows, for whatever deficit there is.

**Apparatus.** Unchanged from the first replay; R's construction was checked to reproduce arm B's
brief byte for byte on a run whose block was empty.

**Next.** The decision this leaves is about the rule sentence, not the history: either soften it (so
auditors are not told to withhold findings) or measure with enough repeats per arm to see an effect
of this size through noise of this size.
