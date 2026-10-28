# Auditor Memory Attribution Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every L1 finding states which prior-bug records led to it, the run summary counts those citations against L2.5 verdicts, and a script measures confirmed findings per run with and without a brief.

**Architecture:** Two required fields on the finding object, asked for in the nine L1 agent files and the `audit-fix.md` schema. `pipeline_state` adds hosted ids to `prior_brief` and a `memory_attribution` object to `summary.json`, both computed at `pipeline close` from files the run already stores. A standalone script under `benchmarks/harness/` reads run directories for the difference-of-differences measurement.

**Tech Stack:** Python 3.10 to 3.13, pytest, Markdown agent prompts.

**Spec:** `docs/superpowers/specs/2026-10-28-auditor-memory-attribution-design.md`

## Global Constraints

- Build starts only after PR 104's pre-registered look at 15 runs (window from `20261028T200105Z`). Task 0 is that gate.
- Memory never suppresses or narrows a finding; no wording may imply a finding with `memory_ids: []` is weaker.
- A cited id is never refused; an id the brief did not show is counted as `unlisted`.
- A run with no brief reports `memory_attribution: null`, not zeros. Unknown is reported as unknown (`null`), never as 0.
- Every new behaviour test is red-proved (pre-change code or a deliberate mutation, failing on its intended assertion) and the proof is recorded in its docstring.
- Full suite on VPS2 only: `bash ~/.claude/bin/vps2-pytest -q -p no:cacheprovider`. Read the summary line; exit 0 alone is not a pass.
- The ledger is append-only; nothing here writes `bugs.jsonl`.
- No dash as punctuation in prose written for the repository; first person singular in any reply to a third party.

## Review Focus

1. **A finding with no `memory_ids` key** (a worktree whose stale `.claude/` self-install runs old prompts): counted as `missing`, never folded into `[]`.
2. **`memory_ids` as a string, not a list** (`"cca-audit:abc:BUG-001"`): counted as `malformed`, never iterated into characters. The ledger once stored file names split into letters through exactly this shape (BUG-001 of run `1433be742f45`).
3. **The same id cited twice**, by one finding or by two auditors merged at L2: counted once per finding.
4. **A deferred pass or a HUNT run**: `memory_attribution` is `null`, and the measurement script leaves the run out.
5. **An id that differs only in case or whitespace from a shown id**: exact match only, so it counts as `unlisted`; the docstring says so.

Each line has its test in the task that owns the code (Task 2 for 1 to 3 and 5, Task 2 and Task 1 for 4).

---

### Task 0: Gate and pre-registration (no code)

**Files:**
- Modify: `docs/preregistrations/2026-10-28-l1-auditor-instructions.md` (append a Result section only)
- Create: `docs/preregistrations/<date>-auditor-memory-attribution.md`

- [ ] **Step 1:** Run `python benchmarks/harness/l1_precision.py --root ~/Documents --since 20261028T200105Z`. Proceed only when it reports at least 15 runs. Exclude runs from worktrees with a stale `.claude/` self-install where they can be told apart, and count them.
- [ ] **Step 2:** Append `## Result (<date>)` to the PR 104 record per its own rules (no edit above it), and commit.
- [ ] **Step 3:** Run Task 1's script over the PR 104 window to get the "before" gap (confirmed per run, brief minus no brief, with n per side).
- [ ] **Step 4:** Write the new record with `/preregister`: the before gap as the baseline, a predicted after gap with direction and rough size, the falsifier (after gap at or below before gap), one look at 15 runs per side, and the confounds the spec names. Commit it before Task 5's install.

Task 1 is needed by Step 3; build Task 1 first if the gate is reached before it exists.

### Task 1: Measurement script for the brief / no-brief gap

**Files:**
- Create: `benchmarks/harness/memory_gap.py`
- Test: `tests/test_memory_gap.py`

**Interfaces:**
- Produces:
  - `had_brief(run_dir: pathlib.Path, summary: dict) -> bool | None`. `True` when `local-prior.json`'s `block` has a line matching `^(BUG|LESSON) `; for a hosted run (`prior-records.json` present), `sum(prior_block.values()) + lessons_block > 0`; `None` when neither receipt exists.
  - `confirmed_l1(run_dir: pathlib.Path) -> int | None`. CONFIRMED verdicts in `run_dir/"L2.5.json"` whose id prefix is an L1 prefix (reuse `l1_precision.PREFIX`); `None` when the file is absent.
  - `runs(root: pathlib.Path, since: str, until: str) -> list[dict]`. One dict per run with keys `run_id`, `brief` (bool|None), `confirmed` (int); in: `mode == "DIFF"` and `tier in ("STANDARD", "DEEP")`; deduplicated by `run_id`; a run with no `L2.5.json` counts 0 only when `L1.json` exists and is `[]`; any other run without `L2.5.json` is excluded as `no_verdicts`.
  - `report(rows: list[dict]) -> dict` with `n_brief`, `n_none`, `mean_brief`, `mean_none`, `gap`, `excluded` (counts by reason: `hunt`, `deferred`, `fast`, `no_receipt`, `no_verdicts`).
  - CLI: `python benchmarks/harness/memory_gap.py --root ~/Documents --since S [--until U]`, JSON to stdout.

