# Pre-registration: does the bug-ledger backfill read the real CCA history?

**Date:** 2026-10-11   **Status:** predicted, not yet measured

## The question

Run read-only over every local clone and scorecard ledger on the author's workstation, what share
of the selected CCA fix commits does `python -m cca_checks bugs backfill` turn into at least one
bug row, and does a commit whose answer I know come out exactly right?

## What I predict

1. **Parsed share:** at least 75% of selected commits (rows produced / commits selected, after
   reverted commits are set aside) yield at least one bullet; the rest land in `unparsed`.
2. **Known answer:** RE-call commit `6aa7ff5bd` ("15 audit fixes to LiteStore from CCA (DEEP
   tier)") yields exactly **17** rows: its P1 and P2 bullets name 17 finding IDs once `CODE-001/002`,
   `CODE-003, DOC-003` and `PERF-004/005, BUG-012` are expanded, and `DEPLOY-001` sits under "Owner
   decision, not fixed here" and must not count. (The subject says 15 because the author counted
   bullets, not IDs.)
3. **Proved without bindings:** exactly **0** rows, by construction: ledger evidence applies only
   through `--bind`.
4. **Candidates:** between 1 and 8, and they include run `pr805a` paired with the RE-call commit
   that names FIX-1 to FIX-6 for #805.
5. **Proved after binding that one pairing:** exactly **1** row (FIX-1 of #805). It is the only
   finding with a saved RED proof (`redproof-fix1.json` holds FIX-1 cases only), and its ledger rows
   say CONFIRMED and APPROVED.
6. **Total rows:** between 150 and 900. This is a wide band on purpose: I have commit counts per
   remote from a `--grep` over all branches (2026-10-11), which over-select, and no bullet counts.

## What would falsify this

* Parsed share below 75%: the parser does not read the real history and the count is mostly a
  lower bound on nothing.
* `6aa7ff5bd` yielding anything but 17 rows, or any row for `DEPLOY-001`.
* Any `proved` row without a binding.
* No candidate for `pr805a`, or more than 1 proved row after binding it.
* Any of five randomly chosen rows whose `summary` is not the commit's own bullet text.

## How it will be measured

```bash
python -m cca_checks bugs backfill --json $(one --repo per local clone under ~/Documents) \
    $(one --ledger per AUDITOR_SCORECARD.jsonl under ~/Documents and ~/.claude/archive)
```

then the same with `--bind <sha>=pr805a` for the candidate in prediction 4. Metrics, by name:
`rows`, `unparsed` (commits), commits selected (`rows`' distinct `fix_commit` plus `unparsed`),
`tiers.proved`, `candidates`. Spot check: five rows chosen with `random.sample(rows, 5)`, seed 11,
compared by hand with `git show -s <fix_commit>`. Secondary, for the deferred review minor about
subject-only selection: per repository, selected commits against `git log <default ref> -i -E
--grep=<same pattern>` over whole messages.

Nothing is written: no `--write` until the user approves the dry run.

## What I already know

* Seven distinct scorecard ledgers, ten runs, 2026-08-26 to 2026-09-30; only `pr805a` saved
  per-finding proofs (measured 2026-10-11 while designing; spec §2).
* Ledger rows key findings by run-local FIX ids; only one ledger has a `finding` key (spec §11).
* At least one large CCA fix commit will not be selected: RE-call `43dd1bf14` ("Deep bug audit of
  graph, reasoning and calibration: 54 verified fixes") matches none of the subject patterns. The
  total is a lower bound for that reason too.

## Confounds I can name now

* A clone whose `origin` refs are stale undercounts its repository (review minor, deferred): the
  first clone of each remote is read, not the freshest.
* Commits with unknown finding prefixes are reported, not counted; if real history uses a prefix
  outside the known set, the parsed share drops for a reason that is the allowlist, not the parser.
* Squash merges whose PR title lacks the CCA phrase are not selected at all, so they cannot show up
  as unparsed either: the parsed share is over selected commits only.

## Result (2026-10-11)
**Status:** measured. Prediction falsified on the parsed share and on the known answer.

Measured on `feat/cca-bug-ledger` at `4a71737`, read-only, 19 repositories (one clone per remote,
the one whose default branch had the newest commit; no fetch), 14 scorecard ledger files. Private
repositories are reported as one total, since this file is public.

| # | Predicted | Measured | Held? |
|---|---|---|---|
| 1 | parsed share at least 75% | **21 of 117 selected commits, 17.9%**; 96 unparsed (51 in the private repository, 41 in RE-call, 4 elsewhere) | **no** |
| 2 | `6aa7ff5bd` yields exactly 17 rows | **0 rows**: the commit is not on RE-call's default branch | **no**, wrong premise |
| 3 | 0 proved without bindings | 0 of 187 rows | yes |
| 4 | 1 to 8 candidates, including `pr805a` | 1 candidate: RE-call `0cfa7219954d` with run `pr805a` | yes |
| 5 | exactly 1 proved after binding it | 1: `RE-call:0cfa7219954d:FIX-1`, sources two ledger lines and `redproof-fix1.json` | yes |
| 6 | 150 to 900 rows | 187 (RE-call 99, cca-audit 21, private 67) | yes, near the floor |

Spot check (seed 11, five rows): all five summaries are the commit's own bullet text.
Unknown IDs reported, not counted: 78 (prefix `F` 75, `EVAL` 2, `FIN` 1). Reverted 0,
cross-repository duplicates 0, ambiguous 0. The secondary measure (subject-only selection against
whole-message `--grep`) was not run.

**Gap, and what it means.** The 17.9% is not a parser defect in the sense of a broken rule: the
rules read exactly the shape the spec described (Step 7 of `audit-fix.md`), and real history mostly
does not have that shape. Reading the unparsed commits by structure only, three formats account
for them:

1. severity headings without a trailing colon and with text after the level ("P1 Critical
   (high-stakes ...)"), which the heading rule does not accept;
2. bullets labelled by a bare letter (`- A`, `- B`) or by `F-n`, with no auditor-style finding ID
   at all, in the private repository;
3. "second pass" commits whose sections are thematic ("Shipped library and install:") rather than
   P1/P2, above bullets that do carry real IDs.

So the 187 is a lower bound on landed CCA fixes whose message names a finding ID under a P1/P2
heading, not a count of CCA fixes. What I believed was that the Step 7 template had been followed;
it was followed in the repositories CCA was developed in (cca-audit, much of RE-call) and much less
in the others.

The known-answer premise was wrong for an informative reason: I chose `6aa7ff5bd` from a
`git log --all` listing, and "fixed means landed" excludes exactly that kind of commit.

**Apparatus checks that passed:** no row is `proved` without an explicit binding; the one binding
produces one `proved` row traced to its artifacts; summaries are verbatim. The freshest local
clone of the private repository has remote refs ending 2026-09-10, so that repository is
undercounted after that date.
