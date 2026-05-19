#!/usr/bin/env bash
set -euo pipefail

# CCA-Audit installer for Codex CLI
# Copies the orchestrator script and agent prompts to the current project

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

cp "$SCRIPT_DIR/cca-audit.sh" ./cca-audit.sh
chmod +x ./cca-audit.sh
echo "  Installed cca-audit.sh"

mkdir -p .cca-audit/agents
for agent in "$SCRIPT_DIR/agents/"cca-*.md; do
  cp "$agent" ".cca-audit/agents/$(basename "$agent")"
  echo "  Installed $(basename "$agent")"
done

echo ""
echo "CCA-Audit installed. Run: bash cca-audit.sh"
