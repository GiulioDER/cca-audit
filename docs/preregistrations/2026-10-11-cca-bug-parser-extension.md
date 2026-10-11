# Pre-registration: does the extended parser read the real CCA history, and are its new rows fixes?

**Date:** 2026-10-11   **Status:** predicted, not yet measured

## The question

With the parser extended at `a7d07d6` (spec §11.9), what share of the selected CCA fix commits
yield at least one row, and what share of the rows it adds are real fix descriptions?

## What I predict

1. **Parsed share:** between 70% and 80% of selected commits (commits with at least one row /
   commits selected, reverted commits set aside). My estimate is 88 of 117: the 21 read before,
   plus the 52 with bullets under P1/P2 headings and the 15 with known IDs under topic headings,
   counted by a structure scan of the unparsed commits on 2026-10-11.
2. **Rows:** between 500 and 900, up from 187.
3. **New rows are fixes:** of 10 rows chosen at random among those the first dry run did not
   produce, at least 9 are a description of a change made in that commit. Not a fix: a heading, a
   list of test files, a note about the audit itself, a deferral or a "no change" line.
4. **Apparatus unchanged:** 0 rows `proved` without a binding; exactly 1 with
   `--bind 0cfa7219954d=pr805a`.

## What would falsify this

* Parsed share below 70%: the three formats were not the main gap after all.
* Fewer than 9 of 10 sampled new rows being fixes: the "every bullet under P1/P2 is a fix" rule
  over-counts, and the count it produces cannot back a claim.
* Any `proved` row without a binding, or a proved count other than 1 with it.

## How it will be measured

The same inputs as `2026-10-11-cca-bug-backfill-dry-run.md`: one clone per GitHub remote under
`~/Documents` (the one whose default branch has the newest commit, no fetch), every
`AUDITOR_SCORECARD.jsonl` under `~/Documents` and `~/.claude/archive`, read-only, nothing written.
Parsed share is distinct `fix_commit` over rows divided by that plus `unparsed`. The sample is
`random.seed(11); random.sample(new_rows, 10)`, where `new_rows` excludes every `bug_id` the
first run produced, each judged by reading its summary against `git show -s <fix_commit>`.

## What I already know

* First dry run: 21 of 117 parsed, 187 rows, 0 proved unbound, 1 proved bound.
* Structure scan of the 96 unparsed commits: 52 have bullets under P1/P2 headings with no known ID,
  15 have known IDs only under topic headings, 29 have neither (prose).

## Confounds I can name now

* A fix written as several top-level bullets under one P1 heading counts as several fixes. The
  sample checks whether each row is a fix, not whether two rows are the same fix, so this would
  inflate the count without failing prediction 3.
* The skip words are matched anywhere in a heading, so "P1 Critical (2 dropped by fp-check):"
  would skip a real P1 section and undercount.
* The judgement in prediction 3 is mine, the author's; a second reader could grade borderline
  rows differently.
