# Fix audit loop: a run does not finish with bugs in its own fixes

Date: 2026-11-22. Status: design, awaiting the owner's approval.

## Intent

The owner's words (2026-11-21): CCA "should fix real bugs, and once done those result are tested and
confirmed fixed. It cannot introduce new bugs or it create a loop where we need 2 3 4 5 runs to
finish."

The evidence behind it. The self hunt of `cca_checks` (run 20261120T201246Z) confirmed 31 defects. Its
22 P2 fixes were then audited by DEEP DIFF run 20261120T225224Z, which confirmed 7 P2s. All 7 were in
or next to the fixes:

| Kind | Findings | What went wrong |
|---|---|---|
| Incomplete fix (a sibling site left) | BUG-001, SEC-001, NUM-001 | `errors="replace"` added to one ledger reader of four; the safe git lookup still accepted a git inside the clone; the parent run read only from a file that exists after close |
| New bug in a new mechanism | BUG-002, BUG-003 | the new proof timeout could hang on Windows; the new dedupe set crashed on a list id |
| New code quality or doc defect | CODE-001, DOC-001 | a duplicated helper; a missing caveat |

Today the original code is audited by up to 10 auditors at L1, but the fix code is checked only by
gates that ask narrower questions: L5 (do the tests pass), L5.5 (does each hunk stay within its
finding's scope), L5.6 (does each P1 test fail on the pre-fix code) and L6 (does every fix map to a
finding). Nothing hunts bugs in the fix code at L1 depth, so the next run is the first thing that does.

## Goals

1. Before a run commits, its fix code has been audited for bugs at L1 depth (STANDARD and DEEP).
2. Every other site of a pattern a fix changed is listed by code, and each is fixed or justified.
3. A fix that adds a new mechanism is flagged by code, audited by the matching auditor, and carries a
   test of that mechanism's own failure.
4. A fix much larger than its finding needs must say why before L5.5 reviews it.
5. The loop is bounded by the existing per-finding 3-cycle cap and journal.
6. The effect is measured: the share of confirmed findings in a run that sit in an earlier CCA run's
   fix lines, before and after, pre-registered.

## Non-goals

- No guarantee that a fix never introduces a bug. The guarantee is narrower and enforceable: a run
  does not close while a confirmed P1 or P2 in its own fix code is unresolved.
- No change to L1, L2 or L2.5, and no splitting of audit scope (discussed 2026-11-21, separate work).
- No new agent type. The fix audit reuses the existing auditor and verifier agents.

## Anti-goals (a failure even if it works)

- The fix audit grows into a second full audit: it reports bugs that existed before the fixes and
  pulls them into the run. Those go to the deferred pool unless the sibling sweep listed them.
- The loop runs without converging and nobody is told: every cycle is journaled and capped.
- FAST runs become slow: FAST gets only the deterministic checks, which run in seconds.
- An open run of the current version is suddenly refused at commit by a layer it never knew about.

## Constraints

- The disposition ledger and the bug ledger are append-only (owner, 2026-10-22).
- The commit guard rework (branch `claude/awesome-albattani-0ff2e9`, and `claude/frosty-elion-0135d2`)
  has uncommitted changes to `cca_checks/plugin/hooks/cca_pipeline_guard.py` and
  `cca_checks/plugin/installer.py`. This work must not touch either file. Reusing existing agents
  keeps `installer.py` out of it.
- Claude and Codex parity: `audit-fix.md` and the Codex `SKILL.md` change together.
- Every new behaviour test is proven red against the pre-change code or a mutation (user rule
  2026-09-08). Heavy tests run on VPS2 only.
- The measurement is pre-registered and committed before it is taken (user rule 2026-08-15).

## Approaches considered