- [ ] **Step 1: Write the failing tests** with a `tmp_path` tree of run directories (`.claude/audits/runs/<id>/summary.json`, `local-prior.json`, `L1.json`, `L2.5.json`):
  - `test_brief_is_read_from_the_receipt`: a receipt whose block holds `BUG x:1:BUG-001 [exact] a.py f: ...` gives `True`; a block with only the heading gives `False`; no receipt gives `None`.
  - `test_hosted_brief_uses_the_block_counts`: `prior_block {"exact": 0, "symbol_changed": 0, "file_only": 1}` gives `True`; all zeros and `lessons_block 0` give `False`.
  - `test_only_confirmed_l1_findings_count`: verdicts `BUG-001 CONFIRMED`, `SEC-002 FALSE_POSITIVE`, `FIX-1 CONFIRMED` give `1`.
  - `test_a_clean_run_counts_zero`: `L1.json` `[]` and no `L2.5.json` gives a row with `confirmed == 0`.
  - `test_hunt_deferred_and_fast_are_excluded_and_counted`: one run of each lands in `excluded`, not in the means.
  - `test_gap_is_brief_minus_none`: brief runs 3 and 5, no-brief runs 1 and 1 give `gap == 3.0`.
- [ ] **Step 2:** Run `python -m pytest tests/test_memory_gap.py -q -p no:cacheprovider`. Expected: collection error now (module missing), which is not red proof; red proof comes in Step 4.
- [ ] **Step 3:** Implement the four functions and the CLI in `benchmarks/harness/memory_gap.py`, in the style of `l1_precision.py` (stdlib only, `rglob` over `.claude/audits/runs/*/summary.json`).
- [ ] **Step 4:** Run the tests green. Then red-prove each by one mutation (e.g. `had_brief` ignoring LESSON lines, `confirmed_l1` counting every prefix, `runs` dropping the `[]` clean run) and record mutation and failing assertion in the module docstring of the test.
- [ ] **Step 5:** Commit `benchmarks/harness/memory_gap.py tests/test_memory_gap.py`.

### Task 2: `memory_attribution` and hosted ids in the run summary

**Files:**
- Modify: `cca_checks/pipeline_state.py` (`prior_brief` near line 1294, `_write_summary` near line 1310)
- Test: `tests/test_memory_attribution.py`

