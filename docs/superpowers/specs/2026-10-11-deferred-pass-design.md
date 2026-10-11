# Offer a gated second pass over deferred findings

Status: approved in conversation 2026-10-11, spec awaiting review.

Two decisions taken in that conversation frame everything below:

- **Scope:** the second pass covers everything the first run deferred: P3 findings, and P2 findings
  the first run left unfixed (skipped by `p1-only`, or reported unverified on FAST). Not P3 alone.
- **Approach:** a `DEFERRED` run mode enforced by the gate, not a prose rewrite of § Second Pass.

## Problem

Read on `origin/master` at `0ec8c47`, 2026-10-11:

1. **The second pass runs outside the gate.** § Second Pass of `audit-fix.md` never calls
   `pipeline start`, so `verify_commit_allowed` returns `allowed=True, "no CCA run is open"` and the
   guard lets the commit through. Of the fix layers only L5 (tests and lint) runs: no `fp-check`, no
   L5.5 regression diff, no L5.6 red-state proof, no L6 architect gate.
2. **P3 findings are never verified, yet are counted as verified.** Step 2.5 says "P3 are deferred
   anyway" and `cca-fp-check.md` says "P3 findings are not verified". But `_check_counts` refuses
   L2.5 unless `confirmed + fp + uncertain + duplicate` equals L2's unique count, and the ledger must
   hold one row per verdict. So every P3, and every P2 on FAST, is filed under a verdict it never
   received. Filed as CONFIRMED, it raises the auditor's scorecard precision on no evidence.
3. **The pool is read from commit prose.** § Second Pass parses the `P3:` section of the last commit
   message, while the run already stores the findings as JSON in `.claude/audits/runs/<run_id>/`.
4. **L3's deferred count is free text.** It lives in `--detail "<D> deferred"`; nothing structured
   says which findings were deferred.
5. **Nothing offers the pass.** The Output Summary ends with a hint line.

Re-measure on any checkout:

```bash
grep -n "Second Pass" -A12 cca_checks/plugin/commands/audit-fix.md
grep -n "P3 are deferred anyway" cca_checks/plugin/commands/audit-fix.md
grep -n "P3 findings are not verified" cca_checks/plugin/agents/cca-fp-check.md
grep -n 'elif layer == "L2.5"' -A20 cca_checks/pipeline_state.py
```

## Goal

After a fixing run commits and closes, the user is asked whether to run a second pass over the
findings it deferred, with a RUN or SKIP recommendation and its reasons. A yes starts a CCA run that
owes every layer a fixing run owes, verifies every deferred finding before it is edited, and cannot
defer anything again.

## Design

### 1. The deferred pool, defined once

The **pool** of a run R is the set of finding ids that R's L2.5 output marks `DEFERRED`, united with
the ids R's L3 output lists under `deferred`. It never includes UNCERTAIN findings, findings parked
by the three-cycle cap, false positives or duplicates: those need a human or need nothing.

Which findings land in the pool, by tier:

| Tier | At L2.5 (unverified, verdict `DEFERRED`) | At L3 (confirmed, not planned) |
|---|---|---|
| FAST | every P2 and P3 | none |
| STANDARD, DEEP | every P3 | every P2 when `p1-only` |

### 2. What every run records

**2.1 `start --base <rev>`.** `start` resolves `<rev>` with `git rev-parse` and stores the sha as
`base_commit`. The default is `HEAD`; the `commit N` path passes `--base HEAD~N`. A repository with
no commits stores `null`. The deferred run uses it as its scope (§ 4). `start` also validates
`--mode` against `("DIFF", "HUNT", "DEFERRED")`, which it does not today.

**2.2 L2 output shape.** The L2 file must be a JSON array of objects, each with a unique string
`id` and a `priority` in `P1`, `P2`, `P3`; its length must equal `--unique`. Other keys are kept as
written. This is what lets a later run find a finding by id.

**2.3 L2.5 counts and output.** Two new counts, both defaulting to 0:

