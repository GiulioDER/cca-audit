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