**Interfaces:**
- Consumes: `prior.select_records(records, repo)` and `prior.select_lessons(records, repo)` (each record's id is `record["id"]`), `_saved_brief_answer(state, root)`, `_stored_json(state, "L2", root)`, `_stored_json(state, "L2.5", root)`.
- Produces:
  - `prior_brief(state, root)` returns, for a hosted run, `{"source": "hosted", "ids": [ids of select_records then select_lessons, in that order]}`; `ids` is `[]` when the answer or the origin cannot be read. Local path unchanged.
  - `memory_attribution(state: PipelineState, root) -> dict | None`, added to `summary.json` as key `"memory_attribution"`. `None` when `state.mode != "DIFF"` or `prior_brief` is `None` or lists no ids. Otherwise:
    `{"findings_citing": int, "missing": int, "malformed": int, "by_verdict": {"CONFIRMED": int, "FALSE_POSITIVE": int, "other": int}, "confirmed_ids": [str, sorted, unique], "unlisted": int}`
    over the L2 findings; a finding cites when `memory_ids` is a non-empty list of strings; `missing` counts findings without the key, `malformed` those whose value is not a list of strings; duplicate ids within a finding count once; `unlisted` counts cited ids not in `prior_brief["ids"]` by exact string match; a finding with no L2.5 verdict counts under `other`.

- [ ] **Step 1: Write the failing tests** (pattern: `tests/test_l1_dispatch_brief.py::test_the_summary_counts_the_local_brief`, which drives a real run to `pipeline close` with `git_repo`):
  - `test_hosted_brief_records_its_ids`: a saved `prior-records.json` with one bug and one lesson record for the repo gives `prior_brief["ids"] == [bug_id, lesson_id]`.
  - `test_attribution_counts_citations_by_verdict`: L2 findings `BUG-001 memory_ids [id1]` (CONFIRMED), `CODE-002 []` (CONFIRMED), `SEC-003 [id1, "nope"]` (FALSE_POSITIVE) give `findings_citing 2`, `by_verdict {"CONFIRMED": 1, "FALSE_POSITIVE": 1, "other": 0}`, `confirmed_ids [id1]`, `unlisted 1`.
  - `test_missing_and_malformed_are_counted_apart` (Review Focus 1, 2): one finding without the key, one with `memory_ids: "id1"`; `missing 1`, `malformed 1`, `findings_citing 0`, and `unlisted 0` (no character of `"id1"` is counted).
  - `test_a_repeated_id_counts_once` (Review Focus 3): `memory_ids [id1, id1]` gives `findings_citing 1`, `unlisted 0`.
  - `test_case_variant_is_unlisted` (Review Focus 5): `memory_ids [id1.upper()]` gives `unlisted 1`.
  - `test_no_brief_and_non_diff_runs_report_null` (Review Focus 4): an empty receipt, a HUNT run and a DEFERRED run each give `summary["memory_attribution"] is None`.
- [ ] **Step 2:** Run `python -m pytest tests/test_memory_attribution.py -q -p no:cacheprovider`. Expected: each test FAILS on its assertion (`KeyError`/missing key is not red proof; assert `"memory_attribution" in summary` first so the failure is an AssertionError).
- [ ] **Step 3:** Implement `memory_attribution` and the hosted `ids`, and add the key to `_write_summary`.
- [ ] **Step 4:** Run the file green; record the red proof (the pre-change run of Step 2) in the test module docstring.
- [ ] **Step 5:** Commit `cca_checks/pipeline_state.py tests/test_memory_attribution.py`.

### Task 3: Ask for the fields in the auditor prompts

**Files:**
- Modify: the nine L1 files in `cca_checks/plugin/agents/` (`cca-bug-auditor`, `cca-code-auditor`, `cca-security-auditor`, `cca-perf-auditor`, `cca-doc-auditor`, `cca-env-validator`, `cca-numeric-auditor`, `cca-dep-auditor`, `cca-deploy-auditor`)
- Modify: `cca_checks/plugin/commands/audit-fix.md` (Findings Schema; Step 1 template rule on the PRIOR BUGS block; Step 2 consolidation rule 1)
- Test: `tests/test_l1_auditor_prose.py` (extend)

**Interfaces:**
- Produces: the finding keys `memory_ids` (list of strings, `[]` when none) and `memory_how` (string, `""` when none), consumed by Task 2.

- [ ] **Step 1: Write the failing tests:**
  - `test_every_finding_carries_memory_fields[name]`: the `## Return value` section names `memory_ids` and `memory_how`, and the phrase "`[]` when no record led to it".
  - `test_memory_section_explains_the_brief[name]`: a `## Prior bugs and lessons` section exists, says what the records are ("bugs CCA confirmed and fixed"), what to do with each ("name its bug class"), when to cite ("led you to"), and contains "exactly as valid as one that cites".
  - `test_memory_never_suppresses[name]`: that section contains no "do not report", "suppress", "skip" or "only report".
  - `test_schema_and_consolidation_carry_the_fields`: the Findings Schema block in `audit-fix.md` holds `"memory_ids"` and `"memory_how"`; Step 2 rule 1 says a merged finding takes the union of its sources' `memory_ids`.
- [ ] **Step 2:** Run the file; the new tests FAIL on their assertions.
- [ ] **Step 3:** Write the section and the two field lines in each agent file (one shared text, as in PR 104), the schema keys, and the Step 2 union rule. The section must say a finding without memory is exactly as valid as one with it.
- [ ] **Step 4:** Run green; red-prove `test_memory_never_suppresses` by inserting "suppress" into one file's section, and record every proof in the module docstring.
- [ ] **Step 5:** Commit the ten prompt files and the test.

### Task 4: Changelog, suite, PR

**Files:**
- Modify: `CHANGELOG.md` (Unreleased)

- [ ] **Step 1:** Add an Unreleased entry: what the fields are, `memory_attribution` in `summary.json`, hosted ids in `prior_brief`, and that the effect is pre-registered and unmeasured.
- [ ] **Step 2:** Run `bash ~/.claude/bin/vps2-pytest -q -p no:cacheprovider`; read the summary line (expected: all passed, no new skips). Run `ruff check cca_checks tests benchmarks/harness/memory_gap.py`.
- [ ] **Step 3:** Commit, push, open the PR (template `.github/pull_request_template.md`), and wait for the owner's merge.

### Task 5: Install and open the window (after merge, owner's go)

- [ ] **Step 1:** Confirm no run is live (`~/.claude/cca/open-runs.json`, each entry's `run-state.json`).
- [ ] **Step 2:** `pip install --user --force-reinstall --no-deps git+https://github.com/GiulioDER/cca-audit@<merge sha>`, then `install --target ~` and `install-codex` through `python -c "from cca_checks.plugin.cli import main; ..."`. Check the replaced `*.bak` files equal the previous shipped copies modulo CRLF.
- [ ] **Step 3:** Verify all 13 agents match the merge commit in `~/.claude/agents` and `~/.codex/skills/cca-audit/references/agents`, and record the install timestamp as the window start in memory (`l1-auditor-instructions-rewrite` memo or a new one).
