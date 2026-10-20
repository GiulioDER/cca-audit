# Pre-registration: do prior fixes in the L1 brief make auditors confirm more real findings?

**Date:** 2026-10-20   **Status:** predicted, not yet measured

## The question

Replaying eight past CCA runs on the exact diffs they audited, how many CONFIRMED P1 plus P2
findings does Layer 2.5 produce when the Layer 1 auditors get (A) no prior-fixes block, (B) the
file-overlap PRIOR FIXES block from `bugs prior`, and (C) that block plus the RE-call similar-bugs
block from `bugs prior --similar`?

## What I predict

Each prediction is scored on its own, so the record says which source earned its place.

1. **B beats A.** Summed over the eight runs, arm B confirms between 2 and 6 more P1 plus P2
   findings than arm A, and B confirms more than A in 3 to 5 of the 8 runs.
2. **C adds little over B.** Summed over the eight runs, C minus B is between minus 1 and plus 1.
   The reason is measured, not guessed: on 2026-10-20 two of three paraphrased probe queries to the
   `recall-cca` server abstained, and only a near-verbatim summary returned `ok` hits, so I expect
   the similar block to be empty in most runs.
3. **No precision cost.** In each of B and C the false-positive rate (FALSE_POSITIVE over all
   verified findings) is at most 5 percentage points above arm A's.
4. **No leakage.** In each of B and C at most 1 finding restates a bug listed in its block without
   the diff reintroducing it.
5. **Noise is small enough to read 1 and 2.** Arm A run a second time on two runs (A′) differs
   from A by at most 1 confirmed finding in each of those two runs.

## What would falsify this

* Prediction 1: B minus A below 2 in total, or B ahead in fewer than 3 runs. A total above 6 also
  falsifies it, in the direction of a larger effect than I believe.
* Prediction 2: C minus B outside minus 1 to plus 1.
* Prediction 3: either arm's false-positive rate more than 5 points above A's.
* Prediction 4: 2 or more leaked findings in either arm.
* Prediction 5: A′ and A differ by 2 or more in either run. Then the run-to-run noise is as large
  as the effect predicted in 1, and predictions 1 and 2 are reported as inconclusive, not as
  confirmed or refuted.

## How it will be measured

**Corpus, chosen before writing the predictions above:** every closed DIFF mode run in
`~/.claude/cca-bugs/recorded` and `~/.claude/cca-bugs/pending` whose repository has at least one
older ledger row overlapping the files of its findings. Deferred runs are excluded (they dispatch
no Layer 1 auditors), and Codex runs saved without a repository name are excluded (the block needs
one). Exactly eight qualify:

| run | repository | tier | audited diff (base_commit to base_tree) |
|---|---|---|---|
| 20261012T223135Z | GiulioDER/RE-call | DEEP | 11 files, +1147 / -10 |
| 20261013T084136Z | GiulioDER/RE-call | STANDARD | 4 files, +186 / -3 |
| 20261013T112241Z | GiulioDER/RE-call | DEEP | 4 files, +115 / -3 |
| 20261013T160707Z | GiulioDER/RE-call | DEEP | 13 files, +1241 / -127 |
| 20261018T134705Z | GiulioDER/RE-call | DEEP | 42 files, +6031 / -264 |
| 20261018T210526Z | GiulioDER/RE-call | DEEP | 16 files, +692 / -266 |
| 20261020T094942Z | GiulioDER/cca-audit | DEEP | 5 files, +150 / -9 |
| 20261020T124906Z | GiulioDER/cca-audit | DEEP | 12 files, +1614 / -12 |

The plan asked for runs "with forward rows"; six of these are still pending (their pull requests
have not merged), which does not change the diff they audited, so they are included.

**Apparatus per run and arm:** a throwaway worktree of the repository detached at `base_commit`,
its index and working tree set to `base_tree` (`git read-tree -u --reset <base_tree>`), so
`git diff <base_commit>` is exactly the diff the original run audited. Then `/audit-fix no-fix`
at the run's original tier, which runs Layers 1 to 2.5 (and 2.6) and closes. The three arms differ
only in the Layer 1 brief:

* **A:** no PRIOR FIXES block.
* **B:** the output of `python -m cca_checks bugs prior --files <FILES> --repo-name <repository>
  --before <run date>`, run date taken from the run id.
* **C:** as B, plus up to three `recall_search` queries to the `recall-cca` server written from
  the diff, saved and passed with `--similar` and `--cross-repo`. `--before` applies to the
  similar rows too, which is what keeps the tenant from handing a run the very bug it found. The
  settled-decisions query of the pipeline text is not run in any arm: the project memory servers
  are not available uniformly across the two repositories.
* **A′:** arm A again, on runs 20261013T084136Z and 20261013T112241Z (the two smallest diffs).

Every arm uses the same auditor set (decided by the tier and the diff, which are identical across
arms) and the same model per role. The installed package is the current master.

**Metrics, by name:**

* `confirmed`: CONFIRMED P1 plus P2 findings in the run's L2.5 file, summed over runs per arm.
* `fp_rate`: FALSE_POSITIVE findings over all findings with a verdict at L2.5, per arm.
* `leaked`: findings in arms B and C that restate a bug listed in that run's block without the
  diff reintroducing it, judged by one `fp-check` pass over each arm's CONFIRMED findings and the
  block it was given.

**Apparatus checks, recorded with the result:** the B block is non-empty for every run (print it);
the C similar block's row count per run; the number of auditors per run is equal across arms; the
audited diff stat matches the table above.

