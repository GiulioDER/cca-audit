---
description: "6-layer CCA audit+fixing pipeline. Runs 6 parallel auditors on changed files, consolidates findings, implements P1+P2 fixes, re-verifies, and runs architect-reviewer final gate. Triggers: 'audit+fixing', 'audit and fix', 'cca audit', 'run the audit'."
---

# CCA Audit + Fix Pipeline (6 Layers, 6 Parallel Auditors)

Run the full code audit and auto-fix pipeline on changed files.
This is a DETERMINISTIC workflow — follow every step exactly.

## Arguments ($ARGUMENTS)

- (empty) = audit+fix all uncommitted changes (staged + unstaged)
- `commit` = audit the diff of the last N commits (e.g. `commit 1`, `commit 2`)
- `files path1 path2 ...` = audit specific files only
- `no-fix` = audit only, report findings but do NOT implement fixes
- `p1-only` = only fix P1 Critical findings, skip P2/P3
- `deferred` = second pass — fix P3 items deferred from the previous round (see § Second Pass below)

## Step 0: Detect Target Files

Determine which files to audit based on $ARGUMENTS:

```
IF $ARGUMENTS contains "commit":
  N = number after "commit" (default 1)
  FILES = git diff HEAD~N --name-only --diff-filter=ACMR
  DIFF_CMD = "git diff HEAD~N"
ELIF $ARGUMENTS contains "files":
  FILES = remaining args after "files"
  DIFF_CMD = "git diff HEAD -- <files>"
ELSE:
  FILES = git diff --name-only --diff-filter=ACMR HEAD  (staged+unstaged vs HEAD)
  IF FILES is empty:
    FILES = git diff --name-only HEAD~1  (last commit)
    DIFF_CMD = "git diff HEAD~1"
  ELSE:
    DIFF_CMD = "git diff HEAD"
```

If no files found, STOP with "No changed files to audit."

## Step 0.5: Language & Tooling Detection

Auto-detect before launching auditors:

```
LANGUAGES = detect from file extensions in FILES:
  .py → Python    .ts/.tsx/.js/.jsx → TypeScript/JavaScript
  .go → Go        .rs → Rust
  .java → Java    .rb → Ruby

TEST_CMD = detect:
  Python: "pytest" (if pytest.ini/pyproject.toml/conftest.py exists)
  TypeScript/JS: "npm test" or "jest" or "vitest" (from package.json scripts)
  Go: "go test ./..."
  Rust: "cargo test"

LINT_CMD = detect:
  Python: "ruff check" (if ruff.toml/pyproject.toml[ruff] exists) or "flake8"
  TypeScript/JS: "eslint" (from package.json) or "biome"
  Go: "golangci-lint run"
  Rust: "cargo clippy"
```

Report: "Auditing N files ({LANGUAGES}): <file list>"

## Step 1: Layer 1 — Parallel Auditors (6 agents)

Launch ALL 6 auditors in a SINGLE message (parallel Agent tool calls).
Each agent gets the SAME file list, DIFF_CMD, and detected languages.

**CRITICAL**: All 6 must launch in ONE message — not sequentially.

### Agent 1: Code Quality Auditor
```
subagent_type: code-auditor
Scope: Type safety, complexity, DRY violations, magic numbers, naming,
       dead code, unused imports. NOT security, NOT runtime bugs.
Focus: ONLY new/changed code (use DIFF_CMD).
Output: Finding IDs CODE-001..N, severity, file:line, fix suggestion.
```

### Agent 2: Bug Auditor
```
subagent_type: bug-auditor
Scope: Runtime bugs, null refs, error handling gaps, race conditions,
       resource leaks, type mismatches, logic bugs, edge cases.
       NOT security vulnerabilities.
Focus: ONLY new/changed code.
Output: Finding IDs BUG-001..N, severity, file:line, impact, fix.
```

### Agent 3: Security Auditor
```
subagent_type: security-auditor
Scope: SINGLE AUTHORITY for all security. SQL injection, secrets, auth,
       input validation, config safety, data integrity, dependency CVEs.
Focus: ONLY new/changed code.
Output: Finding IDs SEC-001..N, CVSS estimate, file:line, attack vector, fix.
```

### Agent 4: Performance Auditor
```
subagent_type: perf-auditor
Scope: Slow queries, hot-path overhead, memory, connection management,
       redundant computation, query optimization.
Focus: ONLY new/changed code.
Output: Finding IDs PERF-001..N, severity, file:line, estimated impact, fix.
```

### Agent 5: Documentation Auditor
```
subagent_type: doc-auditor
Scope: Missing docs on non-obvious public functions, stale comments
       that contradict new code, missing type annotations.
       Do NOT flag self-explanatory functions.
Focus: ONLY new/changed code.
Output: Finding IDs DOC-001..N, severity, file:line.
```

### Agent 6: Environment Validator
```
subagent_type: env-validator
Scope: Config consistency across files, hardcoded values that should be
       configurable, cross-profile leakage, naming consistency of new
       constants/config keys across all files that reference them.
Focus: ONLY new/changed code.
Output: Finding IDs ENV-001..N, severity, file:line.
```

### Prompt Template for Each Agent

Use this template, filling in the agent-specific scope:

```
Audit these {N} files for {SCOPE_DESCRIPTION}.

Files changed:
{NUMBERED_FILE_LIST}

Languages detected: {LANGUAGES}
Working directory: {CWD}

Check the CHANGED code (use `{DIFF_CMD}`) for:
{AGENT_SPECIFIC_CHECKLIST}

Focus ONLY on new/changed code. Don't audit pre-existing issues.
Report with IDs ({PREFIX}-001, etc.), severity (Critical/High/Medium/Low),
file:line, and concrete fix suggestions. Be specific.
```

