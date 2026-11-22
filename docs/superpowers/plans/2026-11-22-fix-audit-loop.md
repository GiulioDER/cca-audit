# Fix Audit Loop Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add layer L5.4 so a CCA run cannot commit while its own fix code holds an unresolved confirmed P1 or P2, an undisposed sibling site, an unproven new mechanism or an unexplained oversized fix.

**Architecture:** A pure check module (`cca_checks/fixaudit.py`) computes the fix diff from a tree stored at L3, then the sibling sweep, mechanism flags and size check. A record module (`cca_checks/fixaudit_record.py`) owns the fix audit journal, the verdict side ledger and the four record conditions, and records L5.4 as a tool layer. The gate core learns L5.4 only for runs started with `fix_audit: true`. Prose (Claude and Codex) adds Step 5.4.

**Tech Stack:** Python 3.10+, standard library only, git CLI, pytest.

**Spec:** `docs/superpowers/specs/2026-11-22-fix-audit-loop-design.md` (approved 2026-11-22).

## Global Constraints

- Do not touch `cca_checks/plugin/hooks/cca_pipeline_guard.py` or `cca_checks/plugin/installer.py` (open work on `claude/awesome-albattani-0ff2e9` and `claude/frosty-elion-0135d2`).
- No new agent type; reuse the existing auditors, `fp-check`, `differential-review`, `architect-reviewer`.
- Ledgers are append-only. `FIXAUDIT_LEDGER.jsonl` and `fixaudit-journal.jsonl` are append-only too.
- Only runs whose state carries `"fix_audit": true` owe L5.4.
- Cap: 3 fix audit cycles per original finding. At most 4 auditors per pass.
- Ids: fix audit findings are `FA-<pass>-<n>`; passes number from 1.
- Every new behaviour test is proven red (user rule 2026-09-08): record node id, baseline or mutation, and failing assertion in the test docstring.
- Change only the lines the task needs; reuse before writing (user Python rules 2026-11-01).
- Full suite and stressful runs only on VPS2: `bash ~/.claude/bin/vps2-pytest`; read the summary line, exit 0 is not a pass. Targeted single test files may run locally.
- No dash as punctuation in prose written for this change.
- The pre-registration is committed before any install or measurement (Task 9), and its predictions are never edited.

## Review Focus

1. A fix that adds or deletes a whole file, or renames one: diff parsing, sweep and size check must handle it without a crash, attributing the lines to the fix that lists the file.
2. A non-UTF-8 or binary file in the fix diff: skipped by the sweep and the size check, reported as skipped, never a crash.
3. A declared sibling pattern so broad it matches more than 50 sites: refused with a message asking for a narrower pattern, not reported as 50+ hits.
4. A run paused and resumed between fix audit passes: pass numbering continues from the journal, and the delta is computed from the last audited tree.
5. A run started with `fix_audit: true` whose state lacks `prefix_tree` (L3 recorded by an older copy): `fixaudit check` refuses with the reason, never diffs from `base_commit`.

Each line has its test in the owning task (Tasks 2, 2, 3, 5, 2).

## File Structure

| File | Responsibility |
|---|---|
| `cca_checks/plugin/hooks/cca_gate_core.py` | L5.4 constants, `required_layers`, `predecessors` (modify) |
| `cca_checks/pipeline_state.py` | `fix_audit` flag at start, `prefix_tree` at L3 (modify) |
| `cca_checks/fixaudit.py` | fix diff, hunks, sibling sweep, mechanism flags, size check, report (create) |
| `cca_checks/fixaudit_record.py` | journal, side ledger, verdict file shape, record conditions, CLI (create) |
| `cca_checks/__main__.py` | dispatch `fixaudit` (modify) |
| `cca_checks/plugin/tools/cca_tautology_check.py` | per-proof snapshot dir (modify) |
| `cca_checks/plugin/commands/audit-fix.md`, `cca_checks/plugin/codex/cca-audit/SKILL.md`, `cca_checks/plugin/agents/cca-fp-check.md`, `cca_checks/plugin/agents/cca-differential-review.md`, `cca_checks/plugin/agents/cca-architect-reviewer.md`, `docs/pipeline-diagram.md` | Step 5.4 prose (modify) |
| `tests/test_fixaudit_gate.py`, `tests/test_fixaudit_checks.py`, `tests/test_fixaudit_record.py` | tests (create) |
| `docs/preregistrations/2026-11-2X-fix-audit-loop.md` | pre-registration (create) |