| | Approach | Trade-off |
|---|---|---|
| **A (chosen)** | A new gated layer, L5.4, between L5 and L5.5: a deterministic tool, then (STANDARD/DEEP) auditors on the fix hunks, verified, looping to L4 under the cap | Closes the gap inside the run at L1 depth, scoped to the fix hunks, so it costs a few agents per cycle. Needs gate, state and prose changes. |
| B | Give L5.5's differential reviewer a bug-hunting brief and the deterministic report | Least code, but one reviewer still covers all the fix code, which is today's gap. |
| C | A child CCA DIFF run on the fix diff that must close clean before the parent commits | Strongest scrutiny, but the full L1 roster and verification on every loop, and the gate allows one open run per checkout, so it needs the most gate change. |

The owner chose A on 2026-11-22, and chose that FAST runs get only the deterministic checks.

## Design

### Pipeline order

```
L4 implement ─▶ L5 tests ─▶ L5.4 fix audit ─▶ L5.5 regression ─▶ L5.6 red proof ─▶ L6
                    ▲             │
                    └── confirmed P1/P2 in fix code, or an unresolved sibling: back to L4
```

L5.4 is owed whenever L4 is owed, on every tier. On FAST it is the deterministic checks only. It is a
fix verdict layer: recorded against the tree fingerprint, stale after any edit, like L5 to L6. So a
fix made for an L5.4 finding changes the tree and makes L5 and L5.4 owed again. That is the loop, and
the gate already enforces it.

The layer is recorded by its tool, never by hand (like L2.6 and L5.6):
`python -m cca_checks fixaudit record ...`.

### Part 1: the deterministic report (`python -m cca_checks fixaudit check`)

Input: the fix diff, and the run's `L4.json`. The fix diff is NOT the diff from `base_commit`: in a
DIFF run that also holds the feature under audit, and a deferred run inherits its parent's base, so
the checks would run over code that is not fix code. Instead, `pipeline record L3` (the last record
before any fix edit) stores `prefix_tree`, the git tree id of the working tree as it stands, written
through a temporary index so the real index is untouched. The fix diff is `prefix_tree` to the
working tree. If the tree id cannot be written, L3 is refused, as an unknown fingerprint is today.
Output:
`.claude/audits/runs/<run_id>/fixaudit.json`, stamped with the tree fingerprint. Three sections.

**Sibling sweep.**
- Automatic patterns: each removed or modified line in a non-test file, normalized (whitespace
  collapsed), kept only when it carries a call or a string literal, so lines such as `return None`
  or `)` never become patterns.
- Declared patterns: every `L4.json` entry gains `sibling_patterns` (a list of fixed strings or
  regexes) for siblings that are not textual copies, such as NUM-001's "reads the parent from the
  summary only". The list may be empty, and then `sibling_reason` must say why.
- Each pattern is searched over the tracked files outside the fix hunks. Each hit gets an id.
- Every hit needs a disposition in `.claude/audits/out/L5.4-siblings.json`: `fixed`, which the tool
  checks by the hit no longer matching at the current tree, or `not_applicable` with a reason.
- A sibling fix is part of the original finding's fix, not a new one: its file joins that entry's
  `files` in `L4.json`, and L5.5 and L6 are handed the sibling list, so the edit is in scope there
  (not SCOPE_CREEP, not a phantom fix) and `caused_by` and the size check attribute it to that finding.

**New mechanism flags.** Added non-test lines are matched against a table of mechanisms, each with
the auditors it routes to:

| Mechanism | Python examples | Auditors |
|---|---|---|
| process launch | `subprocess`, `Popen`, `os.system` | security, bug |
| lock or concurrency | `threading`, `Lock`, `fcntl`, `msvcrt.locking`, `flock` | bug, data |
| timeout or wait | `timeout=`, `.wait(`, `signal.alarm` | bug, perf |
| parser of untrusted input | `json.loads`, `re.compile`, `ast.parse`, `csv.` | bug, data |
| file write or format | `write_text`, `open(..., "w"/"a")`, `os.replace` | data |

