# Extending CCA-Audit

Add custom auditors to check domain-specific patterns.

## Claude Code / Codex: Adding an Agent File

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

## OpenRouter: Adding a Python Auditor

### 1. Create the prompt template

Create `cca_audit/prompts/my_auditor.j2`:

```jinja2
You are a [domain] auditor. Analyze the following {{ file_count }} files.

**Scope (NON-OVERLAPPING):**
- [Your exclusive checks]

**Does NOT check:**
- [What other auditors handle]

**Languages detected:** {{ languages }}

**Files changed:**
{% for f in files %}
{{ loop.index }}. {{ f }}
{% endfor %}

{% if project_context %}
**Project context:** {{ project_context }}
{% endif %}

**Diff to review:**
\```
{{ diff_content }}
\```

Report with IDs ({{ prefix }}-001, etc.), severity, file:line, and fix.
```

### 2. Create the auditor class

Create `cca_audit/auditors/my_auditor.py`:

```python
from cca_audit.auditors.base import BaseAuditor

class MyAuditor(BaseAuditor):
    name = "my_auditor"
    prefix = "MY"
    output_file = "AUDIT_MY.md"

    def template_name(self) -> str:
        return "my_auditor.j2"
```

### 3. Register in the auditor registry

Edit `cca_audit/auditors/__init__.py`:

```python
from cca_audit.auditors.my_auditor import MyAuditor

AUDITOR_REGISTRY: dict[str, type[BaseAuditor]] = {
    # ... existing auditors ...
    "my_auditor": MyAuditor,
}
```

### 4. Enable in config

Add to `cca-audit.yaml`:

```yaml
auditors:
  - code
  - bug
  - security
  - perf
  - doc
  - env
  - dep
  - my_auditor  # new
```

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
