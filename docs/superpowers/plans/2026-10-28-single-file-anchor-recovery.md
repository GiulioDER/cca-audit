# Single File Anchor Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Recover file-level anchors for unanchored ledger bugs whose fix changed exactly one production file, after the brief stops ranking file-level records above symbol-level ones.

**Architecture:** Two repositories. RE-call's `recall_hooks/auditor_brief.py` re-ranks entries before its caps (symbol-less matches as `file_only`, ties by recency) and exposes a constant CCA probes. CCA's `anchor_log` accepts a new provenance method, and `recover_git_anchors` gains an opt-in `--single-file` mode that refuses `--write` unless the installed reader carries the constant.

**Tech Stack:** Python 3.10 to 3.13, pytest, git CLI.

**Spec:** `docs/superpowers/specs/2026-10-28-single-file-anchor-recovery-design.md` (CCA branch `claude/single-file-anchors`)

## Global Constraints

- `recall/records.py:_match` is not changed; PRIOR_FP depends on its `exact`.
- No symbol is ever inferred; every recovered anchor has `"symbol": null`.
- Annotations are appended to `anchors.jsonl` only; the ledger is never rewritten.
- Never `--write` or `bugs rerender` against `~/.claude/cca-bugs` without the owner's go; the time of the write is the owner's.
- Exclusions constant, version 1: paths under `tests/`, `test/`, `fixtures/`, `docs/`, `.github/`; basenames `test_*` and `*_test.*`; extensions `.md .txt .json .jsonl .yaml .yml .toml .cfg .ini .lock`; `CHANGELOG*`.
- Excluded finding prefixes: `ENV`, `DEP`, `DEPLOY`.
- Probe constant: `auditor_brief.FILE_LEVEL_RANKS_AS_FILE_ONLY = True`.
- New provenance method name: `git-single-production-file`.
- Every behaviour test is red-proved (pre-change code or a deliberate mutation, failing on its intended assertion), recorded in its docstring.
- CCA full suite on VPS2: `bash ~/.claude/bin/vps2-pytest -q -p no:cacheprovider`. RE-call follows its own CLAUDE.md: three workers, heavy runs through its guard, `python scripts/check_public_tree.py` clean (RE-call is public: no addresses, paths of this machine or private names in its tree).
- Both repositories: work in a fresh worktree off `origin/master`, never in the shared main checkout.

## Review Focus

1. **A record with both a symbol anchor and a file-level anchor**: its best anchor is the one the brief labels; it ranks as `file_only` only when that best anchor has no symbol.
2. **A record whose file-level anchor matches `exact`** (the file is unchanged since the fix): still ranked and labelled `file_only` in the brief, and still `exact` for any other caller of `match_memo_records`.
3. **A fix commit that is a merge or the root commit**: refused (`fix does not have one unambiguous parent`), never anchored against an arbitrary parent.
4. **A fix whose one production file is deleted at the fix commit** (the fix removed it): refused, because no regular blob exists at the fix commit.
5. **A reader directory without `recall_hooks/auditor_brief.py`, or one that fails to import**: `--write` is refused as if the probe were false, and the dry run says why.

Tests: 1 and 2 in Task 1; 3 and 4 in Task 3; 5 in Task 3.

---

### Task 1 (RE-call): rank file-level matches as `file_only`, ties by recency

**Files:**
- Modify: `recall_hooks/auditor_brief.py` (`render`, `_line`, `_best_anchor` near lines 820 to 880 on `origin/master`)
- Test: `tests/test_auditor_brief_hook.py`

**Interfaces:**
- Produces: `FILE_LEVEL_RANKS_AS_FILE_ONLY: bool = True` (module constant); `_brief_match(entry: dict) -> str` returning `"file_only"` when `_best_anchor(entry).get("symbol") is None`, else `entry["anchor_match"]`. `render` orders entries by `(MATCH_ORDER.index(_brief_match(e)), newest first by recall_record["date"], id)` before bucketing, and `_line` prints `_brief_match(e)` in the brackets.

