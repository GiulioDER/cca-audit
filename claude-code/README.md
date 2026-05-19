# CCA-Audit for Claude Code

Drop-in agents for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that run a 6-layer parallel audit+fix pipeline on your codebase.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed and authenticated

## Installation

### Unix/macOS

```bash
cd your-project
bash /path/to/cca-audit/claude-code/install.sh
```

### Windows (PowerShell)

```powershell
cd your-project
& \path\to\cca-audit\claude-code\install.ps1
```

This copies the agent and command files into your project's `.claude/` directory:
- `.claude/commands/audit-fix.md` -- the orchestrator
- `.claude/agents/cca-*.md` -- 10 specialized agents

## Usage

Inside Claude Code, use the `/audit-fix` slash command:

```
/audit-fix                    # Audit + fix all uncommitted changes
/audit-fix no-fix             # Audit only, report findings without fixing
/audit-fix p1-only            # Fix only P1 Critical findings
/audit-fix commit 1           # Audit the last commit
/audit-fix commit 3           # Audit the last 3 commits
/audit-fix files src/app.py   # Audit specific files
```

## What Happens

1. **Step 0**: Detects changed files from git
2. **Step 0.5**: Auto-detects languages and test/lint tools
3. **Step 1**: Launches 6 auditors in parallel (code, bug, security, perf, docs, env)
4. **Step 2**: Consolidates and deduplicates findings across auditors
5. **Step 3**: Creates a prioritized fix plan (P1 > P2 > P3)
6. **Step 4**: Implements P1 and P2 fixes
7. **Step 5**: Re-runs tests and linter to verify fixes
8. **Step 6**: Architect-reviewer gives final verdict (APPROVED / REVISE / BLOCKED)
9. **Step 7**: Commits the fixes

## Output

Audit reports are written to `.claude/audits/`:
- `AUDIT_CODE.md`, `AUDIT_BUGS.md`, `AUDIT_SECURITY.md`, `AUDIT_PERF.md`, `AUDIT_DOCS.md`, `AUDIT_ENV.md`, `AUDIT_DEPS.md`
- `FIXES.md` -- consolidated fix plan
- `EXECUTION_LOG.md` -- run history

## Customization

### Adding project context

Edit the prompt template in `audit-fix.md` (Step 1) to add project-specific context. For example, if your project handles financial transactions:

```
Context: This system processes real financial transactions.
Prioritize data integrity and input validation.
```

### Disabling auditors

Comment out any agent launch in Step 1 of `audit-fix.md` to skip it. The pipeline adapts automatically.

### Adding custom auditors

See [docs/extending.md](../docs/extending.md) for the full guide.

## Supported Languages

Auto-detected from file extensions:

| Language | Test Runner | Linter |
|----------|-------------|--------|
| Python | pytest | ruff, flake8 |
| TypeScript/JS | jest, vitest, npm test | eslint, biome |
| Go | go test | golangci-lint |
| Rust | cargo test | clippy |
| Java | mvn test, gradle test | checkstyle |
| Ruby | rspec, minitest | rubocop |
