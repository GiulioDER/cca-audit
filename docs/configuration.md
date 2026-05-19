# Configuration

## Claude Code Variant

Configuration is done by editing the agent files directly in `.claude/agents/` and `.claude/commands/`.

### Adding project context

Edit `audit-fix.md`, Step 1 prompt template:
```
Context: This is a [your project type]. Prioritize [your priorities].
```

### Disabling auditors

Comment out any agent launch in Step 1 of `audit-fix.md`.

### Changing priority criteria

Edit Step 2 in `audit-fix.md`:
```
P1 Critical: [your criteria]
P2 High: [your criteria]
P3 Nice-to-have: [your criteria]
```

## Codex Variant

### CLI options

```bash
bash cca-audit.sh [OPTIONS]

--no-fix              Audit only
--p1-only             Fix only P1
--auditors LIST       Comma-separated (e.g., security,bug)
```

### Agent customization

Edit files in `.cca-audit/agents/` to adjust checks or add context.

## OpenRouter Variant

### Config file

Create `cca-audit.yaml` in your project root:

```yaml
# API key (or use OPENROUTER_API_KEY env var)
api_key: sk-or-...

# Model (see https://openrouter.ai/models)
model: anthropic/claude-sonnet-4-20250514

# Max tokens per auditor response
max_tokens: 8192

# Temperature (0.0 = deterministic)
temperature: 0.0

# Auditors to run
auditors:
  - code
  - bug
  - security
  - perf
  - doc
  - env
  - dep

# Output directory
output_dir: .claude/audits

# Output format: markdown or json
output_format: markdown

# Max review iterations
max_revise_iterations: 3

# Project context injected into all prompts
project_context: ""
```

### CLI options

```bash
cca-audit [OPTIONS]

-m, --model TEXT      LLM model override
-c, --config PATH     Config file path
-n, --commit INT      Audit last N commits
-f, --files PATH      Audit specific files (repeatable)
-a, --auditors TEXT   Comma-separated auditor names
--format [markdown|json]  Output format
--no-fix              Audit only
--p1-only             Fix only P1
--dry-run             Show what would be audited
```

### Environment variables

| Variable | Description |
|----------|-------------|
| `OPENROUTER_API_KEY` | API key (required) |
| `CCA_MODEL` | Model override |

### Config file search order

1. Path given via `--config`
2. `cca-audit.yaml`
3. `cca-audit.yml`
4. `.cca-audit.yaml`

## Common Configuration Across Variants

### Output directory

All variants write reports to `.claude/audits/` by default. Files created:

| File | Content |
|------|---------|
| `AUDIT_CODE.md` | Code quality findings |
| `AUDIT_BUGS.md` | Runtime bug findings |
| `AUDIT_SECURITY.md` | Security findings |
| `AUDIT_PERF.md` | Performance findings |
| `AUDIT_DOCS.md` | Documentation findings |
| `AUDIT_ENV.md` | Environment config findings |
| `AUDIT_DEPS.md` | Dependency findings |
| `FIXES.md` | Consolidated fix plan |
| `REVIEW.md` | Architect-reviewer verdict |
| `EXECUTION_LOG.md` | Run history |
