# Pre-registration: does the fix audit layer (L5.4) cut bugs introduced by CCA's own fixes?

**Date:** 2026-11-22   **Status:** predicted, not yet measured

Design: `docs/superpowers/specs/2026-11-22-fix-audit-loop-design.md`. Branch `claude/fix-audit-loop`.

## The question

Two numbers, each answerable by a rate:

- **Q1 (cross-run share).** Of the CCA fixes committed after L5.4 is installed, what share have a
  `caused_by` edge naming an earlier CCA fix commit, compared with the same share for the fixes
  committed before the install?
- **Q2 (next-run share).** When a CCA run's fixes are audited again by a later CCA run on the same
  code (the next run on that branch, or a deferred or DIFF run over the fix diff), what share of the
  later run's confirmed P1 and P2 findings lie inside the earlier run's fix hunks?

And one setting, measured for the build:

- **Q3 (size threshold).** The 90th percentile of past fix sizes, used as `SIZE_THRESHOLD`.

## What I predict

- **Q1:** the after-install share is at most half the before-install share (a cut of 50% or more),
  most likely a cut of 50% to 80%. I do not predict the baseline value itself: it is computed after
  this record is committed.
- **Q2:** the baseline is 7 of 7 (the 2026-11-21 pair: hunt run 20261120T201246Z, whose P2 fixes
  run 20261120T225224Z then audited). After install, across the first 5 qualifying pairs, the
  pooled share is at most 30%, most likely 10% to 25%.
- **Q3:** the 90th percentile of non-test changed lines per fix is between 30 and 80 lines, most
  likely about 45.

## What would falsify this

- **Q1:** an after-install share above half the before-install share, or a cut whose 95% Wilson
  interval for the difference includes zero.
- **Q2:** a pooled share above 30% over the first 5 qualifying pairs, or any single pair at 100%
  with 3 or more findings.
- **Q3:** a 90th percentile outside 30 to 80 lines. (Q3 is a calibration, not a claim that L5.4
  works; a miss here changes the threshold, not the verdict on Q1 or Q2.)

## How it will be measured

- **Q1:** `caused_by` edges as `cca_checks/bugs/caused_by.py` derives them (git blame at the base
  commit of every line a fix modified or removed, mapped to ledger rows whose `fix_commit` is the
  blamed commit), from the ledger plus `caused_by.jsonl`, via `backfill_caused_by` in a dry run.
  Denominator: CCA fixes with a `fix_commit` in this repository (and RE-call, reported separately).
  Before window: every such fix committed before the install timestamp. After window: fixes from
  runs whose state carries `"fix_audit": true`, until at least 30 such runs or 100 such fixes,
  whichever comes later; then one look.
- **Q2:** for each qualifying pair, the later run's L2.5 CONFIRMED P1 and P2 findings, each
  classified by whether its `file:line` lies in a hunk of `git diff <earlier run's prefix_tree>
  <earlier run's commit>` restricted to that run's fix hunks. Pooled share = in-hunk / all
  confirmed P1 and P2. The first 5 qualifying pairs after install; one look.
- **Q3:** for every ledger fix with a `fix_commit`, non-test changed lines (added plus removed) in
  that commit divided by the number of ledger fixes sharing the commit; the 90th percentile.
  Appended under this record with its command before `SIZE_THRESHOLD` is set.

**Apparatus check before Q1 is read:** the dry-run backfill must reproduce the edges already in
`caused_by.jsonl` for the rows it covers (a case whose answer is known). If it does not, Q1 is
not measured until it does.

## What I already know

- 2026-11-21, this repository: the 7 confirmed P2s of run 20261120T225224Z were all inside or next
  to the fixes of the hunt's P2s (3 incomplete fixes with a missed sibling, 4 bugs in new code).
  That is Q2's baseline, n = 7.
- 2026-10-28 (memo `2026-10-28-cca-bug-memory-growth-and-injection`, recall project memory):
  `caused_by` edges are mostly audit escapes found after DEEP audits (53 fixes, 49 of them found by
  Codex descending re-audits), not regressions in the strict sense.
- L1 precision held at 0.92 to 0.94 in November (measured 2026-11-21 with
  `benchmarks/harness/l1_precision.py`), so the yield rise is not a precision drop.

## Confounds I can name now

- **Undercount in Q1:** a fix that only adds lines blames nothing, so bugs introduced purely by
  added code (BUG-002, BUG-003 shapes) are invisible to `caused_by`. Q2 does not have this blind
  spot, which is why it is measured too.
- **Squash merges:** fixes squashed into one commit with the bugs they introduced (as #132 did with
  its 7 audit fixes) leave no cross-commit edge, so Q1 also misses bugs caught inside a run. Q1
  measures escapes across PRs only.
- **Mix shift:** the after window may audit different code (more hunts, more parser work); a lower
  share could come from easier targets.
- **The memory holdout** (window open since 2026-11-04, look due late December to late January):
  installing L5.4 inside it changes every run's wall time and may change auditor behaviour; the
  install timing is the owner's decision, and the install date is recorded beside the result.
- **Model changes** in either client during the after window.
- **Q2 needs a later audit to exist:** with L5.4, fixes are audited inside the run, so a
  "qualifying pair" is rarer; if fewer than 5 pairs appear by 2027-01-31, Q2 is reported with its
  actual n and no verdict.

## Result (2026-11-22)

Status: Q3 measured; Q1 and Q2 not yet measured.

- **Q3 measured:** the 90th percentile is 139.4 lines (nearest rank). The 50th is 50.1, the 75th
  93.6 and the 95th 141.7. n = 198 ledger fixes of `GiulioDER/cca-audit` with a `fix_commit`,
  in 20 distinct commits. All 20 commits exist in this checkout, so none were skipped; no binary
  file appeared. The ledger holds 3,593 rows with a `fix_commit`, 3,395 of them in other
  repositories, which are excluded.
- **Q3 predicted:** 30 to 80 lines, most likely about 45.
- **Gap:** the 90th percentile is 59.4 lines above the top of the predicted range and 94.4 above
  the most likely value, so the Q3 prediction is falsified. Per the falsification rule above it
  changes the threshold, not the verdict on Q1 or Q2. The 20 commits are few and each is a whole
  audit run's fix commit divided across many fixes, so the percentile rests on 20 distinct sizes.
- **Apparatus check:** commit bfc7d2568ff40b52dace6ee3653c7caef0b48463 has non-test numstat lines
  20+0, 3+2, 37+10 and 12+2, which I summed by hand to 86; the script printed 86 (43 per fix for
  the 2 fixes sharing it).
- **Command** (run from the repository root with `PYTHONPATH` set to it):
  `python C:\Users\gde00\AppData\Local\Temp\claude\f88d87f088c7e606\6c0dcf18-af2c-4868-8ef4-7c3537af01cc\scratchpad\measure_q3.py`
  (single commit: the same script with the sha as its argument). It reads the ledger through
  `bug_rows`, keeps `repo` equal to `GiulioDER/cca-audit` (case-insensitive), counts
  `git show --numstat --format=` lines excluding `fixaudit.is_test_path` paths, and divides by the
  fixes sharing the commit.
- **Setting:** `SIZE_THRESHOLD` becomes 140 (the 90th percentile rounded up).