---

### Task 1: The gate owes L5.4, for new runs only

**Files:**
- Modify: `cca_checks/plugin/hooks/cca_gate_core.py:41-56, 387-422`
- Modify: `cca_checks/pipeline_state.py` (`PipelineState` fields near line 111, `start` near line 412, the L3 branch of `record`)
- Test: `tests/test_fixaudit_gate.py`

**Interfaces:**
- Produces: `core.LAYERS` includes `"L5.4"` between `"L5"` and `"L5.5"`; `core.FIX_VERDICT_LAYERS` and `core.HEADINGS` include it (`"Step 5.4"`); `core.TOOL_LAYERS["L5.4"] = "python -m cca_checks fixaudit record"`.
- Produces: `PipelineState.fix_audit: bool` (default False, serialized as `"fix_audit"`), `PipelineState.prefix_tree: str | None` (serialized as `"prefix_tree"`).
- Produces: `core.required_layers(state, owed)` inserts `"L5.4"` after `"L5"` when `state.get("fix_audit")` and `owed`, on every tier. `core.predecessors`: `L5.4 ← (L5,)`; with the flag, `L5.5 ← (L5.4,)`, `L5.6 ← (L5.4,)`, L6 owed `← (L5, L5.4, L5.5, L5.6)` STANDARD/DEEP and `(L5, L5.4)` FAST. Without the flag every table entry is unchanged.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_new_run_owes_l54_after_l5_on_every_tier(): ...
    # for tier in TIERS: state = {"schema": 2, "tier": tier, "fix_audit": True, ...}
    # assert "L5.4" in core.required_layers(state, owed=True)
    # assert index("L5.4") == index("L5") + 1
def test_an_old_run_owes_exactly_what_it_owed_before(): ...
    # state without "fix_audit": required_layers and predecessors equal the pre-change tuples
def test_l3_stores_the_prefix_tree(git_repo, monkeypatch): ...
    # start a run, record L1..L3 with fixture outputs; assert state.prefix_tree == core.fingerprint(git_repo)
def test_l55_cannot_be_recorded_before_l54_in_a_new_run(git_repo, monkeypatch): ...
    # pytest.raises(ValueError, match="L5.4")
```

- [ ] **Step 2: Run them and confirm they fail**

Run: `python -m pytest tests/test_fixaudit_gate.py -q`
Expected: FAIL (`L5.4` not in LAYERS; `prefix_tree` attribute missing).

- [ ] **Step 3: Implement** the constants, the flag-gated table entries, `start` writing `fix_audit=True`, and the L3 branch storing `prefix_tree = core.fingerprint(root)`, refusing L3 with "the working tree could not be fingerprinted" when it returns None on a `fix_audit` run.

- [ ] **Step 4: Run the tests, then the existing gate tests**

Run: `python -m pytest tests/test_fixaudit_gate.py tests/test_pipeline_state.py tests/test_gate_core.py -q`
Expected: all pass. Red proof for `test_an_old_run_owes_exactly_what_it_owed_before` is a mutation: drop the flag check in `required_layers`; it fails.

- [ ] **Step 5: Commit**

---

### Task 2: The fix diff and the size check

**Files:**
- Create: `cca_checks/fixaudit.py`
- Test: `tests/test_fixaudit_checks.py`

**Interfaces:**
- Consumes: `state.prefix_tree` (Task 1); `cca_checks.bugs.caused_by.fix_diff(repo_root, base, head) -> str` (reused with tree ids); `layer_outputs.l4_fixes(data) -> list[dict]`.
- Produces:
  - `@dataclass(frozen=True) class Hunk: path: str; old_start: int; old_len: int; new_start: int; new_len: int; removed: tuple[str, ...]; added: tuple[str, ...]` and property `id -> str` = `f"{path}:{new_start}"`.
  - `fix_hunks(repo_root: Path, prefix_tree: str | None, tree: str) -> list[Hunk]`, raising `FixAuditRefused` naming `prefix_tree` when it is None (never falling back to `base_commit`)
  - `is_test_path(path: str) -> bool` (`tests/` prefix, `test_*.py`, `*_test.py`)
  - `SIZE_THRESHOLD: int` (set in Task 9's measurement step; placeholder value 40 until then, marked by a comment naming Task 9)
  - `size_report(hunks: list[Hunk], fixes: list[dict]) -> list[dict]` → `[{"fix_id", "lines", "over": bool, "has_reason": bool}]`
  - `class FixAuditRefused(ValueError)`

- [ ] **Step 1: Write the failing tests**

```python
def test_the_fix_diff_starts_at_the_prefix_tree_not_the_base(git_repo): ...
    # commit feature line A; prefix_tree = fingerprint after an uncommitted feature edit B;
    # then fix edit C. fix_hunks(prefix, now) contains C only, never A or B.
