# Anchor recovery from single production file fixes

**Date:** 2026-10-28   **Status:** draft for the owner's review

## Intent

A ledger bug without a code anchor never reaches the local `bugs brief`, so auditors never see it.
Measured 2026-10-28 over `~/.claude/cca-bugs/memos`: 1,307 bug records, 712 unanchored after the
46 that `recover_anchors --write` recovered the same evening. The two existing recovery tools are
exhausted on the rest: run-archive recovery lacks archives (the runs went with their worktrees), and
Git recovery requires the commit bullet to name the file, which 418 rows do not.

213 of the then 758 unanchored rows came from a fix that changed exactly one production file
(tests, docs and configuration excluded), across 42 commits. For those the bug's file is close to
certain. This design adds that inference as a new, separately labelled recovery method.

## Goals

1. Recover a file-level anchor for an unanchored bug row whose fix changed exactly one production
   file, with provenance that says how it was inferred.
2. Keep every existing rule's precedence: authored anchors, run-archive annotations and Git bullet
   annotations win over this method.
3. Make recovered records rank below records with a real symbol anchor in the brief.

## Non-goals

- Symbols. This method never infers one.
- Rows whose fix changed two or more production files (177 rows) or none (95 rows).
- Rewriting any ledger row. `anchors.jsonl` is appended; the ledger stays append-only.

## Anti-goals

- A brief crowded by inferred records. The brief holds at most 5 bugs; an inferred file-level
  record must never displace a record anchored at the changed symbol.
- A silent widening of the rules that two security reviews tightened: the method is opt-in per
  run of the tool, dry-run by default, and its annotations are labelled so they can be retracted
  as a group.

## Design

### Eligibility (decided)

A row is eligible when all hold:

- it has no effective anchor (no authored anchor, no existing annotation);
- its `repo` matches a local clone's `origin`, its `fix_commit` is a full SHA present there, and
  that commit has exactly one parent;
- the files the commit changed, read from Git (`diff-tree` against the parent), not from the row,
  contain exactly one production file after the exclusions below, and that file is in the row's
  `files`;
- that file is a regular UTF-8 blob at the fix commit;
- the finding's prefix is not `ENV`, `DEP` or `DEPLOY` (DOC added 2026-10-29 after the final review) (their defect often lives in configuration or
  a manifest, which the exclusions remove, so the one production file may not be where it was);
- the summary names no other file (the existing `references()` finds none outside that file).

**Exclusions**, one constant with a version number: paths under `tests/`, `test/`, `fixtures/`,
`docs/`, `.github/`; basenames `test_*` and `*_test.*`; extensions `.md .txt .json .jsonl .yaml .yml
.toml .cfg .ini .lock`; `CHANGELOG*`.

### The annotation (decided, revised after the builder check)

`{"v": 2, "bug_id", "repo", "run_id" (null allowed), "fix_commit", "anchors": [{"file", "symbol":
null, "code_sha": <hash of the blob at fix_commit>}], "provenance": {"method":
"git-single-production-file", "commit": fix_commit, "parent": <sha>, "message_sha256",
"summary_sha256", "finding_id", "ordinal", "references": [{"file": <the file>}],
"changed_sha256": <sha256 of the sorted changed-file list>, "exclusions_version": 1}}`.

It keeps every key the existing v2 notes carry, so `effective()` binds it to the row's summary
through `summary_sha256` exactly as it binds the bullet methods: the guarantee the security reviews
established is not weakened. `message_sha256` is the hash of the fix commit's message, recorded
as evidence even though this method does not parse it. `anchor_log._git_provenance` gets a branch
for the new method: `references` is one `{"file"}` per anchor (no `token`), `parent` is a full
SHA, `changed_sha256` is a sha256, `exclusions_version` is a positive int, and every anchor's
symbol is null.

**The commit bullet is not required** (my call). The inference rests on the commit's changed
files, not on its message; requiring the bullet would shrink the eligible set to rows the bullet
rules nearly handle already. The binding to the row is `summary_sha256`, `finding_id` and
`ordinal`, as for the other v2 methods.

**Excluded files do not count against eligibility** (my call): a fix that changed one production
file and any number of tests, docs or configuration files is eligible. The other guards
(`references()` naming no other file, the excluded prefixes) carry the risk.

### Where it lives (my call)

An opt-in flag on the existing tool: `python -m cca_checks.bugs.recover_git_anchors --repo ...
--single-file`. It runs only on rows the bullet rules refuse, so those keep precedence. Dry run by
default; `--write` appends; `bugs rerender --all` follows, as for the other methods. Unresolved rows
report the reason the single-file rule refused them.

### Ranking (prerequisite, in RE-call)

`recall/records.py:_match` compares symbols with `==`, so a record anchor with `symbol: null`
meeting a file-level query anchor (also `symbol: null`) is `symbol_changed` whenever the hashes
differ, while a record anchored at a real symbol only reaches `file_only` against the same query.
File-level records therefore outrank precise ones today (already true of the 364 existing
annotations).

`_match` itself stays as it is (my call, after the builder check): it is shared with the PRIOR_FP
matcher, which suppresses a finding only when every anchor of a false-positive record is `exact`,
and a file-level false positive with an equal hash must keep matching `exact` there.

The change is in the brief only, in RE-call's `recall_hooks/auditor_brief.py`: an entry whose best
matching anchor has no symbol is labelled and ranked `file_only`, whatever its hash, so it sorts
after every entry matched at a symbol (`exact` or `symbol_changed`). This applies to the 364
existing annotations too, and to lessons anchored at module level (they sit in their own bucket,
capped at 3, so they compete only with each other). Measured 2026-10-28 while checking this:
`records.py` sorts matches by `(match index, record id)`, so within one match level the 5-bug
cap keeps records alphabetically by id, which is effectively arbitrary. Breaking that tie by
recency (newest first) is part of the same change.

It ships first, installed locally into `~/.claude/hooks` from the RE-call merge; no RE-call
release is needed for the local brief. The new `--single-file` mode refuses `--write` unless the
installed reader has the change, probed by a constant the change adds
(`auditor_brief.FILE_LEVEL_RANKS_AS_FILE_ONLY = True`), so the 213 can never be written ahead
of it.

### Measurement

None of its own: this is recovery, not a quality claim. Its effect on what auditors see is counted
by the memory attribution study, which is why the timing of `--write` matters (see Rollout).

## Rollout

1. RE-call: the ranking change, with tests, merged and installed in `~/.claude/hooks`.
2. CCA: the method, its validator and tests (red-proved), merged.
3. Dry run, review the plan, then `--write` and `bugs rerender --all` at the time the owner picks.

## Constraints

- Tests follow the owner's red-proof rule.
- Full suite on VPS2 only.
- Never `--write` or `rerender` against `~/.claude/cca-bugs` without the owner's go.
- The served `cca` tenant on VPS2 picks up rerendered memos only through its own refresh; embedding
  runs on VPS2, one process at a time, never from here.

## Retraction

Out of scope for this build. The method label makes a later group retraction possible (a reader
filter on `provenance.method`, or a later note per row); nothing is built for it now.

## Left to the builder

- Whether the exclusion constant lives in `recover_git_anchors.py` or a small shared module.
- The exact refusal reason strings.
