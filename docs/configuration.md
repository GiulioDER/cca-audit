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

### Tiers and domain dispatch

`/audit-fix` is tiered (FAST / STANDARD / DEEP), auto-selected in Step 0.6. To tune it:
- **Tier thresholds** — edit Step 0.6 (the FAST size/file limits), or force a tier per run with the
  `fast` / `deep` argument.
- **Which diffs trigger the conditional auditors** — edit the `*_PATHS` lists
  (`HIGH_STAKES_PATHS` / `NUMERIC_PATHS` / `DATA_PATHS` / `DEP_PATHS` / `DEPLOY_PATHS`) in Step 0.5.
- **Project invariants** — add your hard rules to the High-Stakes / Data-Integrity / Deployability
  auditor scopes (look for `CUSTOMIZE:`), and replace the `{PROJECT_CONTEXT}` placeholder in the
  Step 1 prompt template with your project's description.

`/audit-fix-v2` is a backward-compatible alias that just forces the DEEP tier.

## Output Reports

### Output directory

Auditors return their findings as structured JSON, which the pipeline consumes as the **authoritative**
source. They may **optionally** also write a human-readable trail to `.claude/audits/` (the pipeline
does not depend on these files). Files that may be created:

| File | Content |
|------|---------|
| `AUDIT_CODE.md` | Code quality findings |
| `AUDIT_BUGS.md` | Runtime bug findings |
| `AUDIT_SECURITY.md` | Security findings |
| `AUDIT_PERF.md` | Performance findings |
| `AUDIT_DOCS.md` | Documentation findings |
| `AUDIT_ENV.md` | Environment config findings |
| `AUDIT_DEPLOY.md` | Deployability findings |
| `AUDIT_DEPS.md` | Dependency findings |
| `AUDIT_NUMERIC.md` | Numerical / units / sign findings |
| `AUDIT_FPCHECK.md` | Findings-verification verdicts (Step 2.5) |
| `AUDIT_DIFFREVIEW.md` | Differential review of the fix diff (Step 5.5) |
| `FIXES.md` | Consolidated fix plan |
| `EXECUTION_LOG.md` | Run history |
