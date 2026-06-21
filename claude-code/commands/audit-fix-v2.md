---
description: "CCA Audit+Fix v2 — 9 auditors + 3 verification gates (anti-hallucination, anti-regression, fix→finding mapping). Invoke explicitly with /audit-fix-v2. v1 (/audit-fix) remains the lighter default. Args: (empty)|commit N|files ...|no-fix|p1-only|deferred."
---

# CCA Audit + Fix Pipeline v2 (up to 9 Auditors, 9 Stages)

> **Explicit invocation.** Run this with `/audit-fix-v2`. The lighter v1 (`/audit-fix`) stays
> the default; v1 and v2 coexist on purpose so you can choose depth per task. Both are kept.

This is a DETERMINISTIC workflow — follow every step exactly.

## What's new vs v1

- **Layer 1: up to 9 auditors.** The 6 generic auditors are unchanged from v1; 3 domain auditors
  are added (high-stakes/safety, numerical/units, data-integrity). Domain auditors are dispatched
  **conditionally** based on which parts of the diff they apply to (the 6 generics always run).
- **L2.5 — Findings verification (anti-hallucination):** every P1/P2 finding is re-checked against
  the real code before it enters the fix plan. False positives are dropped; uncertain ones are
  escalated to the user (never fixed blind).
- **L5.5 — Regression diff (anti-regression):** after fixes, a differential review confirms the fix
  diff changed nothing beyond the intent of each finding.
- **L6 extended — fix→finding mapping:** the final gate emits a `finding → fix → status` table.
  An orphan P1 finding (no fix) or a phantom fix (no finding) forces a REVISE.

## Arguments ($ARGUMENTS)

- (empty) = audit+fix all uncommitted changes (staged + unstaged)
- `commit` = audit the diff of the last N commits (e.g. `commit 1`, `commit 2`)
- `files path1 path2 ...` = audit specific files only
- `no-fix` = audit only (run L1→L2.5, report findings, do NOT implement fixes)
- `p1-only` = only fix P1 Critical findings, skip P2/P3
- `deferred` = second pass — fix P3 items deferred from the previous round (see § Second Pass)

> **Argument precedence:** `no-fix` always wins. If `no-fix` is present, never edit or commit —
> on the `deferred` path too (report the deferred items, do not apply them).

## Step 0: Detect Target Files

Determine which files to audit based on $ARGUMENTS:

```
IF $ARGUMENTS contains "commit":
  N = number after "commit" (default 1)
  FILES = git diff HEAD~N --name-only --diff-filter=ACMR
  DIFF_CMD = "git diff HEAD~N"
ELIF $ARGUMENTS contains "files":
  FILES = remaining args after "files"
  Validate each path exists; if the resulting diff is empty, STOP with "No changes in the given files."
  DIFF_CMD = "git diff HEAD -- <files>"
ELSE:
  TRACKED   = git diff --name-only --diff-filter=ACMR HEAD   (staged+unstaged vs HEAD)
  UNTRACKED = git ls-files --others --exclude-standard        (new files not yet added)
  FILES = TRACKED + UNTRACKED
  IF FILES is empty:
    FILES = git diff --name-only --diff-filter=ACMR HEAD~1    (last commit)
    DIFF_CMD = "git diff HEAD~1"
  ELSE:
    DIFF_CMD = "git diff HEAD"   (for untracked files, read the file directly)
```

If no files found, STOP with "No changed files to audit."

## Step 0.5: Language, Tooling & Domain Detection

Auto-detect before launching auditors:

```
LANGUAGES = detect from file extensions in FILES:
  .py → Python    .ts/.tsx/.js/.jsx → TypeScript/JavaScript
  .go → Go        .rs → Rust
  .java → Java    .rb → Ruby

TEST_CMD = detect:
  Python: "pytest" (if pytest.ini/pyproject.toml/conftest.py exists)
  TypeScript/JS: "npm test" or "jest" or "vitest" (from package.json scripts)
  Go: "go test ./..."        Rust: "cargo test"

LINT_CMD = detect:
  Python: "ruff check" (if ruff.toml/pyproject.toml[ruff]) or "flake8"
  TypeScript/JS: "eslint" (from package.json) or "biome"
  Go: "golangci-lint run"    Rust: "cargo clippy"
```

**Domain detection (drives conditional domain dispatch).** Map FILES to domains using generic,
content-based signals. **Customize the path/keyword lists below for your project.**

```
HIGH_STAKES_PATHS = any path matching *payment* , *billing* , *money* , *fund* , *transfer* ,
                    *order* , *checkout* , *auth* , *permission* , *risk* , *delete* , *destroy* ,
                    *migrat* — i.e. code that moves money, changes access, or is irreversible.
NUMERIC_PATHS     = any file doing non-trivial arithmetic: price/qty/amount/rate/ratio/percent/
                    decimal/units/conversion math.
DATA_PATHS        = migrations/ , any file with SQL / ORM / a DB client , any new
                    CREATE TABLE / ALTER TABLE / GRANT , schema or serialization code.
```

