# Auditor memory attribution: each finding says whether memory led to it

**Date:** 2026-10-28   **Status:** design, approved in conversation; build waits (see Rollout)

## Intent

Since 0.13.0 every L1 auditor prompt carries a brief of prior bugs and accepted lessons at the
code under audit (Step 1 of `audit-fix.md`, `bugs brief` or the hosted `recall_records` block).
The auditor is told almost nothing about why it receives them: one template sentence, "use it to
look for the same class of bug". Nothing records whether a record was used, so its effect can
only be guessed from totals, and the 2026-10-20 replay showed totals move within run-to-run noise.

The owner's idea: an auditor that has to say how a memory shaped its work uses it better, the way
an agent forced to search before proposing does. Start at the finding, because that is what can
be measured.

## Goals

1. Every L1 finding states whether records from the brief led to it, and how.
2. The run records those citations, so confirmed findings can be attributed to memory.
3. The owner's success metric can be measured: **more confirmed findings per run in runs that had
   a brief**.

## Non-goals

- A per-record judgement ("checked, code clean", "not applicable"). That is a second step, after
  this one is measured.
- Any change to what the brief contains or how RE-call renders it. (Found while designing, kept
  separate: a LESSON line shows only the lesson's `rule`, not its `pattern`, which is the part
  that says what to look for. That is a change in RE-call's `recall_hooks/auditor_brief.py`.)
- The 556 of 886 ledger bugs with no anchors, which the local brief never shows.

## Anti-goals

- **Memory must never suppress or narrow a finding.** A citation adds attribution, nothing else.
- **No pressure to cite.** The wording must not imply that a finding without memory is weaker.
  If it does, auditors will cite records they did not use and the attribution becomes noise. On
  2026-10-20 one rule sentence moved confirmed findings by about the run-to-run noise, so wording
  is the main risk.

## Design

### The two fields (decided)

Every L1 finding object carries:

- `memory_ids`: the brief's record ids that led to this finding, in the brief's own spelling
  (for example `cca-audit:1433be742f45:BUG-001`, a `LESSON` id). An explicit `[]` when none did.
  Required on every finding, so "not filled" never reads as "memory did not help".
- `memory_how`: one sentence tying the record to the code, required when `memory_ids` is not
  empty, `""` otherwise. Example: "BUG-001 fixed a string `files` value split into characters;
  the new `_rows` repeats `list(entry.get(...))` on L4 input at forward.py:88."

"Led to" means the record made the auditor look at the place or the bug class it then reported. A
record merely consistent with a finding found independently is not cited.

### Where the instruction lives (decided)

In CCA, not in RE-call:

- Each of the nine L1 agent files: the two fields in `## Return value`, and a short `## Prior bugs
  and lessons` section: what the brief is (bugs CCA confirmed and fixed at or near this code, and
  patterns the owner accepted), what to do with each record (name its bug class, check whether the
  change reintroduces it or has a sibling of it), and when to cite. It states, once, that a finding
  with `memory_ids: []` is exactly as valid as one with citations.
- `audit-fix.md`: the Findings Schema gains both fields; the Step 1 template rule about the PRIOR
  BUGS block points at the agent file's section.
- The Codex skill takes the agent files as they are (`install-codex`), so it needs no own text.

### What the pipeline does with them (my call, the builder may refine)

- **L2 consolidation:** a merged finding takes the union of its sources' `memory_ids` and keeps
  every non-empty `memory_how`.
- **Validation without blocking:** a cited id that the run's brief did not show (compared with the
  `prior_brief.ids` PR 102 records for the local path, and with the rendered ids this build adds
  for the hosted path) is counted as `unlisted`, not refused. Measurement must not stop a run, and refusing would push orchestrators to edit auditor
  output.
- **`pipeline close`** adds to `summary.json` a `memory_attribution` object: findings citing
  memory, how many of those were CONFIRMED / FALSE_POSITIVE / other at L2.5, the cited ids that
  produced a CONFIRMED finding, and the `unlisted` count. A run with no brief reports `null`, not
  zeros.

### Measurement (decided by the owner: live, after PR 104's look)

The metric is the owner's: **confirmed L1 findings per run, runs with a non-empty brief against
runs with none.** On live runs this is confounded (a brief appears only where the code has
history, which may be buggier code), so the comparison is a difference of differences:

- **Before:** the brief/no-brief gap in confirmed findings per run over the PR 104 window, which
  already runs the new instructions without this protocol.
- **After:** the same gap over the runs after this protocol is installed.
- **Effect:** after gap minus before gap. The attribution fields add the direct view: how many
  confirmed findings cite memory.

A pre-registration (`/preregister`) states the prediction, the run counts and the falsifier, and is
committed before the protocol is installed.

**Which runs count (my call, settled after the builder check).** Runs are found from each run's
`summary.json`, not from scorecard rows, because a run with zero findings writes no scorecard row
and would silently drop out of "per run":