def test_a_missing_prefix_tree_is_refused(git_repo): ...       # Review Focus 5
    # with pytest.raises(FixAuditRefused, match="prefix_tree"): fix_hunks(git_repo, None, tree)
def test_added_deleted_and_renamed_files_are_counted_to_their_fix(git_repo): ...  # Review Focus 1
def test_a_binary_or_non_utf8_file_is_skipped_and_reported(git_repo): ...         # Review Focus 2
def test_a_fix_over_the_threshold_needs_a_size_reason(): ...
    # lines split evenly where two fixes list one file; over and not has_reason -> listed
```

- [ ] **Step 2: Run and confirm they fail** (an import error does not count as red: write the module skeleton with signatures raising `NotImplementedError` first, then confirm each test fails in its assertion.)

Run: `python -m pytest tests/test_fixaudit_checks.py -q`

- [ ] **Step 3: Implement** `fix_hunks` by parsing `caused_by.fix_diff(repo_root, prefix_tree, tree)` (it already pins diff settings); skip binary hunks (`Binary files ... differ`) and record them in the report's `skipped`. `size_report` counts non-test added plus removed lines per fix's `files`.

- [ ] **Step 4: Run the tests.** Expected: PASS.

- [ ] **Step 5: Commit**

---

### Task 3: The sibling sweep

**Files:**
- Modify: `cca_checks/fixaudit.py`
- Test: `tests/test_fixaudit_checks.py`

**Interfaces:**
- Produces:
  - `auto_patterns(hunks: list[Hunk]) -> list[str]`: normalized removed lines from non-test files that contain `(` or a string literal; whitespace collapsed.
  - `MAX_HITS_PER_PATTERN = 50`
  - `sibling_hits(repo_root: Path, hunks: list[Hunk], fixes: list[dict]) -> list[dict]` → `[{"hit_id": "S-<n>", "fix_id", "pattern", "path", "line", "text"}]`, searching with `git grep -n -F` (declared regexes with `-E`) over tracked files at the working tree, excluding lines inside the fix's own hunks.
  - `check_dispositions(hits: list[dict], dispositions: dict, repo_root: Path) -> list[str]` → reasons; `"fixed"` is valid only when the hit's text no longer occurs at that path; `"not_applicable"` needs a non-empty `reason`.
  - L4 entry fields read: `sibling_patterns: list[str]` (required; may be empty only with non-empty `sibling_reason`).

- [ ] **Step 1: Write the failing tests** (acceptance cases from run 20261120T225224Z, rebuilt in a temp repo)

```python
def test_bug001_shape_lists_the_other_readers(git_repo): ...
    # four readers with read_text(encoding="utf-8"); fix adds errors="replace" to one;
    # sibling_hits lists the other three
def test_sec001_shape_lists_the_other_bare_git_launches(git_repo): ...
def test_num001_shape_needs_a_declared_pattern(git_repo): ...
    # auto patterns find nothing; a declared pattern "runs\" / run_id / \"summary.json\"" finds the reader
def test_a_fix_without_sibling_patterns_or_reason_is_refused(): ...
def test_fixed_is_rejected_while_the_hit_still_matches(git_repo): ...
def test_a_pattern_over_50_hits_is_refused(git_repo): ...    # Review Focus 3
def test_trivial_lines_never_become_patterns(): ...          # "return None", ")" -> []
```

- [ ] **Step 2: Run and confirm each fails in its assertion.**

- [ ] **Step 3: Implement** the three functions.

- [ ] **Step 4: Run the tests.** Expected: PASS.

- [ ] **Step 5: Commit**

---

### Task 4: New mechanism flags

**Files:**
- Modify: `cca_checks/fixaudit.py`
- Test: `tests/test_fixaudit_checks.py`

**Interfaces:**
- Produces:
  - `MECHANISMS: dict[str, tuple[tuple[str, ...], tuple[str, ...]]]` mapping a name to (regexes on added lines, auditor types). Rows exactly as the spec's table: `process` → `security-auditor, bug-auditor`; `lock` → `bug-auditor`, `bug-auditor` with the DAT- prompt; `timeout` → `bug-auditor, perf-auditor`; `parser` → `bug-auditor`, `bug-auditor` with the DAT- prompt; `file_write` → `bug-auditor` with the DAT- prompt. Shell rows for `.sh`/`.ps1`: `trap`, `ErrorActionPreference`, redirections `2>`.
  - `mechanism_flags(hunks: list[Hunk], fixes: list[dict]) -> list[dict]` → `[{"fix_id", "mechanism", "path", "line", "auditors": [...]}]`, non-test files only.
  - `failure_test_reasons(flags: list[dict], fixes: list[dict], repo_root: Path) -> list[str]`: each flagged fix's `failure_test` node must exist (file exists and holds `def <name>`).
  - `auditors_for(fixes: list[dict], flags: list[dict], categories: dict[str, str]) -> list[str]`: at most 4, ranked by how many fixes need each.

- [ ] **Step 1: Write the failing tests**

```python
def test_bug002_shape_flags_a_timeout(): ...     # added "subprocess.run(..., timeout=t)" -> process and timeout
def test_bug003_shape_flags_a_parser(): ...      # added json.loads on ledger rows -> parser
def test_a_flagged_fix_without_failure_test_is_refused(tmp_path): ...
def test_a_failure_test_naming_a_missing_node_is_refused(tmp_path): ...
def test_no_more_than_four_auditors(): ...
def test_test_files_never_raise_flags(): ...
```

- [ ] **Steps 2 to 4:** run the tests and confirm each fails in its assertion, implement, run them green.
- [ ] **Step 5: Commit**

---

### Task 5: Journal, verdict file and side ledger

**Files:**
- Create: `cca_checks/fixaudit_record.py`
- Modify: `cca_checks/layer_outputs.py` (add `l54_audit`)
- Test: `tests/test_fixaudit_record.py`

**Interfaces:**
- Produces:
  - Paths: `JOURNAL = "fixaudit-journal.jsonl"` in `runs/<run_id>/`; `LEDGER = Path(".claude/audits/FIXAUDIT_LEDGER.jsonl")`; verdict file `.claude/audits/out/L5.4.json`; dispositions `.claude/audits/out/L5.4-siblings.json`.
  - `layer_outputs.l54_audit(data) -> dict`: requires `{"pass": int >= 1, "audited_tree": str, "hunks": [hunk ids], "findings": [{"id": "FA-<pass>-<n>", "fix_id", "finding", "priority": "P1"|"P2"|"P3", "verdict": "CONFIRMED"|"FALSE_POSITIVE"|"UNCERTAIN"|"RESOLVED"|"OUT_OF_SCOPE", "file", "line", "claim"}]}`; raises `ShapeError` otherwise.
  - `journal(root: Path, run_id: str, *, finding: str, fix_id: str, pass_no: int, verdict: str, reason: str) -> None` (append one JSON line).
  - `cycles(root: Path, run_id: str) -> dict[str, int]` (per original finding, rows whose verdict is `CONFIRMED`).
  - `parked(root: Path, run_id: str) -> set[str]` (findings with 3 or more cycles, or a `PARKED` row).
  - `next_pass(root: Path, run_id: str) -> int` (1 + highest pass journaled).
  - `append_verdicts(root: Path, run_id: str, audit: dict) -> int` (side ledger rows, one per finding, with `run_id`, `layer: "L5.4"`, `ts`).

- [ ] **Step 1: Write the failing tests**

```python
def test_cycles_count_confirmed_fix_audit_findings_per_original_finding(tmp_path): ...
def test_the_third_cycle_parks_the_finding(tmp_path): ...
def test_pass_numbering_continues_after_a_pause(tmp_path): ...   # Review Focus 4
def test_side_ledger_rows_never_reach_the_disposition_ledger(tmp_path): ...
    # append_verdicts; then pipeline_state._ledger_rows and bugs.ledgers.load_ledger see no FA row
def test_a_verdict_file_with_a_bad_id_is_refused(): ...
```

- [ ] **Steps 2 to 4:** run the tests and confirm each fails in its assertion, implement, run them green.
- [ ] **Step 5: Commit**

---

### Task 6: `fixaudit check` and `fixaudit record`

**Files:**
- Modify: `cca_checks/fixaudit.py` (add `check_report`), `cca_checks/fixaudit_record.py` (add `record_reasons`, `main`), `cca_checks/__main__.py` (dispatch `fixaudit` before argparse, like `qwen-tests`)
- Test: `tests/test_fixaudit_record.py`

**Interfaces:**
- Consumes: Tasks 1 to 5.
- Produces:
  - `check_report(root: Path, state) -> dict` writes `runs/<run_id>/fixaudit.json` = `{"tree", "prefix_tree", "hunks", "siblings", "mechanisms", "sizes", "skipped", "auditors", "delta_hunks"}`; `delta_hunks` are the hunks changed since the last journaled `audited_tree` (all hunks on pass 1).
  - `record_reasons(root: Path, state) -> list[str]`: the spec's four conditions; condition 4 only when `state.tier != "FAST"`: the latest `L5.4.json` covers every id in `delta_hunks`, and every CONFIRMED P1/P2 is RESOLVED in a later pass or in `parked()`.
  - CLI: `python -m cca_checks fixaudit check`, `python -m cca_checks fixaudit journal --finding F --fix-id X --pass N --verdict V --reason R`, `python -m cca_checks fixaudit record [--audit .claude/audits/out/L5.4.json]`. `record` calls `pipeline_state.record("L5.4", detail=..., source="tool", output=...)` and exits 2 with the reasons when refused.

- [ ] **Step 1: Write the failing tests**

```python
def test_record_refuses_a_stale_report(git_repo, monkeypatch): ...         # edit after check
def test_record_refuses_an_undisposed_sibling(git_repo, monkeypatch): ...
def test_record_refuses_an_unresolved_confirmed_p2_on_standard(git_repo, monkeypatch): ...
def test_fast_records_without_an_auditor_file_once_checks_pass(git_repo, monkeypatch): ...
def test_an_edit_after_l54_makes_l5_and_l54_owed_again(git_repo, monkeypatch): ...
def test_the_seven_case_fixture_blocks_until_each_is_resolved(git_repo, monkeypatch): ...
```

- [ ] **Steps 2 to 4:** run the tests and confirm each fails in its assertion, implement, run them green.
- [ ] **Step 5: Commit**

---

### Task 7: Red proofs against the audited tree

**Files:**
- Modify: `cca_checks/plugin/tools/cca_tautology_check.py` (`main` argument parsing, `verify`)
- Test: `cca_checks/plugin/tools/test_cca_tautology_check.py`

**Deviation from the spec, stated:** the spec names a `--baseline-tree` option. The plan reuses the existing snapshot mechanism instead, which already restores files and passed its own audits: before editing for a fix audit finding, `python -m cca_checks tautology snapshot --snapdir .claude/audits/.fixaudit_snapshot/pass-<k> <files>`; a proof may name its own snapshot as `--proof FA-1-1=<nodeid>@.claude/audits/.fixaudit_snapshot/pass-1`. The baseline is the same tree the spec names (the tree the pass audited).

**Interfaces:**
- Produces: `--proof FINDING=NODEID[@SNAPDIR]`; `verify(proofs: list[tuple[str, str, Path | None]], snapdir: Path = DEFAULT_SNAPDIR, ...)` where a third element overrides `snapdir` for that proof.

- [ ] **Step 1: Write the failing tests**

```python
def test_a_fix_audit_proof_is_red_against_its_own_pass_snapshot(git_repo, monkeypatch): ...
def test_the_same_proof_against_the_pre_l4_snapshot_reads_tautological(git_repo, monkeypatch): ...  # control
def test_a_proof_without_a_suffix_uses_the_default_snapdir(git_repo, monkeypatch): ...            # control
```

- [ ] **Steps 2 to 4:** run the tests and confirm each fails in its assertion, implement, run them green.
- [ ] **Step 5: Commit**

---

### Task 8: Prose for both clients

**Files:**
- Modify: `cca_checks/plugin/commands/audit-fix.md`, `cca_checks/plugin/codex/cca-audit/SKILL.md`, `cca_checks/plugin/agents/cca-fp-check.md`, `cca_checks/plugin/agents/cca-differential-review.md`, `cca_checks/plugin/agents/cca-architect-reviewer.md`, `docs/pipeline-diagram.md`
- Test: `tests/test_fixaudit_prose.py` (create)

Content: the contract row `5.4 | fix audit | python -m cca_checks fixaudit record`; a `## Step 5.4` section with the spec's Part 1, Part 2 and recording rules; the re-read cue at the end of Step 5 ("the tests went green; the fix code has not been audited yet: re-read § Step 5.4"); L4 entry fields `sibling_patterns`, `sibling_reason`, `failure_test`, `size_reason` in Step 4; Step 5.5 and Step 6 hand over the sibling list as in scope; the deferred section's D.5 names L5.4; `fp-check` gets a `MODE=FIXAUDIT` paragraph with the `RESOLVED` verdict; FAST tier table row.

- [ ] **Step 1: Write the failing test**

```python
def test_both_clients_name_step_54_and_its_record_command(): ...
    # for each of audit-fix.md and SKILL.md: "Step 5.4" in text and "fixaudit record" in text
def test_the_contract_table_lists_l54_between_l5_and_l55(): ...
def test_fp_check_documents_the_resolved_verdict(): ...
```

- [ ] **Steps 2 to 4:** run the tests and confirm each fails in its assertion, write the prose, run them green.
- [ ] **Step 5: Commit**

---

### Task 9: Measure the size threshold and pre-register the result

**Files:**
- Create: `docs/preregistrations/2026-11-2X-fix-audit-loop.md` (date of writing)
- Modify: `cca_checks/fixaudit.py` (`SIZE_THRESHOLD`)

- [ ] **Step 1: Write the pre-registration first** with `/preregister`: question (does L5.4 cut the share of CCA fixes whose `caused_by` names an earlier CCA fix commit, and the share of a next run's confirmed findings inside the previous run's fix hunks); prediction with numbers; sample size and stopping rule; what falsifies it; the known undercount (pure insertions blame nothing); the install timing decision left to the owner (memory holdout window).
- [ ] **Step 2: Commit the pre-registration** before computing anything.
- [ ] **Step 3: Measure past fix sizes**: for every ledger fix with a `fix_commit`, non-test changed lines divided by the fixes sharing the commit; take the 90th percentile. Append the measured value and the command under the predictions.
- [ ] **Step 4: Set `SIZE_THRESHOLD`** to that value, with a comment naming the pre-registration. Re-run `tests/test_fixaudit_checks.py`.
- [ ] **Step 5: Commit**

---

### Task 10: Whole-branch verification

- [ ] **Step 1:** Full suite on VPS2: `bash ~/.claude/bin/vps2-pytest`. Expected: summary line with 0 failed.
- [ ] **Step 2:** `ruff check` and `pyright` on changed files: clean.
- [ ] **Step 3:** Exercise the deliverable where it is consumed: in a scratch repo, run a real STANDARD CCA run through L5.4 with one planted fix-induced bug (a BUG-001 shape) and confirm `fixaudit record` refuses until the sibling is fixed, then records.
- [ ] **Step 4:** CCA DEEP DIFF audit of the branch (this change is CCA's own gate), then a PR. Install waits for the owner's timing decision.
