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