Shell installers get their own rows (traps, `$ErrorActionPreference`, redirections). A flagged fix's
`L4.json` entry must name `failure_test`, a test node that exercises the mechanism's own failure (the
timeout firing, the lock contended, malformed input). The tool checks that the node exists in its
file. Whether the test really exercises the failure is judged by the auditors and by L6.

**Size check.** Per fix, the changed non-test lines in its `files`, split evenly where fixes share a
file. A fix over the threshold needs `size_reason` in `L4.json`, which L5.5 receives. The threshold is
not guessed: the build measures the size of past fixes from the run archives and sets it at their
90th percentile, recorded in the pre-registration.

### Part 2: the auditor pass (STANDARD and DEEP)

- **Who.** For each fix, the auditor of its finding's category, plus the auditors its flagged
  mechanisms route to. At most 4 auditors per pass, chosen by how many fixes need each.
- **What they see.** The fix hunks only, the finding each hunk fixes, the deterministic report and
  the FIX_JOURNAL rows. Scope: bugs in the lines the fixes added or changed, and in the sibling sites
  the sweep listed. Anything else they notice is reported as `out_of_scope` and goes to the deferred
  pool.
- **Second and later passes** audit only the hunks that changed since the last pass. The tool keeps
  the audited diff in the run directory to compute that.
- **Verification.** Every finding goes through `fp-check` (a fix audit mode of the existing verifier)
  before it can send a fix back, so an auditor's guess never costs a repair cycle.
- **Ids.** Fix audit findings carry their own ids, `FA-<pass>-<n>`, plus `fix_id` naming the fix
  that introduced them. They never reuse an L1 finding id or FIX id.
- **Dispositions.** Confirmed P1 and P2 go back to L4. Each counts against the cycle cap of the
  original finding whose fix introduced it, and gets a FIX_JOURNAL row with gate `L5.4`. P3 goes to
  the deferred pool.
- **Resolved, as code decides it.** A confirmed fix audit finding is resolved only when the next pass
  re-verifies it (`fp-check`, same fix audit mode) and returns `RESOLVED` at the current tree. A
  changed hunk alone does not resolve it.
- **The cap, made machine-readable for L5.4.** Today the 3-cycle cap lives in prose and in
  `FIX_JOURNAL.md`. The fix audit needs it in code, so each fix audit cycle is written by
  `python -m cca_checks fixaudit journal` to `runs/<run_id>/fixaudit-journal.jsonl` (append-only:
  finding, fix id, pass, verdict, reason), and `FIX_JOURNAL.md` keeps getting its row as now. The tool
  counts L5.4 cycles per original finding from that file. A finding whose fix audit cycles reach 3 is
  parked there: its fix is reverted whole, and the original finding parked `UNCERTAIN → human`, as
  the cap does today. No run commits a fix with a known bug it introduced. Making the cap code for
  the other gates (L5, L5.5, L6) is out of scope here.
- **Red proof.** A fix made for a fix audit finding owes the user's red proof rule like any behaviour
  fix. Its baseline cannot be the pre-L4 snapshot (the bug did not exist there, so every real test
  would read TAUTOLOGICAL). It is the tree the fix audit pass audited: the tool keeps that tree id per
  pass, and `tautology verify` gains a `--baseline-tree` option to restore it. L5.6 covers these
  proofs. Fix audit findings never enter the L2.5 counts, so `_planned_p1` and `--no-proofs` are
  unchanged.
- **Where the verdicts go.** A side file, `.claude/audits/FIXAUDIT_LEDGER.jsonl`, append-only, one row
  per verdict. Not the disposition ledger: none of its readers filter on a layer, and
  `bugs/ledgers.load_ledger` keeps the later of two rows for a fix, so a fix audit row there would
  replace the L1 disposition that links the run to its commit. No bug ledger record is created: the
  bug never reached a commit. A scorecard view of fix audit precision is later work.

### Recording L5.4

