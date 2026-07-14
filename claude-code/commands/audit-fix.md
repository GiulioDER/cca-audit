---
description: "Canonical CCA audit+fix pipeline. TIERED: auto-selects FAST / STANDARD / DEEP by diff size + high-stakes risk. STANDARD/DEEP add anti-hallucination (L2.5), anti-regression (L5.5), conditional domain auditors (high-stakes/numeric/data), deployability, and fix→finding mapping. Triggers: 'audit+fixing', 'audit and fix', 'cca audit', 'run the audit'. Args: (empty)|commit N|files ...|hunt <paths>|no-fix|p1-only|fast|deep|deferred. HUNT MODE audits a codebase you did NOT write for pre-existing bugs (whole-file, no diff, target-viability pre-flight, forced DEEP)."
---

# CCA Audit + Fix Pipeline (Tiered, Canonical)

> **This is THE pipeline.** It replaces the old v1 (6-auditor) / v2 (9-auditor) split: the v2-grade
> safety gates (anti-hallucination, anti-regression, domain auditors, fix→finding mapping) are now the
> **default**, not a manual opt-in. The old lightweight behaviour survives as the `fast` tier.
> `/audit-fix-v2` still resolves — it is now a thin alias that forces the `DEEP` tier.

This is a DETERMINISTIC workflow — follow every step exactly.

## Arguments ($ARGUMENTS)

- (empty) = audit+fix all uncommitted changes (staged + unstaged), tier auto-selected
- `commit` = audit the diff of the last N commits (e.g. `commit 1`, `commit 2`)
- `files path1 path2 ...` = audit specific files only
- `no-fix` = audit only (run L1→L2.5, report findings + verdicts, do NOT implement fixes)
- `p1-only` = only fix P1 Critical findings, skip P2/P3
- `fast` = force FAST tier (3 core auditors, no gates) regardless of diff
- `deep` = force DEEP tier (all domain auditors + adversarial verify) regardless of diff
- `hunt path1 [path2 ...]` = **HUNT MODE** — audit the named paths IN FULL for **pre-existing** bugs,
  with no diff. This is the mode for a codebase you did NOT write: an OSS dependency, a repo you are
  evaluating, a legacy service. Runs the Step 0.4 viability pre-flight first and forces DEEP. Paths
  are REQUIRED — there is no repo-wide default.
- `deferred` = second pass — fix P3 items deferred from the previous round (see § Second Pass)

> **Argument precedence:** `no-fix` always wins. If `no-fix` is present, never edit or commit — on
> the `deferred` path too (report the deferred items, do not apply them).

## Step 0: Detect Target Files