## Step 2: Layer 2 — Consolidate Findings

After all 6 agents return, build a consolidated table.

### Deduplication Rules
1. **Same file:line across auditors** → merge into one finding, keep highest severity
2. **Same issue type on same file** → merge, cite all source auditors
3. **ENV findings about missing config** → verify with Grep/Read before accepting (false positive pattern: the config may exist in a different module)

### Priority Framework
- **P1 Critical** (fix before deploy): Security vulns, data corruption, auth bypass, injection
- **P2 High** (fix now): DRY violations creating divergence risk, stale comments misleading devs, incorrect thresholds, config inconsistencies
- **P3 Nice-to-have**: Cosmetic, style, naming, unused params

Output a markdown table:
```
| ID | Finding | Auditors | Severity | File |
```

## Step 3: Layer 3 — Fix Plan

IF $ARGUMENTS contains "no-fix": STOP here. Report findings and exit.

Otherwise, list fixes to implement:
- Always fix P1
- Fix P2 unless $ARGUMENTS contains "p1-only"
- Skip P3 (mention in report as "deferred")

## Step 4: Layer 4 — Implement Fixes

For each fix in the plan:
1. Read the target file (current state)
2. Apply the fix via Edit tool
3. Mark the fix as done

Rules:
- Minimal diffs — fix ONLY what the audit found
- Do NOT refactor unrelated code
- Do NOT change test structure unless a test is wrong
- If uncertain about a fix, flag as BLOCKED for human review

## Step 5: Layer 5 — Re-verify

Run the detected test and lint commands from Step 0.5:
1. `{TEST_CMD}` on the relevant test file(s) — baseline must hold
2. `{LINT_CMD}` on all changed files — must be clean (pre-existing warnings OK)

If tests fail: diagnose, fix, re-run. Do NOT skip.

## Step 6: Layer 6 — Architect-Reviewer Final Gate

Launch ONE architect-reviewer agent:

```
subagent_type: architect-reviewer
Prompt: Review the full diff (feature + audit fixes). Assess Completeness,
Quality, Correctness, Security. Verdict: APPROVED / REVISE / BLOCKED.
```

- **APPROVED** → commit the audit fixes, report summary
- **REVISE** → implement the reviewer's feedback, re-verify, re-submit (max 3 iterations, then escalate to user)
- **BLOCKED** → STOP. Report the blocker to the user. Do NOT commit.

## Step 7: Commit

If APPROVED, create a separate commit for audit fixes:

```
fix(<scope>): N audit fixes from 6-layer CCA review

P1 Critical:
- <list P1 fixes, one line each>

P2:
- <list P2 fixes, one line each>

P3:
- <list P3 fixes if any, one line each>

Audit: 6 parallel agents, X raw findings → Y unique after dedup → Z fixed.
Architect-reviewer: APPROVED.

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
```

## Output Summary

At the end, print:

```
## CCA Audit+Fix Complete

| Layer | Status |
|-------|--------|
| 1. Parallel Audit (6 agents) | DONE — X raw findings |
| 2. Consolidation | DONE — Y unique after dedup |
| 3. Fix Plan | DONE — Z fixes planned (P1: A, P2: B, P3: C deferred) |
| 4. Implementation | DONE — Z fixes applied |
| 5. Re-verify | DONE — tests pass, lint clean |
| 6. Architect Gate | APPROVED |
| 7. Commit | <hash> |

### P1 Fixes (Critical)
- <list>

### P2 Fixes
- <list>

### Deferred (P3)
- <list>

💡 To fix deferred items, run: `/audit-fix deferred`
```

## Second Pass — Fixing Deferred Items

When invoked with `deferred`, skip the full 6-agent audit and instead:

1. **Read the last commit message** (`git log -1 --format=%B`). Extract the `P3:` section listing deferred items.
   - If no P3 section found, also check the previous 3 commits for a CCA audit commit (contains "audit fixes from 6-layer CCA review").
   - If still nothing, STOP with "No deferred items found. Run a full `/audit-fix` first."

2. **Parse deferred items** into a fix list. Each item has: description, file (if mentioned), category.

3. **For each deferred item**:
   - Read the target file
   - Assess whether the fix is still relevant (code may have changed since the first pass)
   - If still relevant: implement the fix (same rules as Step 4 — minimal diffs, no refactoring)
   - If no longer relevant (code moved/deleted): mark as STALE and skip

4. **Re-verify** (same as Step 5): run TEST_CMD + LINT_CMD. Fix failures.

5. **Commit** with message:
   ```
   fix(<scope>): N deferred fixes from CCA second pass

   P3 fixes:
   - <list of fixes, one line each>

   Stale (skipped):
   - <list of stale items, if any>

   Second pass of 6-layer CCA review.

   Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>
   ```

6. **Report**:
   ```
   ## CCA Second Pass Complete

   | Item | Status |
   |------|--------|
   | <description> | FIXED / STALE / BLOCKED |

   All deferred items from the previous audit round are now resolved.
   ```

This two-pass workflow ensures the audit is fully closed out:
- **Round 1** (`/audit-fix`): fixes P1 Critical + P2 High, defers P3 cosmetic/style items
- **Round 2** (`/audit-fix deferred`): cleans up P3 items in a separate commit
