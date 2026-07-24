# Extending CCA-Audit

Add custom auditors to check domain-specific patterns.

## Adding an Agent File

### 1. Create the agent file

Create `.claude/agents/cca-my-auditor.md` following this template:

```markdown
---
name: my-auditor
description: One-line description of what this auditor checks.
tools: Read, Grep, Glob, Bash
model: inherit
---

# My Custom Audit

## Return Value (Required)

Return a JSON array as the FIRST thing in your response — this is the **authoritative** output the
orchestrator consumes. Use the canonical Findings Schema from `audit-fix.md`:

\```json
[
  {
    "id": "MY-001",
    "auditor": "my-auditor",
    "severity": "High",
    "priority": "P2",
    "category": "my-domain",
    "file": "src/thing.py",
    "line": 42,
    "claim": "What is wrong, in one sentence.",
    "evidence": "What in the code proves it",
    "suggested_fix": "The minimal change",
    "confidence": 0.8,
    "high_stakes": false
  }
]
\```

Optionally also write a human-readable trail to `.claude/audits/AUDIT_MY.md`. The pipeline does
**not** depend on that file.

## Status Block (Required)

Every output MUST start with:
\```yaml
---
agent: my-auditor
status: COMPLETE | PARTIAL | SKIPPED | ERROR
timestamp: [ISO timestamp]
duration: [seconds]
findings: [count]
errors: []
skipped_checks: []
---
\```

## Scope (NON-OVERLAPPING)

**my-auditor checks:**
- [List what this auditor exclusively checks]

**Does NOT check (use other agents):**
- ~~[What it doesn't check]~~ → [which auditor does]

## Checks

[Describe the checks, organized by category]

## Output Format (optional trail)

If you write `.claude/audits/AUDIT_MY.md`, report findings grouped by severity:
Critical > High > Medium > Low. Each finding: `### MY-NNN: Title` with Severity, File:line,
Issue, Fix. This mirrors the JSON return value; it never replaces it.

## Execution Logging

After completing, append to `.claude/audits/EXECUTION_LOG.md`:
\```
| [timestamp] | my-auditor | [status] | [duration] | [findings] | [errors] |
\```

## Output Verification

Before completing:
1. Verify the JSON array is the first thing in your response and parses
2. If no issues found, return an empty array `[]` — never omit the return value
3. If you wrote the optional `.claude/audits/AUDIT_MY.md` trail, verify it has content beyond
   headers, writing "No issues detected" rather than leaving it empty
```

### 2. Register in the orchestrator

Edit `.claude/commands/audit-fix.md`, Step 1.

A **generic** auditor (always applicable) gets a row in the "Generic auditors" table:

```
| N | My Auditor | `my-auditor` | `MY-` | [scope — what it exclusively checks]. *(STANDARD/DEEP)* |
```

A **conditional** domain auditor gets its own block under "Domain & infra auditors (conditional)":

```
#### Agent N — My Domain (run if RUN_MY)
\```
subagent_type: my-auditor   |   Prefix: MY-
Scope: [description]
       - [check]
       - [check]
Output: MY- findings per schema, high_stakes=false.
\```
```

### 3. Update scope boundaries

Add your auditor's scope to `docs/auditor-scopes.md` and ensure no overlap with existing auditors.

> For a **conditional** domain auditor, also add a detection flag in Step 0.5 of `audit-fix.md`
> (e.g. a `*_PATHS` list), gate the agent's launch on that flag in the Step 1 tier table, and decide
> which tiers run it. Auditors should return findings as the canonical JSON schema (see the Findings
> Schema section of `audit-fix.md`) — that return value is what the orchestrator consumes.

## Adding a Language Backend

Auditors are language-agnostic prose. The **deterministic** layer is not: a checker's silence is
what licenses a `FALSE_POSITIVE`, so it may only ever be pointed at a language it can actually read.
`cca_checks/languages/` enforces that structurally.

### 1. Write the backend

Create `cca_checks/languages/<lang>.py` satisfying the `LanguageBackend` protocol in
`languages/base.py`:

