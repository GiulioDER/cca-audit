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

### v2-specific configuration

`/audit-fix-v2` adds 3 conditional domain auditors. Tune which diffs trigger them by editing the
`HIGH_STAKES_PATHS` / `NUMERIC_PATHS` / `DATA_PATHS` lists in Step 0.5 of `audit-fix-v2.md`, and
add your project's hard invariants to the High-Stakes and Data-Integrity auditor scopes (Step 1).
Replace the `{PROJECT_CONTEXT}` placeholder in the prompt template with your project's description.

## Output Reports

### Output directory

Both pipelines write reports to `.claude/audits/` by default. Files created:

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