- `--deferred <D>`: findings passed on unverified (the L2.5 column of the table in § 1).
- `--stale <S>`: allowed only in a `DEFERRED` run (§ 4).

The sum rule becomes `confirmed + fp + uncertain + duplicate + deferred + stale == L2 unique`.

The L2.5 file must be a JSON array of objects, each with an `id` that appears in the stored L2 file
and a `verdict` in `CONFIRMED`, `FALSE_POSITIVE`, `UNCERTAIN`, `DUPLICATE`, `DEFERRED`, `STALE`. Ids
are unique, and the tally per verdict must equal the counts on the command line. A file that
disagrees with its own counts is refused.

The ledger rule on STANDARD and DEEP becomes: rows for this run equal `total - deferred`. A deferred
finding gets no ledger row, because it received no verdict. A STALE finding gets a row with verdict
`STALE`.

**2.4 L3 counts and output.** A new `--deferred <D>` count, default 0, replaces the free text in
`--detail`. The L3 file must be a JSON object with two arrays of id strings, `to_fix` and
`deferred`, disjoint, of lengths `--to-fix` and `--deferred`. Every id in either array must be
CONFIRMED in the stored L2.5 file, and `to_fix + deferred` must equal L2.5's `confirmed`: every
confirmed finding is either planned or deferred, so none can fall out of the record unnoticed. This
replaces today's weaker `to_fix <= confirmed`.

**2.5 `close` writes a summary.** A schema 2 run that closes writes
`.claude/audits/runs/<run_id>/summary.json`:

```json
{"schema": 1, "run_id": "...", "tier": "STANDARD", "mode": "DIFF", "no_fix": false,
 "base_commit": "<sha or null>", "commit": "<HEAD at close>", "parent": null,
 "completed_utc": "...", "pool": ["CODE-004", "DOC-002"], "human_pending": 1}
```

`human_pending` is L2.5's `uncertain` count plus the ledger outcome rows for this run whose `result`
is `parked`. `abort` writes no summary, so an abandoned run never produces an offer. A 0.10.x run
writes none either.

### 3. The offer

A new read-only command:

```bash
python -m cca_checks pipeline deferred [--run <run_id>] [--write <file>]
```

Without `--run` it reads the newest summary by `completed_utc`. It prints the pool (id, priority,
file, line, claim, taken from the stored L2 file), the counts by priority, `human_pending`, whether
the pass is offered, the recommendation, and the reasons. It also lists older summaries whose pool
is non-empty and not yet consumed, so a later run with an empty pool cannot hide an earlier one.

**Not offered**, with the reason printed:

| Condition | Reason |
|---|---|
| pool is empty | nothing was deferred |
| `no_fix` is true | the run was told not to edit |
| `mode` is `DEFERRED` | a deferred run never offers a third pass |
| `mode` is `HUNT` | out of scope for this version; the pool is listed, not offered |
| consumed | a completed `DEFERRED` run already names this run as its `parent` |

**Recommendation**, evaluated in order:

1. `human_pending > 0` → **SKIP**: findings await a human decision; settle them first, since a
   cosmetic pass over code a human has yet to rule on is wasted or conflicting.
2. any P2 in the pool → **RUN**.
3. P3 count `>= DEFERRED_MIN_P3` (3) → **RUN**.
4. otherwise → **SKIP**: a deferred run costs at least four agent passes (`fp-check`, the scorecard,
   `differential-review`, `architect-reviewer`) whatever the pool's size.

`DEFERRED_MIN_P3` is a named constant and an unmeasured starting value. The recommendation is
advice: SKIP does not refuse the run.

**`--write <file>`** writes the pool's full finding objects, copied from the parent's stored L2
file, as a JSON array: the L1 input of the deferred run. It refuses a file outside
`.claude/audits/` (the rule `record` already applies) and refuses when the pass is not offered.

