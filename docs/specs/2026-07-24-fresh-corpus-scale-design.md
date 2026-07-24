# Scaling the fresh corpus to n≈30 clean bugs — design

**Date:** 2026-07-24
**Status:** proposed, awaiting review
**Supersedes nothing.** Extends the pilot in `benchmarks/` (PR #15) whose results are
`benchmarks/results/RESULTS.md`.

## Why

The pilot's headline — **83% recall on memorized code vs 43% on novel code** — rests on 3/7 clean
bugs. Two problems, one of which is worse than the sample size.

1. **Wide error bars.** 3/7 has a 95% CI running roughly 16–75%. The number cannot carry a
   public claim.
2. **A confound in the comparison itself.** The 83% arm is BugsInPy (2018–2020 libraries) and the
   43% arm is the fresh 2026 corpus. Those two arms differ in *four* ways simultaneously: era,
   repository selection, fix-hunk size (BugsInPy hunks are tighter; fresh hunks run 9–56 lines),
   and recognition. Recognition is the variable the claim is about; the other three ride along
   uncontrolled. The comparison is cross-corpus, not controlled, and a reviewer retires it in one
   sentence.

Fixing (2) is nearly free once we are mining at n≈45: mine **one** corpus by **one** process and
split it by the recognition probe. Recognition then becomes the only difference between arms.
This also retires BugsInPy from the write-up rather than defending a corpus we have already
shown to be 12/12 contaminated.

## What this changes

| | Pilot (2026-07-15) | This design |
|--|--|--|
| clean bugs | 7 | **≥ 30** |
| comparison | BugsInPy vs fresh (cross-corpus) | clean vs recognized **within one corpus** |
| recognition probe | single-shot | **3-vote majority** |
| `drop_reason` | absent (runs predate the field) | **recorded and scored** |
| corpus freeze | after the fact | **pre-registered, committed before auditing** |

## Precondition — pin the tool under test

**Blocking. Nothing below runs until this is resolved.**

As of 2026-07-24, verified state:

- ✅ **The checkers are versioned and in sync.** `cca_tautology_check.py`, `cca_scorecard.py` and
  both test files are byte-identical (sha256) between `~/.claude/tools/` and `origin/master` of the
  **sentiment-agent** repo at `tools/cca/`. No action needed.
- ❌ **The orchestrator prompt is not.** `~/.claude/commands/audit-fix.md` is 41 937 B (2026-07-24
  09:13) against 33 998 B in `cca-audit/claude-code/commands/` (2026-07-23) — a ~205-line delta
  carrying Step 2.5b Disposition Ledger, Step 2.6 Auditor Scorecard, Fix Attempt Budget & Journal,
  Step 5.6 Red-State Proof and the three-verdict L2.5 panel. `cca-architect-reviewer.md` differs by
  9 lines. Neither file is versioned in sentiment-agent at all.
- ❌ **The divergence runs both ways.** The `cca-audit` copy (on the unmerged
  `feat/language-backend-layer`) carries the `python -m cca_checks capabilities` Language gate,
  DETERMINISTIC COVERAGE reporting, and `ast` / `clippy` in the artifact exemption — all absent from
  the deployed copy. Neither side is a superset.

So the *checkers* CCA invokes are reproducible, but the *pipeline that invokes them* is not: no
commit anywhere describes the orchestrator that would produce these numbers. That is the worse half
to be missing, because `audit-fix.md` is where the L2.5 gate semantics — the thing this benchmark
measures — are actually defined.

A benchmark whose tool exists only as one machine's untracked working state is unreproducible, and
this project has no standing to publish a contamination critique of other people's benchmarks while
its own result cannot be re-derived. Required before the audit stage:

1. ✅ **Reconcile `audit-fix.md` — DONE 2026-07-24.** Three-way merge (base `origin/master`, ours
   `feat/language-backend-layer`, theirs the deployed copy): **zero conflicts, purely additive**
   — 565 base + 21 branch + 142 deployed = **728 lines**. Both change-sets verified present, heading
   sequence verified monotonic, repo and deployed copies now hash-identical.
   `cca-architect-reviewer.md` needed no merge (master == branch) — took the deployed version, which
   adds the `revised_findings` field and the FIX_JOURNAL attempt-budget rule. **Still uncommitted.**

   ⚠️ **Sequencing consequence:** the reconciled file calls `python -m cca_checks capabilities`,
   which exists **only on `feat/language-backend-layer`**, not on `origin/master`. So the reconciled
   `audit-fix.md` cannot live on master until that branch merges — **the version pin in step 2 cannot
   point at master until the Rust/language-backend branch lands.** Either merge it first, or pin the
   benchmark to the branch and say so in `results/`.
2. Record the resulting **commit SHA in `results/`** alongside the scores, plus the sentiment-agent
   `origin/master` SHA that pins `tools/cca/`.
3. Re-verify that the deployed copies and the pinned commits agree by hash before the run — same
   discipline as the VPS `md5 == master` deploy check, and the same reason: *deployed* and *merged*
   are different claims.

## What the 2026-07-24 CCA change predicts

The L2.5 panel moved from "≥2 of 3 fail to refute → CONFIRMED, else UNCERTAIN" to a three-verdict
resolution where a **drop requires 3/3 refutation with file:line evidence attacking the mechanism**,
and any split or evidence-free refutation escalates to UNCERTAIN instead of dropping.

That is strictly more conservative about dropping, so this run carries a **pre-registered
prediction**: relative to the pilot's fresh corpus, the fp-check drop rate falls from 42%, wrong
drops fall from 2/8, and **satpy #3367 — the pilot's one FATAL, a correctly localized finding the
gate killed — is recovered.** State the prediction before the run and report it whether or not it
holds; a gate change that is only ever evaluated after the fact is not evidence.

Note the direction of the trade: a more conservative gate should *raise* confirmed recall and may
*lower* specificity. Both are measured, so report both rather than the flattering one.

## Corpus construction

The binding constraint in `harness/mine_fresh.py` is the **search pool**, not the filters:
it issues one `gh search prs --limit=120` query, and `TARGET = 22` never binds because the pool
is exhausted at 10 survivors (8.3% survival). For 30 clean bugs at the pilot's 70% clean rate we
need ~43 mined, hence a pool of **~520 PRs**.

### Levers applied

| Lever | Effect | Quality cost |
|---|---|---|
| Shard the search by month (2026-02 … 2026-07) | ~6× pool | none |
| Add label variants: `bug`, `type: bug`, `kind/bug`, `bugfix`, `defect` | ~2× pool | none — label vocabulary is per-repo convention |
| Extend the window to 2026-07-24 | +2 weeks | none |
| Widen the star band 150–20 000 → 100–60 000 | ~1.3× | slight: larger repos are likelier to be *recognized*, which the probe measures rather than hides |

Target after the first four levers: **45–55 candidates**.

### Levers deliberately NOT applied

- **Dedup stays at 1 bug per repo.** Held in reserve. Relaxing it introduces within-repo
  correlation, which silently narrows the CI we are trying to earn honestly.
- **The regression-test requirement stays.** A merged fix that ships its own regression test is
  the signal that this is a real, well-defined bug with unambiguous ground truth. Dropping it
  would reach n=30 fastest and would destroy the thing that makes the corpus worth citing.
- **`≤3 source files` and `≤60 changed lines` stay.** Localization within ±3 lines is only
  meaningful against small fix hunks.
- **Python only.** CCA v3.3 ships a Rust backend and it deserves a benchmark, but mixing
  languages breaks comparability with the pilot. Separate experiment.

If the four safe levers do not reach 45 candidates, **do not relax the quality filters** — widen
the window backwards toward the model cutoff instead and re-check the recognition rate, or accept
a smaller n and report it. Reaching 30 by relaxing ground-truth quality is a worse outcome than
reporting n=22 honestly.

## Pre-registration protocol

1. Run the widened miner.
2. Commit `harness/fresh_manifest.json` **together with the exact search parameters that produced
   it**, before the recognition probe or any auditor runs.
3. Only then run the probe and the audit.

The sample must be fixed before the tool sees it, and a reader must be able to verify that from
the commit order. This costs nothing and it is the specific credential the "precision you can
prove" positioning rests on — a benchmark that cannot demonstrate its own sample wasn't chosen
post-hoc has no standing to criticise other people's benchmarks.

## Recognition probe

Currently single-shot. It is now load-bearing for the entire headline: it defines which bugs are
in which arm. SecLLMHolmes documents that this class of LLM judgement is non-deterministic and
sensitive to superficial features such as variable naming.

**Change: 3-vote majority.** The probe is short and cheap relative to the audit, so the cost is
marginal. Record the per-vote verdicts, not just the majority, so that disagreement rate is
itself reportable — a bug where the votes split 2–1 is weaker evidence than a unanimous one, and
that distinction belongs in the results rather than being flattened.

## Arms and staging

Both arms come from the **same frozen corpus**. They are audited on different schedules.

- **Now — clean arm.** All bugs the probe certifies unrecognized. Target ≥30. Full audit.
- **Later — recognized arm.** The ~15 recognized bugs. Audited only if the clean-arm recall holds
  up at tighter error bars.

Mining and probing cover **all** candidates in this pass even though only the clean arm is
audited. The recognized bugs are identified and frozen now so that the control arm, when it runs,
uses the same pre-registered corpus and the same selection process — not a second, separately
mined sample that would reintroduce the confound this design exists to remove.

## Scoring

Unchanged from the pilot except where noted:

- Ground truth = the fixing commit's own diff. **Catch** = a finding within ±3 lines of a fix hunk
  on the buggy file. **Specificity** = staying quiet at that spot on the fixed file.
- **Finding scope: all fp-check-confirmed findings (P1 + P2 + P3).** Not P1-only. This measures
  what CCA actually surfaces to a user.
- **Drop adjudication** against ground truth, as already implemented in `score.py`: a drop landing
  on a fix hunk in the buggy file is a **wrong drop**, and **FATAL** if nothing else caught that
  bug; a drop landing there in the fixed file is a **correct drop** that bought specificity.
- **`drop_reason` is now live** and must be reported. Both stored runs predate the field, so the
  refuted-vs-inconclusive split currently scores `unlabeled`. This run measures it for the first
  time. A refutation ("the code disproves this") and an inconclusive ("may be real, this file
  can't settle it") are opposite signals that a bare drop count hides.
- Report recall with a **bootstrap CI**, not a bare fraction.

## Cost

The pilot's fresh audit was 61 agents / 4.27M tokens / ~34 min for 10 bugs. Scaling to the clean
arm alone (~30 bugs):

| Stage | Estimate |
|---|---|
| Mining (`gh` API, deterministic) | minutes, no model cost |
| Recognition probe, 3-vote, ~45 candidates | small |
| **Audit, clean arm only (~30 bugs)** | **~180 agents, ~13M tokens, ~1.7h** |
| Recognized arm (deferred) | ~+6M tokens if later approved |

## Success criteria

This is a measurement, not a hypothesis test — there is no recall threshold to hit. It succeeds if:

1. **n_clean ≥ 30** without relaxing the quality filters.
2. Recall is reported **with a bootstrap CI**, and that CI is materially tighter than the pilot's.
3. The refuted/inconclusive drop split is **measured** rather than `unlabeled`.
4. The corpus is verifiably **frozen before** the audit ran.

It fails, and should be reported as failing rather than patched, if reaching n=30 requires
dropping the regression-test filter or the per-repo dedup.

## Risks

| Risk | Handling |
|---|---|
| Widened star band raises the recognition rate, so clean yield falls below 70% and n_clean < 30 | Probe-before-audit surfaces this **before** the expensive stage; mine another shard and re-probe |
| Recall moves materially from 43% at larger n | That is the point of the exercise. Report the new number; the pilot figure was explicitly flagged as wide-CI |
| Re-probing the 10 pilot bugs returns different verdicts than in July | Expected and informative — 3-vote reduces it, and disagreement rate gets reported rather than hidden |
| Another session mutates `cca-audit` concurrently | Branch from `master`, not from the unpushed `feat/language-backend-layer` |

## Out of scope

- Rust / multi-language arm.
- Any competitor comparison. Martian Code Review Bench and `Agent-Field/pr-af` are the follow-on
  tracks; both are strictly better after this corpus exists, because both compare against a number
  that currently has 3/7 error bars.
- Fix-correctness. This measures detection and localization only.
