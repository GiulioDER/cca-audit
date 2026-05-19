# CCA-Audit for Codex CLI

Shell orchestrator that runs the CCA-Audit 6-layer pipeline using [OpenAI Codex CLI](https://github.com/openai/codex).

## Prerequisites

- [Codex CLI](https://github.com/openai/codex) installed and authenticated
- bash 4.0+
- git

## Installation

```bash
cd your-project
bash /path/to/cca-audit/codex/install.sh
```

This copies:
- `cca-audit.sh` -- the orchestrator script
- `.cca-audit/agents/` -- 10 auditor prompt files

## Usage

```bash
bash cca-audit.sh                          # Full pipeline (audit + fix)
bash cca-audit.sh --no-fix                 # Audit only, no fixes
bash cca-audit.sh --p1-only               # Fix only P1 Critical findings
bash cca-audit.sh --auditors security,bug  # Run specific auditors only
```

## How It Works

1. Detects changed files via `git diff`
2. Auto-detects languages from file extensions
3. Launches auditors in parallel as background jobs (`codex --prompt`)
4. Waits for all to complete, then consolidates findings
5. Implements fixes via Codex
6. Re-runs tests and linter
7. Runs architect-reviewer for final verdict

## Tool Mapping

The agent prompts use Codex tool names:

| Claude Code | Codex CLI |
|-------------|-----------|
| Read | read_file |
| Write | write_file |
| Edit | apply_diff |
| Bash | shell |
| Grep | search |
| Glob | list_files |

## Output

Audit reports are written to `.claude/audits/`:
- `AUDIT_CODE.md`, `AUDIT_BUGS.md`, `AUDIT_SECURITY.md`, etc.
- `FIXES.md` -- consolidated fix plan

## Customization

Edit the agent files in `.cca-audit/agents/` to adjust checks, scope boundaries, or add project-specific context.