**Where the offer happens.** The new last step of the Output Summary: after `pipeline close`
succeeds, the orchestrator runs `pipeline deferred`. When the pass is offered it asks one
question carrying the recommendation and its reasons. Yes starts § 4 in the same session. No ends
the turn, and `/audit-fix deferred` still works later, until a completed deferred run consumes the
pool. The run is already closed when the question is asked, so the Stop hook does not interfere.

**Fallback when no summary exists** (the worktree that ran the audit is gone, or the run predates
this change): the orchestrator parses the `P3:` section of the last CCA commit into the L1 file as
finding objects marked `"source": "commit-message"`, and starts with `--from commit:<sha>`. The
gate accepts a sha that is an ancestor of HEAD, stores `base_commit: null`, and the scope criterion
of § 4 is then reported as not checked. The report and the commit both say the source was degraded.
Every item still goes through L2.5.

### 4. The deferred run

`/audit-fix deferred` (and `audit+fix deferred` in Codex). Each step is the normal step with the
differences below; the prose says so step by step, rather than describing a separate pipeline.

| Step | In a `DEFERRED` run |
|---|---|
| open | `pipeline deferred --write .claude/audits/out/L1.json`, then `pipeline start --mode DEFERRED --from <run_id> --tier <T>`. T is DEEP when the parent was DEEP or any pool item is `high_stakes` or sits on a high-stakes path by Step 0.5; STANDARD otherwise. The run inherits the parent's `base_commit`. |
| L1 | no auditors; record the file `--write` produced, `--detail "loaded <N> from <run_id>"` |
| L2 | re-anchor each item to its current file and line (the parent's own fixes may have moved it); keep the parent's priority; merge duplicates |
| L2.5 | one `fp-check` over every item, P2 and P3 alike, with criterion (b″) below; `--deferred` must be 0 |
| L2.6 | as usual |
| L3 | plan every CONFIRMED item; `--deferred` must be 0; UNCERTAIN goes to a human |
| L4 to L5.5 | as usual |
| L5.6 | `verify --no-proofs --record`: the pool holds no P1 by construction (§ 1), and L2 keeps the parent's priority |
| L6 | the fix to finding mapping covers every CONFIRMED item, P3 included, because those are the point of the run |
| commit | `fix(<scope>): N deferred fixes from CCA (<TIER> tier, parent <run_id>)`, one line per item: FIXED, STALE, FALSE_POSITIVE or UNCERTAIN |
| close | the summary records `parent`, which consumes the parent's pool; no offer follows |

**Criterion (b″), MODE=DEFERRED,** replaces (b) in the `fp-check` prompt and in `cca-fp-check.md`:

- *In scope:* the cited code lies inside `git diff <base_commit>`, the code the parent audited or
  fixed. A finding on code outside it is FALSE_POSITIVE with the reason `out of scope`.
- *STALE:* the issue is present at the parent's `commit` (`git show <commit>:<file>`) and absent
  now, because later work moved, rewrote or fixed it. Distinct from FALSE_POSITIVE, which means the
  issue was never there.

Ledger rows in the deferred run carry the deferred run's `run_id` and the **original** finding's
`auditor` and `category`, so a P3 confirmed now credits the auditor that raised it.

### 5. Gate rules

In `cca_gate_core.py` and `pipeline_state.py`:

- `start --mode DEFERRED` is refused when `--from` is missing; when the parent has no summary and
  is not a `commit:<sha>` ancestor of HEAD; when the parent's `mode` is `DEFERRED` or `HUNT` or its
  `no_fix` is true; when the parent's pool is empty or consumed; when `--tier` is FAST; and when `--no-fix` is passed. A
  `commit:<sha>` parent has no summary, so only the ancestor, consumed and FAST checks apply to it.
- `--from` is refused outside `--mode DEFERRED`.
- L2.5 and L3 refuse `--deferred > 0` in a `DEFERRED` run. L2.5 refuses `--stale > 0` outside one.
- `required_layers` and `predecessors` do not change. A `DEFERRED` run owes exactly what a fixing
  run of its tier owes; the only thing that differs is what L1 contains, which the prose states.
- The Stop hook and the commit guard need no change: both already read the required layers.

**Compatibility.** The state schema stays 2; `parent` and `base_commit` are new optional fields and
the new counts default to 0. A run opened before the upgrade that records L2, L2.5 or L3 afterwards
is refused if its file lacks the new shape, and the refusal names the shape. 0.11.0 is unreleased,
so no released contract changes. 0.10.x runs keep the 0.10.x rules.

**Scorecard.** `cca_scorecard.py` stops counting `STALE` rows as disposed (today every verdict row
increments `disposed`, which would yield-flag an auditor whose findings went stale). They are
already outside precision, which counts only CONFIRMED and FALSE_POSITIVE.

### 6. Prose and docs

- `audit-fix.md`: the Contract table (L2.5 and L3 rows gain the new counts, a `deferred` row is
  added), Step 0.6 (`--base`), Step 2.5 (which findings are DEFERRED, the file shape), Step 2.5b
  (deferred findings get no row), Step 3 (the file shape), the Output Summary (the offer), and
  § Second Pass rewritten as § 4.
- `cca-fp-check.md`: MODE=DEFERRED and criterion (b″); the "P3 findings are not verified" line
  says this holds in a normal run only.
- `cca-fix-planner.md`: P3 handling points at the deferred pass.
- Codex `SKILL.md`: the offer is a chat question; the commands are the same.
- `README.md` (the round 2 paragraph), `docs/pipeline-diagram.md`, `claude-code/README.md`, and the
  0.11.0 entry of `CHANGELOG.md`.

## Testing

Standing rule for this repository: a behaviour test counts only after it has been seen to fail
against a named, plausible mutation of the production line it protects, in its intended assertion.
Each test's docstring records the node id, the mutation and the failure reason.

| Test | Mutation that must turn it red |
|---|---|
| L2.5 refuses counts that ignore `deferred` in the sum | drop `deferred` from the sum |
| L2.5 refuses a file whose tallies disagree with the counts | skip the tally comparison |
| L2.5 ledger check expects `total - deferred` rows | compare against `total` |
| L2.5 refuses `--stale` outside DEFERRED, `--deferred` inside it | delete each mode check |
| L3 refuses `to_fix + deferred != confirmed` | restore `to_fix <= confirmed` |
| L3 refuses an id that is not CONFIRMED in L2.5 | skip the cross-file check |
| `start` refusals, one test each (no `--from`, DEFERRED, HUNT or no-fix parent, consumed pool, FAST) | delete the corresponding check |
| `close` writes a summary whose pool is L2.5 DEFERRED united with L3 deferred | build the pool from L3 alone |
| `abort` writes no summary | write the summary in `abort` too |
| `pipeline deferred`: each not-offered row and each recommendation branch | invert or delete that branch |
| `--write` copies the parent's L2 objects for exactly the pool ids | copy all L2 objects |
| a DEFERRED run's commit is refused until L6 reads APPROVED | special-case DEFERRED in `required_layers` |
| scorecard leaves STALE out of `disposed` | count every verdict row |
| CLI round trip: parent run, close, `deferred --write`, child start to close | (integration; red against a child start that skips `--from` validation) |

Run before claiming done: `python -m pytest -q`, `ruff check`, `python -m pyright` (CI runs pyright
before pytest).

## Out of scope

- Deferred passes for HUNT runs (listed, not offered).
- Calibrating `DEFERRED_MIN_P3` against real runs.
- Running the pass without asking.
- Verifying P3 findings inside the first run (it would raise the cost of every run, including the
  ones whose user skips the pass).
- A third pass, or deferral inside a deferred run.

## Corrections made while planning (2026-10-11)

Three lines changed after approval, each to match the code it describes:

- `pipeline deferred` lost `--json`: every `pipeline` subcommand already prints one JSON object.
- The pool lists `claim`, not `title`: the Findings Schema has no `title` key.
- `start --mode DEFERRED` also refuses `--no-fix`: a deferred run exists to fix, and
  `/audit-fix deferred no-fix` runs `pipeline deferred` and stops instead.
