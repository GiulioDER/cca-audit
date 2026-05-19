# CCA-Audit for OpenRouter API

Standalone Python CLI that runs the CCA-Audit 6-layer pipeline using any LLM via [OpenRouter](https://openrouter.ai/).

## Prerequisites

- Python 3.10+
- An [OpenRouter API key](https://openrouter.ai/keys)
- git (for detecting changed files)

## Installation

```bash
pip install cca-audit
```

Or from source:

```bash
git clone https://github.com/GiulioDER/cca-audit.git
cd cca-audit/openrouter
pip install -e .
```

## Quick Start

```bash
# Set your API key
export OPENROUTER_API_KEY=your-key-here

# Run on uncommitted changes
cca-audit

# Audit only (no fix suggestions)
cca-audit --no-fix

# Audit last 3 commits
cca-audit --commit 3

# Specific files
cca-audit --files src/app.py --files src/utils.py

# Choose a different model
cca-audit --model openai/gpt-4o

# JSON output
cca-audit --format json

# Run only specific auditors
cca-audit --auditors security,bug,code

# Dry run (show what would be audited)
cca-audit --dry-run
```

## Configuration

Create `cca-audit.yaml` in your project root (see `cca-audit.example.yaml`):

```yaml
model: anthropic/claude-sonnet-4-20250514
max_tokens: 8192
temperature: 0.0
auditors: [code, bug, security, perf, doc, env, dep]
output_dir: .claude/audits
output_format: markdown
project_context: "Optional project-specific context for auditors"
```

Environment variables override config file values:
- `OPENROUTER_API_KEY` -- API key (required)
- `CCA_MODEL` -- model override

## How It Works

1. Detects changed files via `git diff`
2. Auto-detects languages from file extensions
3. Sends 7 parallel API requests (one per auditor) to OpenRouter
4. Parses and deduplicates findings across auditors
5. Generates a prioritized fix plan (FIXES.md or FIXES.json)
6. Runs architect-reviewer for final verdict

**Note:** The OpenRouter variant cannot auto-fix code (it doesn't have file access). Use the Claude Code or Codex variant for auto-fix. This variant excels at audit reporting and CI integration.

## Output

Reports are written to `.claude/audits/` (configurable):
- `AUDIT_CODE.md`, `AUDIT_BUGS.md`, `AUDIT_SECURITY.md`, etc.
- `FIXES.md` (or `FIXES.json`) -- consolidated fix plan
- `REVIEW.md` -- architect-reviewer verdict

## Supported Models

Any model available on OpenRouter. Recommended:

| Model | Speed | Quality | Cost |
|-------|-------|---------|------|
| `anthropic/claude-sonnet-4-20250514` | Fast | High | $$ |
| `anthropic/claude-opus-4-20250514` | Slow | Highest | $$$$ |
| `openai/gpt-4o` | Fast | High | $$ |
| `google/gemini-2.5-pro` | Fast | High | $ |
| `meta-llama/llama-4-maverick` | Fast | Good | $ |

## Development

```bash
pip install -e ".[dev]"
pytest
ruff check .
```

## CI Integration

```yaml
# .github/workflows/audit.yml
name: CCA Audit
on: [pull_request]
jobs:
  audit:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install cca-audit
      - run: cca-audit --no-fix --format json > audit-results.json
        env:
          OPENROUTER_API_KEY: ${{ secrets.OPENROUTER_API_KEY }}
      - uses: actions/upload-artifact@v4
        with:
          name: audit-report
          path: .claude/audits/
```