```python
class GoBackend:
    name = "go"
    extensions = frozenset({".go"})       # lower-case, dot-prefixed
    claim_types = frozenset({"clock_leak", "taint"})

    def enclosing_span(self, path, line_1based): ...   # 1-indexed, inclusive
    def settle(self, claim): ...                       # -> Verdict

    # Optional:
    def semgrep_catalog(self, kind): return f"go_{kind}.yaml"
    def unavailable_claim_types(self): return {}       # tool missing HERE, with reason
```

`claim_types` is a **positive list**. A claim type is unsupported by your backend until you opt in,
which is the safe default — a backend that forgot one escalates rather than routing the claim to a
checker built for another language.

### 2. Register it

Add it to `BACKENDS` in `cca_checks/languages/__init__.py`. That is the only place. The CLI's
`--claim-type` choices, the `capabilities` output, and the catalog tests are all derived from the
registry; a hand-maintained list beside it is how the two drift.

### 3. Choose the claim vocabulary for that language, not Python's

**Do not port the six Python claim types by reflex.** Rust deliberately has no `definedness`,
`type` or `nullability`: the code compiled, so those refute by construction and would double the
vocabulary while settling nothing new. Ask instead what a verdict can carry information about in
*this* language, and name the claim types after that.

### 4. Decide what may CONFIRM, and what may only REFUTE

The asymmetry is per claim type, and it follows one rule: **does the tool see the defect, or a
possibility?** A clippy `let_underscore_must_use` fires on the defect itself, so `error_swallow` may
confirm. A `clippy::unwrap_used` fires on a construct that may or may not be reachable, so
`panic_path` may only refute — mirroring `taint`, where semgrep flags safely-parameterized calls.
Confirmations for those come from a repro that actually reproduces.

### 5. State what your tool cannot see, and make it block refutation

Every backend has blind spots. They are handled the same way: the conditions under which the tool
could be hiding something must **escalate**, never pass silently. The Rust backend blocks
`FALSE_POSITIVE` on a glob `use` from a time crate and on any macro invocation in scope, because
tree-sitter cannot expand macros. If your tool needs the project to build, an un-buildable project
is `UNCERTAIN` everywhere.

If the audited project controls a suppression mechanism your tool honours, **disable it** —
`--force-warn` for clippy, `enableTypeIgnoreComments: false` for pyright, `--disable-nosem` for
semgrep. The auditor must control the configuration its refutations rest on; the audited repo must
not.

### 6. Fixtures, and CI

Fixture line numbers are the test contract. Disable the language's formatter in the fixture
directory (`rustfmt.toml`'s `disable_all_formatting`, mirroring ruff's `extend-exclude`), and pin
the coordinates in `tests/test_fixture_contract.py`.

If your tests gate on an external binary via `shutil.which(...)`, **ci.yml must install it**.
`tests/test_ci_contract.py` asserts that invariant: a test that skips on every CI run is worse than
no test, because it looks like coverage.

Then add the honest limits to `README.md` — and, per this repo's convention, a test for each.

## Design Principles

1. **Non-overlapping scopes**: Every check belongs to exactly one auditor
2. **Status blocks**: Every auditor output starts with a structured status block
3. **Finding IDs**: Unique prefix per auditor (CODE-, BUG-, SEC-, etc.)
4. **Severity levels**: Critical, High, Medium, Low
5. **Structured return**: Every auditor returns its findings as the canonical JSON array — that
   return value is authoritative; the `.claude/audits/*.md` trail is optional
6. **Language agnostic**: Checks adapt to detected languages

## Examples of Custom Auditors

| Auditor | Domain | Checks |
|---------|--------|--------|
| API Schema | REST/GraphQL | Missing validation, undocumented endpoints, breaking changes |
| Database | SQL/ORM | Missing indexes, schema drift, migration safety |
| Accessibility | Web UI | ARIA labels, color contrast, keyboard navigation |
| i18n | Internationalization | Hardcoded strings, missing translations, locale issues |
| Compliance | Regulatory | GDPR data handling, audit logging, retention policies |