- **In:** closed DIFF runs at STANDARD or DEEP. A run with no L1 finding counts, with 0.
- **Out:** HUNT runs (their brief is the ledger's PRIOR FIXES form, a different block), deferred
  passes (they dispatch no L1 auditor), and FAST runs (P2 and P3 are never verified there, so a
  confirmed count is not comparable). The result states how many of each were left out.
- **Confirmed count:** L1-prefixed findings with a CONFIRMED verdict in the run's own stored L2.5
  output.

This is a different denominator from PR 104's pre-registration, which counted runs with at least
one scorecard row. That record is left as it is. The "before" gap for this study is computed
with the definition above over the PR 104 window, so before and after compare like with like.

**What "had a brief" means (my call).** The brief was non-empty as the auditors saw it:

- local path: the run's `local-prior.json` receipt lists at least one `BUG` or `LESSON` line
  (the same lines `prior_brief.ids` is built from; reading the receipt also covers runs closed
  before PR 102, whose `summary.json` has no `prior_brief`);
- hosted path: `prior_block` (summed over matches) plus `lessons_block` above 0. Both are already
  in every hosted DIFF run's `summary.json`, the PR 104 window included, so the signal needs no
  backfill.

Going forward this build also records the rendered ids for hosted runs in `prior_brief`, so the
`unlisted` check compares a citation with the records the auditor actually saw. In a hosted run
closed before this build, `unlisted` is reported as unknown, never as 0.

Measured 2026-10-28 over the summaries under `~/Documents/recall`, `~/Documents/cca-audit`,
`~/Documents/agent-memory-bench*` and `~/.codex/worktrees` since 2026-10-25: 39 DIFF runs, 19 with
a non-empty local brief, 17 with an empty one, 3 with no receipt; none on the hosted path; all
STANDARD or DEEP. So both sides of the split fill at roughly six runs a day.

The split lives in `benchmarks/harness/l1_precision.py` or a sibling script (builder's choice).

## Rollout

1. Wait for PR 104's pre-registered look at 15 runs (window from `20261028T200105Z`), so that
   measurement is not contaminated. Then write and commit this pre-registration.
2. Build, test (prose tests red-proved; the summary counts with real fixtures), merge.
3. Install from master as on 2026-10-28, and record the first run id of the new window.

## Constraints

- Tests follow the owner's red-proof rule: each new test fails on its intended assertion against
  the pre-change code or a deliberate mutation before it counts.
- Heavy test runs go to VPS2 (`bash ~/.claude/bin/vps2-pytest`).
- The ledger is append-only: nothing here rewrites `bugs.jsonl` rows.
- No text from the brief may leak into a HUNT of a foreign repository beyond what Step 1 already
  allows; the fields only carry ids the brief listed.

## Left to the builder

- Exact wording of the agent-file section, within the anti-goals.
- The JSON shape of `memory_attribution`, and whether the split by brief lives in
  `l1_precision.py` or a sibling script.
- Whether the deferred pass (which reloads L1 findings) needs any change to keep the fields.

## Revision 2026-10-31: a random holdout replaces the before gap

Measured 2026-10-31 with `benchmarks/harness/memory_gap.py` (after its time-window fix) over
`~/Documents` and `~/.codex/worktrees`, 2026-10-28T20:01:05Z to 2026-10-31T11:06:21Z: 24 DIFF runs
at STANDARD or DEEP, every one with a non-empty brief, none without (excluded: hunt 2, deferred 4,
fast 3, untimed 6). The anchors written in that window put a record in front of every run, so the
"before" gap above has an empty arm and cannot be computed. The owner chose, 2026-10-31, a random
holdout instead. This section supersedes "Measurement" where they differ; the text above stays as
the record of what was first decided.

- **Assignment.** A DIFF run is in the holdout arm when `int(sha256(run_id), 16) % 3 == 0`, so
  about one run in three. The rule is deterministic, so anyone can recompute a run's arm from its
  id, and nobody choosing a run id can see its arm without computing it.
- **What the holdout withholds.** The CCA brief only: the local `bugs brief` block and the hosted
  PRIOR BUGS and lessons blocks render as they do when the ledger has nothing for the change. The
  auditors are not told that anything was withheld, so both arms read the same prompt except for
  the records. The project's own SETTLED DECISIONS memory (Step 1 item 2) is not withheld.
- **Where it is recorded.** Whichever command builds the brief writes
  `.claude/audits/runs/<run_id>/memory-arm.json` in both arms: `{"run_id", "arm": "brief" |
  "holdout", "rule"}`. `summary.json` carries `memory_arm`. In a holdout run `memory_attribution`
  is `null`.
- **Scope.** DIFF runs only. HUNT runs, deferred passes and runs whose mode cannot be read are
  never withheld (arm `brief`, with the reason recorded when the mode was unreadable).
- **Metric.** Confirmed L1 findings per run, brief arm minus holdout arm, by assigned arm (a brief
  arm run whose brief happened to be empty stays in the brief arm). The attribution fields remain
  the direct view.
- **End.** The holdout is on while a single constant says so; a follow-up PR turns it off after
  the pre-registered look.