Cost: 26 audit runs, about 260 agents.

## What I already know

* The original runs, single samples on older pipeline versions: 52 CONFIRMED P1 plus P2 over the
  eight runs (12, 4, 3, 6, 14, 4, 2, 7 in table order), and 11 false positives over 73 verified
  findings (15%). These are context, not an arm: the replay runs on the current pipeline.
* The `cca` tenant's paraphrase behaviour on 2026-10-20, above (memo `prior-bugs-in-brief`).
* Nothing in memory measures auditor variance between two identical runs, which is why A′ exists.

## Confounds I can name now

* **Stochastic auditors.** Two identical runs can differ; A′ estimates by how much, on two runs
  only.
* **The block as a prompt length change.** B and C prompts are longer; an effect may come from the
  extra instruction text rather than the history in it.
* **Self-confirmation.** A listed bug class may raise both the finding and the verifier's
  readiness to confirm it; the leakage check catches restated bugs, not this.
* **Ledger dating.** `--before` filters by the date of a row's merge, so a fix merged before the
  run date but written for a later change could still appear; rows are few enough to inspect.
* **Corpus skew.** Six of eight runs are RE-call; a result may not carry to other repositories.

## Result (2026-10-20)
**Status:** measured

Measured by the workflow `prior-bugs-replay` (run `wf_bc599911-a13`, 242 agents, none errored, about
16.2M subagent tokens), scored from the files by a script, not from the agents' own counts.

| run | A | B | C | A′ |
|---|---:|---:|---:|---:|
| 20261012T223135Z | 16 | 14 | 10 | |
| 20261013T084136Z | 4 | 4 | 3 | 4 |
| 20261013T112241Z | 0 | 0 | 2 | 0 |
| 20261013T160707Z | 8 | 6 | 7 | |
| 20261018T134705Z | 21 | 18 | 25 | |
| 20261018T210526Z | 11 | 3 | 2 | |
| 20261020T094942Z | 1 | 1 | 1 | |
| 20261020T124906Z | 10 | 8 | 5 | |
| **confirmed** | **71** | **54** | **55** | 4 |
| fp_rate | 3/81 = 3.7% | 1/60 = 1.7% | 6/66 = 9.1% | 1/5 |
| raw findings | 246 | 209 | 230 | 20 |
| leaked | | 0 | 0 | |

Scored prediction by prediction:

1. **B beats A: falsified, in the opposite direction.** Predicted +2 to +6; measured B minus A =
   **minus 17**, and B ahead in **0 of 8** runs (predicted 3 to 5). B produced fewer raw findings
   (209 against 246) and fewer P1 plus P2 to verify (60 against 81).
2. **C adds little over B: held.** Predicted minus 1 to plus 1; measured C minus B = **plus 1**.
   The reason I gave was wrong: the similar block was non-empty in 6 of 8 runs, not mostly empty
   (the probes before the run were not representative of diff-written queries).
3. **No precision cost: held for B, falsified for C.** B's rate is 2.0 points below A's; C's is 5.4
   points above, over the 5-point bound.
4. **No leakage: held.** 0 leaked findings in B and in C.
5. **Noise small: held,** on the two smallest runs only: A′ equals A exactly (4 and 4, 0 and 0).
   The spread on the large runs, where the B deficit is concentrated, is not measured.

**Gap, and what it means.** I predicted the history would make auditors find more; it made them
report less. The likeliest mechanism, not measured: the rule paired with the block ("never report a
listed bug unless the code reintroduces it") makes auditors suppress anything resembling a listed
bug, so they file fewer findings overall, including real ones. Run 20261018T210526Z carries most of
the loss (11 to 3). Fewer confirmations is not shown to be worse auditing: `confirmed` counts what a
haiku fp-check accepted, not bugs proved real, and the fp-check here was lenient (3.7% false
positives in A, against 15% in the original runs). What the data does support is narrow: with this
pipeline text, the block does not increase confirmed findings, and it does not improve them enough
to show in this metric.

**Apparatus checks.**
* B block non-empty: **6 of 8** runs, not 8 (the plan's check failed). 20261013T112241Z and
  20261013T160707Z have no older ledger rows for their own diff files; my corpus check had matched
  on the files of the findings instead. In those two runs B differs from A only by the empty block
  and its rule text, yet 160707Z still moved 8 to 6, which is a hint that noise on larger runs is
  bigger than A′ shows.
* C similar block non-empty: 6 of 8 runs (5, 1, 1, 3, 4 rows and 0, 0 where all three queries
  abstained).
* Auditor set equal across arms by construction (from tier and diff); audited diff stats match the
  table above.

**Deviations from the plan, all applied to every arm alike.**
* Every agent ran on haiku (the user's instruction for this run), including the verifier.
* Layer 2.5 was one fp-check per run and arm: no adversarial 2-of-3 panel on high-stakes P1s.
* Priorities were the auditors' own, merged by highest; no orchestrator re-map.
* Orchestration was a script (L1 auditors, a deterministic consolidation script, fp-check, leakage
  check), not the `/audit-fix` skill run 26 times.
* Run 1's auditors were dispatched by hand, the other seven runs' by the workflow, with identical
  prompts.

**Next.** Before treating the block as harmful, two things would separate the explanations: the
rule text without the block (to test the suppression mechanism), and A repeated on the large runs
(to measure the noise the B deficit sits inside).
