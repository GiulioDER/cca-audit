#!/usr/bin/env bash
set -euo pipefail

# CCA-Audit installer for Claude Code
# Copies agent and command files to .claude/ in the current project

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Target directories
AGENTS_DIR=".claude/agents"
COMMANDS_DIR=".claude/commands"

mkdir -p "$AGENTS_DIR" "$COMMANDS_DIR"

# Copy agents
for agent in "$SCRIPT_DIR/agents/"cca-*.md; do
  cp "$agent" "$AGENTS_DIR/$(basename "$agent")"
  echo "  Installed $(basename "$agent") -> $AGENTS_DIR/"
done

# Copy orchestrator command
cp "$SCRIPT_DIR/commands/audit-fix.md" "$COMMANDS_DIR/audit-fix.md"
echo "  Installed audit-fix.md -> $COMMANDS_DIR/"

echo ""
echo "CCA-Audit installed. Run /audit-fix in Claude Code to start."