Set flags: `RUN_STAKES = (FILES ∩ HIGH_STAKES_PATHS) ≠ ∅`, `RUN_NUM = (FILES ∩ NUMERIC_PATHS) ≠ ∅`,
`RUN_DAT = (FILES ∩ DATA_PATHS) ≠ ∅`. When in doubt, run the domain auditor (fail toward coverage).

Report: "Auditing N files ({LANGUAGES}). Domain auditors: STAKES={on/off} NUM={on/off} DAT={on/off}. Files: <list>"

## Step 1: Layer 1 — Parallel Auditors (up to 9 agents)

Launch ALL applicable auditors in a SINGLE message (parallel Agent tool calls).
The 6 generic auditors ALWAYS run. Each domain auditor runs only if its flag is set.

**CRITICAL**: All auditors launch in ONE message — not sequentially.

### Generic auditors (always run) — same as v1

| # | Agent | subagent_type | Prefix | Scope |
|---|-------|---------------|--------|-------|
| 1 | Code Quality | `code-auditor` | `CODE-` | Type safety, complexity, DRY, magic numbers, naming, dead code, unused imports. NOT security, NOT runtime bugs. |
| 2 | Bug | `bug-auditor` | `BUG-` | Runtime bugs, null refs, error handling gaps, race conditions, resource leaks, type mismatches, logic bugs, edge cases. NOT security. |
| 3 | Security | `security-auditor` | `SEC-` | SINGLE AUTHORITY for security. Injection, secrets, auth, input validation, config safety, dependency CVEs. |
| 4 | Performance | `perf-auditor` | `PERF-` | Slow queries, hot-path overhead, memory, connection mgmt, redundant computation. |
| 5 | Documentation | `doc-auditor` | `DOC-` | Missing docs on non-obvious public fns, stale comments contradicting new code, missing annotations. Don't flag self-explanatory fns. |
| 6 | Environment | `env-validator` | `ENV-` | Config consistency across files, hardcoded values that should be configurable, naming consistency of new keys. |

### Domain auditors (conditional)

#### Agent 7: High-Stakes / Safety Auditor — run if RUN_STAKES
```
subagent_type: bug-auditor
Prefix: STAKES-
Scope: Correctness of high-stakes / irreversible operations — anything that moves money, changes
       access, deletes data, or cannot be undone. Check every such path:
       - Bounds & limits respected (no unbounded amount / size / scope); caps enforced, not just computed.
       - Guards / kill-switches present, reachable, and not bypassable.
       - Side-effecting actions are actually wired to the code path, not only calculated.
       - Idempotency / double-execution protection where a repeat would be harmful.
       CUSTOMIZE: add your project's hard invariants here as explicit assertions, e.g.
       "<critical guard> must never be bypassed", "<limit constant> is the enforced cap".
Output: STAKES-001..N, severity, file:line, real-world impact, concrete fix.
```

#### Agent 8: Numerical / Units Auditor — run if RUN_NUM
```
subagent_type: numeric-auditor
Prefix: NUM-
Scope: Dimensional and sign correctness — the "math looks right but units/sign are wrong" class.
       - Sign / direction correct end-to-end (e.g. an input's sign carried through to its effect).
       - Units: no implicit mixing (ms vs s, bytes vs KB, percent vs fraction vs basis points).
       - Scaling: fixed-point / decimal factors applied in the right direction; no precision loss.
       - Rounding / truncation direction; off-by-one on thresholds and indices.
       - Conversions invert correctly; ratios use the intended denominator.
Output: NUM-001..N, severity, file:line, the dimensional mismatch, concrete fix.
```

#### Agent 9: Data-Integrity / Storage Auditor — run if RUN_DAT
```
subagent_type: bug-auditor
Prefix: DAT-
Scope: Data-layer correctness on the changed code.
       - New tables/columns/objects: migration present AND required permission grants in place.
       - Column / field type assumptions correct (no implicit text↔date↔number coercion bugs).
       - Typed/structured columns accessed via the project's safe accessor, not raw.
       - Queries do not silently rely on stale cache / stale reads.
       - Serialization round-trips correctly; no schema/version mismatch.
       CUSTOMIZE: add your stack's rules here, e.g. database name, required role grants,
       "no <forbidden backend> reintroduced", safe-loader helpers.
Output: DAT-001..N, severity, file:line, integrity risk, concrete fix.
```

### Prompt Template for Each Agent

