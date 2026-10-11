# Gate every step of the CCA pipeline

Status: approved in conversation 2026-10-10, spec awaiting review.
Sub-project 2 of 4 from the 2026-10-10 request (the others: remote execution of blocked verifiers,
a cross-project bug ledger with a `cca` recall tenant, a performance report). This spec covers only
the gating work.

## Problem

0.10.0 made four layers non-advisory: a `PreToolUse` guard refuses `git commit` until L1, L2, L2.5
and L6 carry verdicts, plus L5, L5.5 and L5.6 once fixes exist. The user reports that the steps the
guard does not check are the ones agents skip: L0.4 (hunt pre-flight), L2.6 (scorecard), L3 (fix
plan) and L4 (implement).

Reading `origin/master` at `d2317de` shows the skip is worse than one missing layer each:

1. **Skipping the L4 record disables three gates.** `required_layers` adds the fix layers only when
   `state.fixes_applied > 0`, and `fixes_applied` is set only by `record L4 --fixes N`. An agent that
   edits code and never records L4 leaves `fixes_applied == 0`, so the guard stops requiring L5, L5.5
   and L5.6. The gate trusts the self report it exists to check.
2. **`pipeline close` checks nothing.** `close()` appends `COMPLETE` to `EXECUTION_LOG.md` and deletes
   the state file whatever the state holds. Closing an incomplete run is a silent skip that the log
   then records as a success.
3. **Order is never checked.** `record` accepts any layer at any time, so L6 can be recorded before
   L4, and a REVISE loop that re-edits code after L6 leaves the old L5 to L6 verdicts standing.
4. **Nothing gates a run that never commits.** `no-fix` runs and runs that stop to summarise early
   never reach `git commit`, so the guard never fires.

Re-measure items 1 and 2 against any checkout:

```bash
grep -n "fixes_applied > 0\|def close" -A3 cca_checks/pipeline_state.py
```

## Design

### 1. Detect fixes from the tree, not from the report

`pipeline start` stores a baseline fingerprint in the state file:

```json
"base": {"head": "<git rev-parse HEAD>", "tree": "<sha256>"}
```

`tree` is the sha256 of `git diff HEAD --binary` followed by every untracked, non-ignored file
(`git ls-files --others --exclude-standard`) as `path\0sha256(content)\n`, sorted by path. `.claude/`
is gitignored, so the run's own state never perturbs it.

Fixes are owed when `fixes_applied > 0` **or** the current fingerprint differs from `base`. If the
fingerprint cannot be computed (git missing, not a repository), the answer is "changed": failing
open here would rebuild hole 1.

The standalone guard computes the same fingerprint with its own copy of the function, pinned to
`pipeline_state`'s by test, following the existing table duplication pattern.

### 2. Every step is a layer

`LAYERS` gains `L0.4`. Required layers become:

| Condition | Required |
|---|---|
| always | L1, L2, L2.5 |
| MODE=HUNT | L0.4, detail must read `PASS` |
| STANDARD or DEEP | L2.6 |
| not `no-fix` | L3, L6 (L6 must read `APPROVED` to commit) |
| fixes owed (section 1) | L4, L5; plus L5.5 and L5.6 in STANDARD or DEEP |

In HUNT mode the run must exist before L0.4, so `pipeline start` moves to Step 0.4 for hunts. Hunt
already forces DEEP, so the tier is known there.

**Tool-recorded layers.** L2.6 and L5.6 are deterministic commands, so the command records them:
`cca_scorecard.py --record` and `cca_tautology_check.py verify --record` call
`pipeline_state.record(..., source="tool")`. `pipeline record L2.6|L5.6` from the CLI is refused
with the command to run instead, and the gate requires `source == "tool"` for both. This does not
make the record unforgeable; an agent with a shell can write the file. It makes forgetting
impossible, and forging a deliberate act, which is the same bound the 0.10.0 docstring states.

### 3. Order, checked at record time

The state gains a monotonic counter `seq`; each record stores `seq` instead of relying on
second-resolution timestamps. Predecessors:

| Layer | Must follow |
|---|---|
| L1 | L0.4 (HUNT only) |
| L2 | L1 |
| L2.5 | L2 |
| L2.6 | L2.5 |
| L3 | L2.5, and L2.6 in STANDARD or DEEP |
| L4 | L3 |
| L5 | L4 |
| L5.5, L5.6 | L5 (either order, may run in parallel) |
| L6 | L5, L5.5, L5.6 when fixes are owed; otherwise L3 |

A layer is satisfied only if its latest record has a higher `seq` than the latest record of each
predecessor. Re-recording L4 after a REVISE therefore invalidates L5 to L6 without any extra state,
which is the repair loop the pipeline already describes.

**Order alone is not enough after a REVISE.** An agent that re-edits code after L6 REVISE and then
re-records only L5.5 and L6 satisfies every predecessor check, because L4 and L5 still sit below
them. So L5, L5.5, L5.6 and L6 each store the tree fingerprint (section 1) at record time, and the
commit guard and `close` require all four to equal the current fingerprint. Any edit after a verdict
makes that verdict stale. (Found by the 2026-10-10 performance report, defect 3.)

`record` refuses an out-of-order layer with exit code 2 and a JSON body naming the missing
predecessor and the `audit-fix.md` heading to re-read. The commit guard and `close` apply the same
check, so a hand-edited state file meets it too.