```
IF $ARGUMENTS contains "hunt":
  TARGETS = paths after "hunt"   # REQUIRED; if empty, STOP: "hunt needs explicit paths"
  Run the Step 0.4 viability pre-flight. Any REJECT → STOP, and spawn NO auditors.
  FILES = source files under TARGETS, EXCLUDING tests/, examples/, docs/, generated code
          (protobuf / transpiled output) and vendored deps
  DIFF_CMD = NONE                # sentinel: there IS no diff; auditors read whole files
  MODE = HUNT
ELIF $ARGUMENTS contains "commit":
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

> `MODE` defaults to `DIFF`; only the `hunt` branch (above) sets `MODE = HUNT`. Every `MODE`-conditional
> step below branches on this.

## Step 0.4: Target Viability Pre-flight (MODE=HUNT only — BLOCKING)

Before spawning a SINGLE auditor against a repo you do not own, verify all five gates. Report the
table. If any REJECT fires, **STOP** — do not audit.

| Gate | Check | Fail |
|------|-------|------|
| **Alive** | default branch pushed within ~90d, AND the newest commits are not an archive/deprecation notice, AND the README carries no archive banner | REJECT |
| **Accepts contributions** | `CONTRIBUTING.md` exists, OR ≥1 merged PR from a non-org author in the last 6 months | REJECT |
| **Test harness** | a runnable test suite exists — you need somewhere to put the red→green repro | REJECT |
| **Language** | the language is one this pipeline audits well (not transpiled / generated output) | REJECT |
| **Money / irreversible surface** | order, payment, signing, auth, or destructive paths exist | Not a reject — this only decides whether the STAKES/NUM domain auditors dispatch |

```bash
gh repo view <owner>/<repo> --json isArchived,pushedAt,description
gh pr list --repo <owner>/<repo> --state merged --limit 10 --json author,title
head -20 README.md | grep -iE 'archiv|deprecat|no longer maintained|do not use'
```

**Why this gate exists.** Every signal can look perfect on a dead repository: real code, a real test
suite, exactly the right bug class — and an archive banner in the README that says "no longer
maintained, do not use." Auditing it burns the whole run and produces a fix nobody can merge. A repo
that has already deprecated itself is the classic trap this gate catches.

**The generalisation, which is the real lesson: check the target is alive before you work on it.** It
applies to a repo, a service, a branch, or a dependency. Most wasted effort is not a wrong answer to
the question; it is a correct answer to a dead question.

## Step 0.5: Language, Tooling & Domain Detection

```
LANGUAGES = detect from file extensions in FILES:
  .py → Python    .ts/.tsx/.js/.jsx → TypeScript/JavaScript
  .go → Go        .rs → Rust    .java → Java    .rb → Ruby

TEST_CMD = detect:
  Python: "pytest" (if pytest.ini/pyproject.toml/conftest.py exists)
  TypeScript/JS: "npm test" or "jest" or "vitest" (from package.json scripts)
  Go: "go test ./..."        Rust: "cargo test"

LINT_CMD = detect:
  Python: "ruff check" (if ruff.toml/pyproject.toml[ruff]) or "flake8"
  TypeScript/JS: "eslint" (from package.json) or "biome"
  Go: "golangci-lint run"    Rust: "cargo clippy"
```

**Domain detection (drives conditional dispatch).** Map FILES to domains using generic, content-based
signals. **Customize the path/keyword lists below for your project.**

```
HIGH_STAKES_PATHS = any path matching *payment* , *billing* , *money* , *fund* , *transfer* ,
                    *order* , *checkout* , *auth* , *permission* , *risk* , *delete* , *destroy* ,
                    *migrat* — i.e. code that moves money, changes access, or is irreversible.
NUMERIC_PATHS     = HIGH_STAKES_PATHS + any file doing non-trivial arithmetic:
                    price/qty/amount/rate/ratio/percent/decimal/units/conversion math.
DATA_PATHS        = migrations/ , any file with SQL / ORM / a DB client , any new
                    CREATE TABLE / ALTER TABLE / GRANT , schema or serialization code.
DEP_PATHS         = requirements*.txt , pyproject.toml , setup.py , Pipfile , package.json ,
                    go.mod , Cargo.toml , and the matching lockfiles.
DEPLOY_PATHS      = anything that ships to a runtime target: deployable source, service/unit
                    definitions, release/deploy scripts, migrations, DEP_PATHS.
```

Set flags (fail toward coverage — when in doubt, set the flag):
`RUN_STAKES = FILES ∩ HIGH_STAKES_PATHS ≠ ∅` · `RUN_NUM = FILES ∩ NUMERIC_PATHS ≠ ∅` ·
`RUN_DAT = FILES ∩ DATA_PATHS ≠ ∅` · `RUN_DEP = FILES ∩ DEP_PATHS ≠ ∅` ·
`RUN_DEPLOY = FILES ∩ DEPLOY_PATHS ≠ ∅`.

## Step 0.6: Tier Selection

Pick the tier. Explicit `fast` / `deep` args override auto-selection.

```
HIGH_STAKES = RUN_STAKES OR RUN_NUM          # diff touches a high-stakes / numeric path
SIZE = total changed lines (git diff --shortstat) ; FILE_COUNT = |FILES|