```
Audit these {N} files for {SCOPE_DESCRIPTION}.

Files changed:
{NUMBERED_FILE_LIST}

Languages detected: {LANGUAGES}
Working directory: {CWD}

Context: {PROJECT_CONTEXT}
(Default: "This is a software project; apply extra scrutiny to any code that handles money,
auth, user data, or irreversible actions." Replace {PROJECT_CONTEXT} with your project's own
one-line description and risk note.)

Check the CHANGED code (use `{DIFF_CMD}`) for:
{AGENT_SPECIFIC_CHECKLIST}

Focus ONLY on new/changed code. Don't audit pre-existing issues.
Report with IDs ({PREFIX}-001, etc.), severity (Critical/High/Medium/Low),
file:line, and concrete fix suggestions. Be specific.
```

## Step 2: Layer 2 — Consolidate Findings

After all auditors return, build a consolidated table.

### Deduplication Rules
1. **Same file:line across auditors** → merge into one finding, keep highest severity.
2. **Same issue type on same file** → merge, cite all source auditors.
3. **ENV/DAT findings about missing config/table** → flag for L2.5 verification (common FP pattern:
   the config/table may exist in a different module or migration).

### Priority Framework
- **P1 Critical** (fix before deploy): security vulns, data corruption, auth bypass, injection,
  unsafe handling of money/irreversible actions (wrong bound, bypassed guard, wrong sign).
- **P2 High** (fix now): DRY divergence risk, stale misleading comments, incorrect thresholds,
  config/data inconsistencies, unit mismatches not yet causing loss.
- **P3 Nice-to-have**: cosmetic, style, naming, unused params.

Output a markdown table:
```
| ID | Finding | Auditors | Severity | File |
```

## Step 2.5: Layer 2.5 — Findings Verification (ANTI-HALLUCINATION)

Before anything is fixed, verify the P1 and P2 findings are real. (P3 are deferred anyway.)
If there are zero P1/P2 findings, skip this layer.

Launch ONE `fp-check` agent with the consolidated P1/P2 list and the `{DIFF_CMD}`:

```
subagent_type: fp-check
Prompt: For each finding below, verify against the ACTUAL code using Read/Grep:
  (a) Does the issue actually exist at the cited file:line?
  (b) Is it in code CHANGED in this diff (use {DIFF_CMD}), not pre-existing?
  (c) Is the stated impact real, or already mitigated elsewhere?
Emit one verdict per finding: CONFIRMED / FALSE_POSITIVE (with evidence) / UNCERTAIN.
{P1_P2_FINDINGS_TABLE}
```

Apply the verdicts:
- **CONFIRMED** → proceed to fix plan.
- **FALSE_POSITIVE** → drop. Record under "Dropped false positives" with the evidence.
- **UNCERTAIN** → do NOT fix blind. List for the user; treat as deferred-to-human.

If `no-fix`: STOP here. Report consolidated findings + verification verdicts.

## Step 3: Layer 3 — Fix Plan

(Reachable only when `no-fix` is absent.) List fixes from CONFIRMED findings only:
- Always fix P1.
- Fix P2 unless $ARGUMENTS contains "p1-only".
- Skip P3 (mention in report as "deferred").

## Step 4: Layer 4 — Implement Fixes

For each fix in the plan:
1. Read the target file (current state).
2. Apply the fix via Edit tool.
3. Mark the fix as done, noting which finding ID it resolves.

Rules:
- Minimal diffs — fix ONLY what the audit confirmed.
- Do NOT refactor unrelated code.
- Do NOT change test structure unless a test is wrong.
- If uncertain about a fix, flag as BLOCKED for human review (do not guess on a high-stakes path).

## Step 5: Layer 5 — Re-verify

Run the detected test and lint commands from Step 0.5:
1. `{TEST_CMD}` on the relevant test file(s) — baseline must hold.
2. `{LINT_CMD}` on all changed files — must be clean (pre-existing warnings OK).

If tests fail: diagnose, fix, re-run. Do NOT skip.

## Step 5.5: Layer 5.5 — Regression Diff (ANTI-REGRESSION)

After fixes verify green, confirm the fixes changed nothing beyond intent.
If zero fixes were applied in Layer 4, skip this layer.

Launch ONE `differential-review` agent:

```
subagent_type: differential-review
Prompt: Review ONLY the diff introduced by the audit fixes (Layer 4 changes, not the original
feature diff). For each fix, the intended change is to resolve a specific finding. Verify:
  - The fix does NOT alter behavior outside the scope of its finding.
  - No control-flow / sign / units / default-value drift was introduced as a side effect.
  - No new code path silently bypasses an existing guard.
Map each hunk to the finding it serves. Flag any hunk changing behavior NOT tied to a finding.
Verdict per hunk: SAFE / SCOPE_CREEP / REGRESSION_RISK, with file:line and reasoning.
```