### 4. Counts that must agree

New optional flags, required for the layers named:

| Layer | Flags | Check |
|---|---|---|
| L2 | `--unique N` | stored |
| L2.5 | `--confirmed C --fp F --uncertain U --duplicate D` | C + F + U + D == N |
| L2.5 (STANDARD/DEEP) | none | rows in `AUDITOR_SCORECARD.jsonl` with this `run_id` == C + F + U + D |
| L3 | `--to-fix K` | K <= C |

The ledger check gates Step 2.5b, which is currently prose only. The pipeline text changes to say
the ledger's `run_id` **is** the pipeline's `run_id` (today it says `<short>`, undefined). Sub-project
3 builds on these rows.

### 5. Status vocabulary, close and Stop

**`n/a` no longer satisfies a required layer.** Today the CLI accepts `--status n/a` and the guard
blocks only `skipped` and `failed`, so `n/a` passes any gate (`cca_checks/__main__.py`, the
`pl_record` `--status` choices). A required layer must be `done`. `n/a` and `skipped` remain valid
only for a layer the condition table does not require for this run. A layer with nothing to do
(L2.5 with zero P1/P2) is recorded `done` with zero counts, which renders differently from a skip.

`close()` refuses unless the run is complete, and names what is missing. `abort` stays the only way
to retire an incomplete run.

**`no-fix` runs must close.** Step 2.5 says "If `no-fix`: STOP here" before the L2.5 record call and
never calls `close`, so every `no-fix` run leaves its state open and blocks the user's next unrelated
commit until someone aborts it. The prose moves the STOP after the record and adds
`pipeline close`; `close` accepts a `no-fix` run once L1, L2, L2.5 (and L2.6 in STANDARD or DEEP)
are satisfied.

"Complete" means: every required layer satisfied in order; for `no-fix`, L1, L2, L2.5 (and L2.6 in
STANDARD or DEEP) are enough.

A new standalone `Stop` hook, `cca_stop_guard.py`, reads the payload's `cwd`, finds the run as the
commit guard does, and:

| State | Action |
|---|---|
| no open run | allow |
| L6 recorded `BLOCKED` | allow: the run is waiting for a human, and the commit stays refused |
| complete but not closed | block: "run `pipeline close`" |
| incomplete, `stop_refusals < 3` | increment `stop_refusals` in the state, block with the next owed layer and its heading |
| incomplete, `stop_refusals >= 3` | allow, and append `STOPPED_INCOMPLETE` to `EXECUTION_LOG.md` once |

The hook keeps its own cap rather than relying on `stop_hook_active`, so the bound is per run and
visible in the state file. The exact Stop hook payload and block contract is verified against the
current Claude Code docs during implementation, before the hook is written.

`install --print-hook` prints both hooks. Arming stays opt-in, as in 0.10.0.

### 6. Codex

Codex has no hooks. The Codex skill instructs `python -m cca_checks pipeline status
--require-complete` before any summary; it exits non-zero naming the owed layers. This is weaker than
a hook and the docs say so.

### Compatibility

State files written by 0.10.x lack `base` and `seq`. For those, fixes owed falls back to
`fixes_applied`, and the order check is skipped. A run started under 0.10.x finishes under 0.10.x
rules; every new run gets the new ones.

### Prose

`audit-fix.md`: the Contract table gains L0.4, the count flags and the tool-recorded rows; Step 0.4
gains `pipeline start --tier DEEP --mode HUNT`; Steps 2.6 and 5.6 lose their manual record commands.
The Codex `references/pipeline.md` copy is regenerated from it as today.

## Testing

Per the standing rule, every new behaviour test is first run against the pre-change code at
`d2317de` (or a named mutation where the symbol is new) and must fail in its intended assertion
before it counts. Each test's docstring records the baseline and the failure.

| Invariant | Red proof |
|---|---|
| tree changed with no L4 record still owes L5, L5.5, L5.6 | `d2317de` guard allows the commit |
| `close` on an incomplete run is refused | `d2317de` `close()` returns a log path |
| L6 recorded before L4 is refused | `d2317de` `record` accepts it |
| re-recorded L4 invalidates an earlier L6 | `d2317de` commit allowed |
| an edit after L6 APPROVED, re-recording only L5.5 and L6, is refused | `d2317de` commit allowed |
| `--status n/a` on a required layer does not satisfy it | `d2317de` commit allowed |
| a `no-fix` run with L1, L2, L2.5 recorded can `close` | mutate the no-fix branch of completeness |
| HUNT run without L0.4 `PASS` cannot commit | `d2317de` has no L0.4 |
| L2.5 counts that do not sum to L2 unique are refused | mutate the sum check to `True` |
| ledger row count mismatch is refused | mutate the ledger count to skip |
| CLI `record L2.6` is refused | mutate the source check |
| Stop hook blocks 3 times, then allows and logs once | mutate the cap to 0 |
| Stop hook allows on L6 `BLOCKED` | mutate the BLOCKED branch |
| guard and `pipeline_state` agree on fingerprint and requirements | existing matrix test, extended |
| 0.10.x state file still evaluates under old rules | delete the compatibility branch |

## Out of scope

Remote execution of blocked verifiers, the bug ledger and `cca` tenant, and performance changes.
Each gets its own spec.
