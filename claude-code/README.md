# CCA-Audit for Claude Code

Drop-in agents for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) that run a **tiered** parallel audit+fix pipeline on your changed code.

## Prerequisites

- [Claude Code](https://docs.anthropic.com/en/docs/claude-code) CLI installed and authenticated
- `git` — both installers shallow-clone the repo when run as a one-liner
- `python` + `pip` — the installers pip-install the `cca_checks` helper package
- **For the deterministic verification layer**: `pyright`, `pytest`, and `semgrep` on your `PATH`
  (`pip install pyright pytest semgrep`). Without them `/audit-fix` gracefully falls back to
  LLM-only verification — no crash, no regression.
- **For numeric findings**: the `numeric` extra (`pip install "cca_checks[numeric]"`), which adds
  the `hypothesis`-backed `numeric` claim type.

## Installation

Run from the root of the project you want to audit. Both installers work either from a
local clone or piped straight from GitHub (they shallow-clone the repo to a temp dir).

### Unix/macOS

```bash
# One-liner (requires git)
curl -fsSL https://raw.githubusercontent.com/GiulioDER/cca-audit/master/claude-code/install.sh | bash

# ...or from a local clone
bash /path/to/cca-audit/claude-code/install.sh
```

### Windows (PowerShell)

```powershell
# One-liner (requires git)
irm https://raw.githubusercontent.com/GiulioDER/cca-audit/master/claude-code/install.ps1 | iex

# ...or from a local clone
& \path\to\cca-audit\claude-code\install.ps1
```

This copies the agent and command files into your project's `.claude/` directory:
- `.claude/commands/audit-fix.md` — the canonical tiered orchestrator
- `.claude/commands/audit-fix-v2.md` — backward-compatible alias that forces the DEEP tier
- `.claude/agents/cca-*.md` — the specialized agents

It also installs the **`cca_checks`** helper package (`python -m cca_checks`) that powers the
deterministic verification layer.

## Usage

One command, auto-tiered. The pipeline auto-selects **FAST / STANDARD / DEEP** by diff size and
risk — you only pass a tier to override it.

```
/audit-fix                    # Audit + fix all uncommitted changes (tier auto-selected)
/audit-fix no-fix             # Audit + verify only, report findings without fixing
/audit-fix p1-only            # Fix only P1 Critical findings
/audit-fix fast               # Force the cheap 3-auditor tier (no gates)
/audit-fix deep               # Force the full tier (all domain auditors + adversarial verify)
/audit-fix commit 1           # Audit the last commit
/audit-fix commit 3           # Audit the last 3 commits
/audit-fix files src/app.py   # Audit specific files
/audit-fix hunt src/          # HUNT MODE: audit code you did NOT write for pre-existing bugs
/audit-fix deferred           # Second pass for deferred P3 items
```

**Tiers:**

| Tier | When (auto) | Auditors | Verification gates |
|------|-------------|----------|--------------------|
| FAST | trivial, low-stakes, non-deploy diff | security, bug, code | — |
| STANDARD | normal diff | all 6 core + conditional domain/dep/deploy | L2.5 anti-hallucination, L5.5 anti-regression, L6 mapping |
| DEEP | high-stakes / numeric / forced | all of STANDARD | + adversarial 2-of-3 on high-stakes P1 |

High-stakes diffs (money, auth, data migrations, numeric-heavy code) always run **DEEP**.

> `/audit-fix-v2` is kept as a thin alias that forces the **DEEP** tier. The old v1 (6-auditor) /
> v2 (9-auditor) split has been merged into this one tiered pipeline.

## What Happens

1. **Step 0**: Detects changed files from git (tracked + untracked).
2. **Step 0.4** *(hunt mode only, blocking)*: Target-viability pre-flight — any REJECT stops the run
   before a single auditor is spawned.
3. **Step 0.5**: Auto-detects languages, test/lint tools, and which domains the diff touches.
4. **Step 0.6**: Selects the tier (FAST / STANDARD / DEEP).
5. **Step 1**: Launches the applicable auditors in parallel (core + conditional domain/dep/deploy).
6. **Step 2**: Consolidates and deterministically deduplicates findings (structured JSON return values).
7. **Step 2.5** *(STANDARD/DEEP)*: Re-verifies P1/P2 findings against the real code — drops false
   positives, escalates uncertain ones; high-stakes P1 get adversarial 2-of-3 verification.
8. **Step 3–4**: Prioritized fix plan; implements P1+P2 (each P1 gets a red→green regression test).
9. **Step 5**: Re-runs tests and linter.
10. **Step 5.5** *(STANDARD/DEEP)*: Differential review confirms the fix diff stayed in scope.
11. **Step 6**: Architect-reviewer (read-only) gives the final verdict + fix→finding mapping.
12. **Step 7**: Commits the fixes.

## Output

Auditors return their findings as structured JSON (the authoritative source the pipeline consumes).
They may **optionally** also write a human-readable trail to `.claude/audits/` (`AUDIT_*.md`,
`EXECUTION_LOG.md`); the pipeline does not depend on those files.

## Customization

### Adding project context

Replace the `{PROJECT_CONTEXT}` placeholder in the Step 1 prompt template of `audit-fix.md` with your
project's one-line description and risk note. For example:

```
Context: This system processes real financial transactions.
Prioritize data integrity, sign/units correctness, and input validation.
```

### Tuning domain dispatch

Edit the `*_PATHS` lists in Step 0.5 of `audit-fix.md` to control which diffs trigger the conditional
auditors (high-stakes, numeric, data, dependency, deployability), and add your project's hard
invariants to the High-Stakes / Data-Integrity / Deployability auditor scopes (look for `CUSTOMIZE:`).

### Tuning the tier thresholds

Step 0.6 picks the tier. Adjust the FAST size/file thresholds, or force a tier per run with the
`fast` / `deep` argument.

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