- [ ] **Step 1: Write the failing tests:**
  - `test_symbolless_match_ranks_after_symbol_matches`: three bug entries: A `symbol_changed` on an anchor with `symbol: null`, B `file_only` on a symbol anchor, C `symbol_changed` on a symbol anchor; rendered order is C, then A and B as `file_only` (by date), and A's line shows `[file_only]`.
  - `test_exact_file_level_match_is_shown_file_only` (Review Focus 2): a symbol-less anchor with match `exact` renders `[file_only]`; `recall.records.match_memo_records` on the same input still reports `exact` (assert on its returned `anchor_match`).
  - `test_mixed_anchor_record_uses_its_best_anchor` (Review Focus 1): a record with a symbol anchor matched `symbol_changed` and a file-level anchor matched `file_only` renders `[symbol_changed]`.
  - `test_ties_break_newest_first`: two `file_only` entries dated `2026-10-01` and `2026-10-20` render the later first; with six such bug entries the cap keeps the five newest.
  - `test_the_probe_constant_is_set`: `auditor_brief.FILE_LEVEL_RANKS_AS_FILE_ONLY is True`.
- [ ] **Step 2:** Run `python -m pytest tests/test_auditor_brief_hook.py -k "symbolless or file_only or best_anchor or newest or probe" -q -p no:cacheprovider`; each new test fails on its assertion.
- [ ] **Step 3:** Implement the constant, `_brief_match`, and the ordering in `render`.
- [ ] **Step 4:** Run the file green; then RE-call's suite per its CLAUDE.md, and `python scripts/check_public_tree.py`. Record red proofs in the test docstrings.
- [ ] **Step 5:** Commit, push, open the RE-call PR (its template), wait for the owner's merge.
- [ ] **Step 6 (after merge, owner's go):** deploy by copying `recall_hooks/*.py` from the merge commit into `~/.claude/hooks/recall_hooks/`, after copying the current deployed directory aside as `recall_hooks.bak-<UTC stamp>`. Verify with RE-call's `tests/test_prompt_time_hook.py::test_the_deployed_copy_matches_the_source`. Then confirm `python -X utf8 -P -m cca_checks bugs brief --root <a RE-call checkout> --files recall/provenance_controller.py` still prints `ok` with `[file_only]` on the `50cb206e1a4e` records. If the reader fails because the `recall` package it imports (the editable install at `~/Documents/recall`, 60 commits behind on 2026-10-28) lacks something the new hook needs, stop and report; do not update that shared checkout without the owner.

### Task 2 (CCA): accept the new provenance method

**Files:**
- Modify: `cca_checks/bugs/anchor_log.py` (`_git_provenance`, near line 58)
- Test: `tests/test_git_anchor_recovery.py` (extend; it already exercises v2 Git notes through `anchor_log`)

**Interfaces:**
- Produces: `anchor_log.valid(note)` returns True for a v2 note whose provenance has `method == "git-single-production-file"`, `commit == fix_commit`, non-empty `finding_id`, int `ordinal >= 1`, sha256 `message_sha256`, `summary_sha256` and `changed_sha256`, a full-SHA `parent`, int `exclusions_version >= 1`, `references` equal to `[{"file": a["file"]} for a in anchors]`, and every anchor symbol null. `effective()` needs no change because the note carries `summary_sha256`.

- [ ] **Step 1: Write the failing tests:** `test_single_file_note_is_valid` (a complete note passes); `test_single_file_note_rejects_a_symbol`, `..._missing_parent`, `..._missing_summary_sha`, `..._references_not_matching_anchors`, `..._zero_exclusions_version` (each fails `valid`); `test_single_file_note_is_effective_for_its_row` (through the reader `effective()` uses, the note applies to the row with the same summary and not to one with a different summary).
- [ ] **Step 2:** Run them; the positive tests fail on their assertions.
- [ ] **Step 3:** Add the branch to `_git_provenance`.
- [ ] **Step 4:** Run green; red-prove the negative tests by deleting each check in turn; record in the docstrings.
- [ ] **Step 5:** Commit.

### Task 3 (CCA): `recover_git_anchors --single-file`

**Files:**
- Modify: `cca_checks/bugs/recover_git_anchors.py` (`plan`, `main`, a new `_recover_single_file`)
- Test: `tests/test_git_anchor_recovery.py` (extend; it already builds Git fixtures)

**Interfaces:**
- Consumes: Task 2's note shape; `recover_anchors._git`, `_blob`; `anchors.code_sha`; `references(summary, files)`.
- Produces: `EXCLUSIONS_VERSION = 1`; `excluded(path: str) -> bool`; `_recover_single_file(row: BugRow, repo: Path, identity: str | None) -> dict` raising `Unavailable(<reason>)`; `reader_ranks_file_level(root: Path) -> tuple[bool, str]` (imports `recall_hooks.auditor_brief` from `CCA_RECALL_ROOT` or `~/.claude/hooks` in a child process, as `local_brief.build` does, and returns `(True, "")` only when `FILE_LEVEL_RANKS_AS_FILE_ONLY is True`); `plan(root, repos, single_file: bool = False)`; CLI flag `--single-file`.

Refusal reasons (exact strings): `"fix does not have one unambiguous parent"`, `"fix changed no production file"`, `"fix changed more than one production file"`, `"production file is not in the row's files"`, `"finding prefix is excluded"`, `"summary names another file"`, `"production file is not a regular Git blob at the fix"`.

- [ ] **Step 1: Write the failing tests** in the existing fixture style:
  - `test_single_file_fix_is_anchored`: commit changes `pkg/a.py` and `tests/test_a.py`; a row with `files` both gets one anchor `{"file": "pkg/a.py", "symbol": None, "code_sha": code_sha(<blob>)}` and a note that `anchor_log.valid` accepts.
  - `test_two_production_files_are_refused`, `test_env_prefix_is_refused`, `test_summary_naming_another_file_is_refused`, `test_file_missing_from_row_is_refused`: each raises with its exact reason.
  - `test_merge_or_root_fix_is_refused` (Review Focus 3) and `test_deleted_production_file_is_refused` (Review Focus 4).
  - `test_bullet_rules_keep_precedence`: a row the bullet rules anchor gets the bullet annotation even with `single_file=True`.
  - `test_write_refused_without_the_ranking_reader` (Review Focus 5): with `CCA_RECALL_ROOT` pointing at a directory whose `auditor_brief.py` lacks the constant, at one lacking the file, and at one whose module raises on import, `main([... "--single-file", "--write"])` exits non-zero, writes nothing, and prints the reason; with the constant present it writes.
  - `test_without_the_flag_nothing_changes`: `plan(..., single_file=False)` output equals today's for the same fixture.
- [ ] **Step 2:** Run the new tests; they fail on their assertions.
- [ ] **Step 3:** Implement. Changed files come from `git diff-tree --no-commit-id --name-only -r <parent> <fix>`; one parent from `git rev-list --parents -n 1 <fix>`.
- [ ] **Step 4:** Run green; red-prove by mutation (e.g. drop the prefix check, count excluded files, skip the probe); record in the docstrings.
- [ ] **Step 5:** Commit.

### Task 4 (CCA): docs, suite, PR

**Files:**
- Modify: `docs/configuration.md` (section "Older findings without run IDs": one paragraph on `--single-file`, its rule, its label, and the reader prerequisite); `CHANGELOG.md` (Unreleased).

- [ ] **Step 1:** Write both, naming the 213 rows measured 2026-10-28 as the expected scope, unmeasured until the dry run.
- [ ] **Step 2:** `bash ~/.claude/bin/vps2-pytest -q -p no:cacheprovider` (read the summary line) and `ruff check cca_checks tests`.
- [ ] **Step 3:** Commit, push, open the CCA PR; wait for the owner's merge.

### Task 5: dry run, then the owner's write

- [ ] **Step 1:** After both merges and the Task 1 deploy, install CCA from the merge commit as on 2026-10-28 (package, `install`, `install-codex`).
- [ ] **Step 2:** Dry run: `python -X utf8 -P -m cca_checks.bugs.recover_git_anchors --repo <RE-call clone> --repo <sentiment-agent clone> --repo <cca-audit clone> --single-file`. Report recoverable count, reasons, and a sample of ten anchors for the owner to read.
- [ ] **Step 3:** Only on the owner's go, at the time they pick: back up `anchors.jsonl`, rerun with `--write`, then `python -X utf8 -P -m cca_checks bugs rerender --all`, and record the time in the `unanchored-ledger-rows` memo as a brief-content change for any open measurement window.
