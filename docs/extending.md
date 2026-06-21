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

Output to `.claude/audits/AUDIT_MY.md`.

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

## Output Format

Report findings grouped by severity: Critical > High > Medium > Low.
Each finding: `### MY-NNN: Title` with Severity, File:line, Issue, Fix.

## Execution Logging

After completing, append to `.claude/audits/EXECUTION_LOG.md`:
\```
| [timestamp] | my-auditor | [status] | [duration] | [findings] | [errors] |
\```

## Output Verification

Before completing:
1. Verify `.claude/audits/AUDIT_MY.md` was created
2. Verify file has content beyond headers
3. If no issues found, write "No issues detected" (not empty file)
```

### 2. Register in the orchestrator

Edit `.claude/commands/audit-fix.md`, Step 1. Add a new agent block:

```
### Agent N: My Auditor
\```
subagent_type: my-auditor
Scope: [description]
Focus: ONLY new/changed code.
Output: Finding IDs MY-001..N, severity, file:line, fix.
\```
```

### 3. Update scope boundaries

Add your auditor's scope to `docs/auditor-scopes.md` and ensure no overlap with existing auditors.

> For a v2-style **conditional** domain auditor, also add a detection flag in Step 0.5 of
> `audit-fix-v2.md` (e.g. a `*_PATHS` list) and gate the agent's launch on that flag in Step 1.

## Design Principles

1. **Non-overlapping scopes**: Every check belongs to exactly one auditor
2. **Status blocks**: Every auditor output starts with a structured status block
3. **Finding IDs**: Unique prefix per auditor (CODE-, BUG-, SEC-, etc.)
4. **Severity levels**: Critical, High, Medium, Low
5. **Output verification**: Every auditor verifies its output file was created
6. **Language agnostic**: Checks adapt to detected languages

## Examples of Custom Auditors

| Auditor | Domain | Checks |
|---------|--------|--------|
| API Schema | REST/GraphQL | Missing validation, undocumented endpoints, breaking changes |
| Database | SQL/ORM | Missing indexes, schema drift, migration safety |
| Accessibility | Web UI | ARIA labels, color contrast, keyboard navigation |
| i18n | Internationalization | Hardcoded strings, missing translations, locale issues |
| Compliance | Regulatory | GDPR data handling, audit logging, retention policies |