`fixaudit record` refuses unless, at the current tree:
1. `fixaudit.json` exists and is current;
2. every sibling hit has a valid disposition;
3. every flagged fix names an existing `failure_test`, and every oversized fix a `size_reason`;
4. on STANDARD/DEEP, a verified fix audit file covers every changed fix hunk, and no confirmed P1 or
   P2 in it is unresolved (`RESOLVED` in a later pass, or parked in the fix audit journal).

On FAST, conditions 1 to 3 block exactly as on the other tiers: the owner chose that FAST gets the
checks, and a check that only reports would be advice. FAST has no auditor pass, so it has no loop
that needs a cap: fixing a sibling hit removes it, and the checks converge without one.

### Gate and state

- `cca_gate_core`: `L5.4` joins `LAYERS`, `FIX_VERDICT_LAYERS`, `TOOL_LAYERS` and `HEADINGS`.
  `required_layers` adds it after L5 whenever fixes are owed, on every tier. `predecessors` gives
  L5.4 ← L5, L5.5 ← L5.4, L5.6 ← L5.4, and L6 ← L5, L5.4 (+ L5.5, L5.6 on STANDARD/DEEP).
- Only runs started by the new version owe L5.4: `pipeline start` writes `"fix_audit": true` into
  the state, and the gate requires L5.4 only when that flag is set. An open run of the old version
  keeps its old requirements.
- `pipeline_state`, `pipeline resume` and the report learn the new layer.
- `audit-fix.md` and the Codex `SKILL.md` get Step 5.4, the contract row, and the re-read cue after
  Step 5 ("the tests went green; the fix code has not been audited yet").
- Deferred runs owe L5.4 like any other run.

### Measurement

The metric is the share of CCA fixes whose `caused_by` names an earlier CCA fix commit, the derived
edge `cca_checks/bugs/caused_by.py` already computes by blame. The baseline is the history before the
install; the result is the runs after it. Known undercount: a fix that only adds lines blames nothing.
A second, direct check on the next real pair of runs: the share of run N+1's confirmed findings inside
run N's fix hunks (7 of 7 in the 2026-11-21 pair). The prediction, its sample size and its stopping
rule are written in `docs/preregistrations/` and committed before the install.

### Acceptance cases

The seven fix-induced findings of run 20261120T225224Z are the fixtures. Expected, not yet checked:
the automatic sweep lists BUG-001's other readers and SEC-001's other git launches; a declared pattern
covers NUM-001; the mechanism table flags BUG-002 (timeout) and BUG-003 (a set built from untrusted
rows, through the parser row). Each case becomes a test, shown red before the tool exists.

### Install timing (decided at install, not now)

The memory holdout's window is open (look due about late December to late January). L5.4 adds wall
time to every run, which moves that study's secondary metric for wall time in both arms. Whether to
install inside the window as a recorded mid-window change, or after the look, is the owner's call at
install time.

## Left to the builder

- The exact normalization and filters of automatic sibling patterns, provided the acceptance cases pass.
- The full mechanism table beyond the rows above, and the shell rows.
- Where the fix audit file lives and its schema, following `layer_outputs.py`.
- The `fp-check` fix audit mode's wording.
- How the auditor pass is prompted, within the scope rules above.

## Calls I made (for the owner to check)

- At most 4 auditors per fix audit pass.
- Fix audit P3s go to the deferred pool, not back to L4.
- Fix audit verdicts go in their own append-only file, not the disposition or bug ledger (changed
  after the builder check showed the disposition ledger's readers would mix them up).
- The fix diff starts at a tree stored at L3, not at `base_commit`.
- A sibling fix counts as part of the original finding's fix.
- The cap becomes code for L5.4 only; the other gates keep today's prose cap.
- A fix audit fix is proven red against the tree its pass audited.
- On FAST the three checks block; there is no auditor pass and no cap.
- The size threshold is set from measured past fixes, not guessed.
- Only runs started after the change owe L5.4.