IF MODE == HUNT:     TIER = DEEP             # hunt is ALWAYS deep — the adversarial gate IS the point
ELIF arg "fast":     TIER = FAST
ELIF arg "deep":     TIER = DEEP
ELIF HIGH_STAKES:    TIER = DEEP             # high-stakes is ALWAYS deep, never auto-downgraded
ELIF SIZE <= 40 AND FILE_COUNT <= 2 AND NOT (RUN_DAT OR RUN_DEPLOY):
                     TIER = FAST             # trivial, low-stakes, non-deploy diff
ELSE:                TIER = STANDARD
```

**What each tier runs:**

| Stage | FAST | STANDARD | DEEP |
|-------|------|----------|------|
| L1 generic auditors | security, bug, code only | all 6 | all 6 |
| L1 domain (STAKES/NUM/DAT) | — | if flag set | if flag set |
| L1 dep-auditor | — | if RUN_DEP | if RUN_DEP |
| L1 deploy-auditor | — | if RUN_DEPLOY | if RUN_DEPLOY |
| L2.5 findings verification | — | single fp-check | fp-check + **adversarial 2-of-3 on high-stakes P1** |
| L4 P1 fix style | direct | red→green test | red→green test |
| L5.5 regression diff | — | yes | yes |
| L6 architect gate | yes (verdict only) | yes + mapping | yes + mapping |

Report: `Tier=<T> | MODE=<DIFF|HUNT> | <N> files (<LANGUAGES>) | domains STAKES=<…> NUM=<…> DAT=<…> DEP=<…> DEPLOY=<…> | size=<SIZE>L`

**In HUNT mode, also log every file under TARGETS that no auditor reached.** A hunt that silently
truncates its own coverage reads as "audited everything" when it did not.

## Findings Schema (canonical)

**Every auditor returns a JSON array as the FIRST thing in its response**, then optional prose. The
orchestrator consumes the JSON RETURN VALUE — it does NOT depend on any `.claude/audits/*.md` file
(those are optional audit-trail only). Each finding object:

```json
{
  "id": "SEC-001",                 // {PREFIX}-{NNN}, prefix per auditor
  "auditor": "security-auditor",
  "severity": "Critical",          // Critical | High | Medium | Low
  "priority": "P1",                // P1 | P2 | P3  (auditor's proposed priority)
  "category": "sql-injection",     // short stable slug, used for dedup
  "file": "src/orders/repo.py",
  "line": 142,                     // best single line; 0 if file-level
  "claim": "User-controlled id interpolated into a SQL string.",
  "evidence": "f\"... WHERE id='{order_id}'\" at repo.py:142",
  "suggested_fix": "Use a parameterized query (placeholder binding).",
  "confidence": 0.9,               // 0..1 — auditor's own confidence
  "high_stakes": true              // true if on a HIGH_STAKES/NUMERIC path (drives adversarial verify)
}
```

Dedup key = `(file, line, category)`. This makes consolidation deterministic instead of eyeballed.

## Step 1: Layer 1 — Parallel Auditors

Launch ALL applicable auditors (per the tier table) in a SINGLE message — parallel Agent calls, never
sequential. Each agent gets the same FILES, DIFF_CMD, LANGUAGES, and the Findings Schema above.

### Generic auditors

| # | Agent | subagent_type | Prefix | Scope |
|---|-------|---------------|--------|-------|
| 1 | Code Quality | `code-auditor` | `CODE-` | Type safety, complexity, DRY, magic numbers, naming, dead code, unused imports. NOT security, NOT runtime bugs. |
| 2 | Bug | `bug-auditor` | `BUG-` | Runtime bugs, null refs, error-handling gaps, race conditions, resource leaks, type mismatches, logic bugs, edge cases. NOT security. |
| 3 | Security | `security-auditor` | `SEC-` | SINGLE AUTHORITY for security. Injection, secrets, auth, input validation, config safety, dependency CVEs. |
| 4 | Performance | `perf-auditor` | `PERF-` | Slow queries, hot-path overhead, memory, connection mgmt, redundant computation. *(STANDARD/DEEP)* |
| 5 | Documentation | `doc-auditor` | `DOC-` | Missing docs on non-obvious public fns, stale comments contradicting new code. Don't flag self-explanatory fns. *(STANDARD/DEEP)* |
| 6 | Environment | `env-validator` | `ENV-` | Config consistency, hardcoded values that should be configurable, naming consistency of new keys. *(STANDARD/DEEP)* |

FAST tier runs only #1–#3.

### Domain & infra auditors (conditional)

#### Agent 7 — High-Stakes / Safety (run if RUN_STAKES)
```
subagent_type: bug-auditor   |   Prefix: STAKES-
Scope: Correctness of high-stakes / irreversible operations — anything that moves money, changes
       access, deletes data, or cannot be undone. Check every such path:
       - Bounds & limits respected (no unbounded amount / size / scope); caps enforced, not just computed.
       - Guards / kill-switches present, reachable, and not bypassable.
       - Side-effecting actions actually wired to the code path, not only calculated.
       - Idempotency / double-execution protection where a repeat would be harmful.
       CUSTOMIZE: add your project's hard invariants as explicit assertions, e.g.
       "<critical guard> must never be bypassed", "<limit constant> is the enforced cap".
Output: STAKES- findings per schema, high_stakes=true.
```

#### Agent 8 — Numerical / Units (run if RUN_NUM)
```
subagent_type: numeric-auditor   |   Prefix: NUM-
Scope: Dimensional + sign correctness — the "math looks right but units/sign are wrong" class.
       - Sign / direction correct end-to-end (an input's sign carried through to its effect).
       - Units: no implicit mixing (ms vs s, bytes vs KB, percent vs fraction vs basis points).
       - Scaling: fixed-point / decimal factors applied in the right direction; no precision loss.
       - Rounding / truncation direction; off-by-one on thresholds and indices.
       - Conversions invert correctly; ratios use the intended denominator.
Output: NUM- findings per schema, high_stakes=true.
```

#### Agent 9 — Data-Integrity / Storage (run if RUN_DAT)
```
subagent_type: bug-auditor   |   Prefix: DAT-
Scope: Data-layer correctness on the changed code.
       - New tables/columns/objects: migration present AND required permission grants in place.
       - Column / field type assumptions correct (no implicit text↔date↔number coercion bugs).
       - Typed/structured columns accessed via the project's safe accessor, not raw.
       - Queries do not silently rely on stale cache / stale reads.
       - Serialization round-trips correctly; no schema/version mismatch.
       CUSTOMIZE: add your stack's rules (database name, required role grants, forbidden backends,
       safe-loader helpers).
Output: DAT- findings per schema.
```

#### Agent 10 — Dependency (run if RUN_DEP)
```
subagent_type: dep-auditor   |   Prefix: DEP-
Scope: ONLY when a dependency manifest/lockfile changed. Maintenance health, licenses,
       unused/duplicate deps, lighter alternatives. CVEs belong to security-auditor (single
       authority) — do NOT duplicate. Flag any dep change that would break a pinned constraint
       (see deploy-auditor for the deploy-side impact).
Output: DEP- findings per schema.
```

#### Agent 11 — Deployability (run if RUN_DEPLOY)
```
subagent_type: deploy-auditor   |   Prefix: DEPLOY-
Scope: hazards that surface BETWEEN merge and a healthy running deploy, which the code diff alone
       does not reveal (protected/generated files, dependency pin/lock breakers, service+scheduler
       unit pairing, migration permission grants, deploy-target assumptions). See the agent file.
Output: DEPLOY- findings per schema.
```

### Prompt Template for Each Agent
```
Audit these {N} files for {SCOPE_DESCRIPTION}.

Files changed:
{NUMBERED_FILE_LIST}

Languages: {LANGUAGES}   Working dir: {CWD}
Context: {PROJECT_CONTEXT}
(Default: "This is a software project; apply extra scrutiny to any code that handles money, auth,
user data, or irreversible actions." Replace {PROJECT_CONTEXT} with your project's one-line risk note.)

─── IF MODE == DIFF ──────────────────────────────────────────────────────────
Check the CHANGED code (use `{DIFF_CMD}`) for: {AGENT_SPECIFIC_CHECKLIST}

RULES:
- Focus ONLY on new/changed code. Don't audit pre-existing issues.
- BEFORE flagging a design / threshold / strategy choice, check it isn't an already-SETTLED project
  decision. If the project keeps a decision log (ADRs, a DECISIONS file, or a searchable memory),
  consult it; if a finding contradicts a recorded decision, SUPPRESS it and note "settled: <ref>".

─── IF MODE == HUNT ──────────────────────────────────────────────────────────
Read each file IN FULL. There is no diff. Audit ALL of it for: {AGENT_SPECIFIC_CHECKLIST}

RULES:
- **Pre-existing bugs are the TARGET.** Age is not evidence of correctness — code can be wrong for
  years. Do NOT skip a path because it is old, popular, well-starred, or widely depended upon. The
  fact that nobody has reported it is the reason you are looking, not proof that it works.
- This is a codebase you did NOT write. Before flagging a design or threshold choice, check the
  UPSTREAM project's own issues, PRs, and docs — not yours. If it is a deliberate upstream decision,
  SUPPRESS it and cite the reference.
- You are not reviewing a diff for a colleague; you are looking for a defect that ships today. Prefer
  ONE bug you can PROVE with a failing test over ten you can merely describe.

─── END ──────────────────────────────────────────────────────────────────────
- Return your findings as a JSON array per the Findings Schema as the FIRST thing in your reply,
  then a short prose summary. The JSON is the authoritative return value.
```

## Step 2: Layer 2 — Consolidate Findings (deterministic)

Collect every auditor's JSON return value. Deterministic dedup:
1. **Same `(file, line, category)`** → merge into one, keep highest severity, cite all source auditors.
2. **Same category on same file within ±3 lines** → merge.
3. **ENV/DAT/DEPLOY "missing config/table/grant"** → tag for L2.5 verification (common FP: exists in
   another module/migration).

Re-map each auditor `priority` to the canonical framework (auditors propose; orchestrator decides):
- **P1 Critical** (fix before deploy): security vulns, data corruption, auth bypass, injection, unsafe
  handling of money/irreversible actions (wrong bound, bypassed guard, wrong sign).
- **P2 High**: DRY divergence risk, stale misleading comments, wrong thresholds, config/data
  inconsistencies, unit mismatches not yet causing loss.
- **P3 Nice-to-have**: cosmetic, style, naming, unused params.

Output table: `| ID | Finding | Auditors | Severity | P | high-stakes? | File:line |`

## Step 2.5: Layer 2.5 — Findings Verification (ANTI-HALLUCINATION) — STANDARD/DEEP

Verify P1/P2 are real before any fix (P3 are deferred anyway). FAST tier skips this layer. If there
are zero P1/P2 findings, skip it.

**Non-high-stakes P1/P2** — one `fp-check` agent over the list:
```
subagent_type: fp-check
For each finding, verify against ACTUAL code via Read/Grep:
  (a) does the issue exist at the cited file:line?

  (b)  [MODE=DIFF] is it in code CHANGED in this diff ({DIFF_CMD}), not pre-existing?

  (b′) [MODE=HUNT] is it ALREADY KNOWN UPSTREAM?  ← criterion (b) MUST NOT run in hunt mode: every
       hunt finding is pre-existing by definition, so (b) would reject all of them. Instead search
       the target's open AND closed issues, and its recent commits:
         gh issue list --repo <owner>/<repo> --state all --search "<keywords>"
         gh pr list    --repo <owner>/<repo> --state all --search "<keywords>"
         git log --oneline -20 -- <file>
       Already reported, or already fixed on the default branch ⇒ verdict DUPLICATE (drop it, cite
       the issue/PR URL). A bug someone else already found is not a finding.

  (c) is the stated impact real, or already mitigated (config elsewhere, upstream guard, validated before)?
  (d) does it contradict an already-SETTLED decision?
      (MODE=DIFF: yours — decision log / memory.  MODE=HUNT: the UPSTREAM project's.)
Verdict per finding: CONFIRMED | FALSE_POSITIVE (give evidence) | DUPLICATE (cite URL) | UNCERTAIN.
For UNCERTAIN where a quick test would settle it, PROPOSE a one-line assertion test (do not run blind).
```

**P1 on a high-stakes path (`high_stakes=true`)** — DEEP only: adversarial **2-of-3**.
Launch 3 independent skeptics in ONE message (parallel), each:
```
subagent_type: fp-check
Try to REFUTE this finding. Default verdict = FALSE_POSITIVE unless you can prove the bug is real,
in changed code, and has real impact. Provide the file:line evidence for your verdict.
```
A high-stakes P1 is CONFIRMED only if **≥2 of 3** skeptics fail to refute it. Otherwise → FALSE_POSITIVE
(or UNCERTAIN if split with low confidence → escalate to human, never fix blind).

Apply verdicts: CONFIRMED → fix plan · FALSE_POSITIVE → drop (record with evidence) ·
DUPLICATE → drop (record the upstream URL; it is someone else's find, not yours) ·
UNCERTAIN → list for the user, treat as deferred-to-human.

If `no-fix`: STOP here. Report consolidated findings + verdicts.

## Step 3: Layer 3 — Fix Plan

(Reachable only when `no-fix` is absent.) From CONFIRMED findings only: always fix P1; fix P2 unless
`p1-only`; skip P3 (report as deferred).

## Step 4: Layer 4 — Implement Fixes

Per fix: Read current file → apply via Edit → note which finding ID it resolves.

Rules:
- Minimal diffs — fix ONLY what was CONFIRMED. No refactoring of unrelated code. No test-structure
  changes unless a test is wrong. If uncertain on a high-stakes path → BLOCKED for human (never guess).

**P1 regression test — red→green (STANDARD/DEEP).** For each CONFIRMED P1 (especially
`high_stakes=true`):
1. Write a test that REPRODUCES the bug and currently FAILS (red), next to the module's existing tests.
2. Implement the fix.
3. Confirm the new test PASSES (green) and the baseline still passes.
A P1 fix without a red→green test is incomplete (the architect gate will flag it). FAST tier skips this.

## Step 5: Layer 5 — Re-verify

1. `{TEST_CMD}` on the relevant test file(s) — baseline + any new P1 tests must pass.
2. `{LINT_CMD}` on changed files — clean (pre-existing warnings OK).
If tests fail: diagnose, fix, re-run. Do NOT skip.

## Step 5.5: Layer 5.5 — Regression Diff (ANTI-REGRESSION) — STANDARD/DEEP

If zero fixes were applied, skip. Otherwise one `differential-review` agent over ONLY the audit-fix
diff (Layer 4 changes, not the original feature):
```
subagent_type: differential-review
For each fix hunk, the intended change is to resolve a specific finding. Verify:
  - does NOT alter behaviour outside its finding's scope (no incidental semantic change);
  - no control-flow / sign / units / default-value drift introduced as a side effect;
  - no new path silently bypasses an existing guard.
Map each hunk → finding. Verdict per hunk: SAFE | SCOPE_CREEP | REGRESSION_RISK (file:line + reasoning).
```
REGRESSION_RISK → revert/correct, re-run L5. SCOPE_CREEP → trim to minimal, re-run L5. All SAFE → proceed.

## Step 6: Layer 6 — Architect Gate + Fix→Finding Mapping

One `architect-reviewer` agent (read-only gate — it does NOT edit code; it returns REVISE with
instructions). Pass it the CONFIRMED P1/P2 list (IDs + fix locations) inline. STANDARD/DEEP additionally
require the mapping table.
```
subagent_type: architect-reviewer
Review the full diff (feature + audit fixes). Assess Completeness, Quality, Correctness, Security.
THEN (STANDARD/DEEP) emit a mapping covering every CONFIRMED P1/P2:
  | Finding ID | Fix (file:line) | Red→green test (P1) | Resolves? | Notes |
Verdict rules:
  - Orphan CONFIRMED P1 (no fix, fix that doesn't resolve it, or a P1 missing its red→green test) → REVISE.
  - Phantom fix (change not tied to any finding) → REVISE (trim it).
  - Otherwise judge normally.
Final verdict: APPROVED | REVISE | BLOCKED.
```
APPROVED → commit. REVISE → implement feedback, re-verify (L5 + L5.5), re-submit (max 3 iters, then
escalate). BLOCKED → STOP, report blocker, do NOT commit.

## Step 7: Commit

If APPROVED, a separate commit for the audit fixes:
```
fix(<scope>): N audit fixes from CCA (<TIER> tier)

P1 Critical:
- <fix, finding ID, +red→green test>
P2:
- <fix, finding ID>
P3:
- <deferred items>

Audit: <tier>, <K> auditors (STAKES/NUM/DAT/DEP/DEPLOY conditional) → X raw → Y unique → V confirmed
(F false-positives dropped) → Z fixed. Regression diff: clean. Architect: APPROVED.

Co-Authored-By: Claude <noreply@anthropic.com>
```

## Output Summary

```
## CCA Audit+Fix Complete — <TIER> tier

| Stage | Status |
|-------|--------|
| 0.6 Tier | <FAST/STANDARD/DEEP> (auto / forced) |
| 1. Parallel Audit | DONE — X raw (gen + STAKES/NUM/DAT/DEP/DEPLOY on/off) |
| 2. Consolidation | DONE — Y unique after dedup |
| 2.5 Verification | DONE — V confirmed, F false-positives, U uncertain (adversarial: <n high-stakes P1>) |
| 3. Fix Plan | DONE — Z planned (P1:A P2:B P3:C deferred) |
| 4. Implementation | DONE — Z applied (P1 red→green tests: <m>) |
| 5. Re-verify | DONE — tests pass, lint clean |
| 5.5 Regression Diff | DONE — all hunks SAFE |
| 6. Architect Gate | APPROVED — all P1/P2 mapped |
| 7. Commit | <hash> |

### P1 Fixes (Critical)   ### P2 Fixes   ### Dropped False Positives (L2.5, with evidence)
### Uncertain — needs human   ### Deferred (P3)

💡 To fix deferred items, run: `/audit-fix deferred`
```

## Second Pass — Fixing Deferred Items

When invoked with `deferred` (and NOT `no-fix`), skip the full audit:
1. Read the last commit message (`git log -1 --format=%B`); extract the `P3:` section. If none, check
   the previous 3 commits for a CCA commit ("audit fixes from CCA"). If still nothing, STOP with
   "No deferred items found. Run a full `/audit-fix` first."
2. Parse deferred items (description, file if mentioned, category).
3. Per item: Read the file; if still relevant implement (minimal diffs); if code moved/deleted → STALE.
4. Re-verify (L5): TEST_CMD + LINT_CMD. Fix failures.
5. Commit `fix(<scope>): N deferred fixes from CCA second pass` (list fixes + stale items).
6. Report a STATUS table (FIXED / STALE / BLOCKED per item).

> If `no-fix` was passed alongside `deferred`, do NOT edit or commit — just list the deferred items
> and their relevance, then stop.