- Any **REGRESSION_RISK** → revert or correct that hunk, re-run Layer 5.
- **SCOPE_CREEP** → trim the fix to minimal, re-run Layer 5.
- All **SAFE** → proceed.

## Step 6: Layer 6 — Architect Gate + Fix→Finding Mapping

Launch ONE `architect-reviewer` agent. Pass it the CONFIRMED P1/P2 list (IDs + fix locations)
inline — do not rely on a file. In addition to the v1 review, it MUST emit a mapping table.

```
subagent_type: architect-reviewer
Prompt: Review the full diff (feature + audit fixes). Assess Completeness, Quality, Correctness,
Security. Here is the CONFIRMED P1/P2 finding list with the fix applied for each:
{CONFIRMED_FINDINGS_WITH_FIXES}

THEN emit a mapping table covering every CONFIRMED P1/P2 finding:
  | Finding ID | Fix (file:line) | Resolves finding? | Notes |

Rules for the verdict:
  - Every CONFIRMED P1 finding MUST have a fix that resolves it. An orphan P1 → REVISE.
  - A phantom fix (a change not tied to any finding) → REVISE (trim it).
  - Otherwise judge normally.
Final verdict: APPROVED / REVISE / BLOCKED.
```

- **APPROVED** → commit the audit fixes, report summary.
- **REVISE** → implement the reviewer's feedback, re-verify (L5 + L5.5), re-submit
  (max 3 iterations, then escalate to user).
- **BLOCKED** → STOP. Report the blocker to the user. Do NOT commit.

## Step 7: Commit

If APPROVED, create a separate commit for audit fixes:

```
fix(<scope>): N audit fixes from CCA v2 (9-auditor) review

P1 Critical:
- <list P1 fixes, one line each, with finding ID>

P2:
- <list P2 fixes, one line each, with finding ID>

P3:
- <list P3 fixes if any>

Audit: up to 9 auditors (STAKES/NUM/DAT conditional) → X raw → Y unique → V confirmed
(F false-positives dropped) → Z fixed. Regression diff: clean. Architect-reviewer: APPROVED.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

## Output Summary

At the end, print:

```
## CCA Audit+Fix v2 Complete

| Stage | Status |
|-------|--------|
| 1. Parallel Audit (up to 9 auditors) | DONE — X raw findings (STAKES/NUM/DAT: on/off) |
| 2. Consolidation | DONE — Y unique after dedup |
| 2.5. Findings Verification | DONE — V confirmed, F false-positives dropped, U uncertain |
| 3. Fix Plan | DONE — Z fixes planned (P1: A, P2: B, P3: C deferred) |
| 4. Implementation | DONE — Z fixes applied |
| 5. Re-verify | DONE — tests pass, lint clean |
| 5.5. Regression Diff | DONE — all hunks SAFE |
| 6. Architect Gate + Mapping | APPROVED — all P1/P2 mapped to fixes |
| 7. Commit | <hash> |

### P1 Fixes (Critical)
- <list>

### P2 Fixes
- <list>

### Dropped False Positives (L2.5)
- <list with evidence>

### Uncertain — needs human (L2.5)
- <list>

### Deferred (P3)
- <list>

💡 To fix deferred items, run: `/audit-fix-v2 deferred`
```

## Second Pass — Fixing Deferred Items

When invoked with `deferred` (and NOT `no-fix`), skip the full audit and instead:

1. **Read the last commit message** (`git log -1 --format=%B`). Extract the `P3:` section.
   - If no P3 section, check the previous 3 commits for a CCA v2 commit
     (contains "audit fixes from CCA v2").
   - If still nothing, STOP with "No deferred items found. Run a full `/audit-fix-v2` first."

2. **Parse deferred items** into a fix list (description, file if mentioned, category).

3. **For each deferred item**:
   - Read the target file.
   - Assess whether the fix is still relevant (code may have changed).
   - If relevant: implement (same rules as Step 4 — minimal diffs).
   - If no longer relevant: mark STALE and skip.

4. **Re-verify** (L5): run TEST_CMD + LINT_CMD. Fix failures.

5. **Commit**:
   ```
   fix(<scope>): N deferred fixes from CCA v2 second pass

   P3 fixes:
   - <list>

   Stale (skipped):
   - <list>

   Second pass of CCA v2 review.

   Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
   ```

6. **Report** a STATUS table (FIXED / STALE / BLOCKED per item).

> If `no-fix` was passed alongside `deferred`, do NOT edit or commit — just list the deferred
> items and their relevance, then stop.

This two-pass workflow closes the audit out:
- **Round 1** (`/audit-fix-v2`): fixes confirmed P1 + P2, defers P3.
- **Round 2** (`/audit-fix-v2 deferred`): cleans up P3 in a separate commit.
