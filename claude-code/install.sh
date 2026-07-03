#!/usr/bin/env bash
set -euo pipefail
shopt -s nullglob

# CCA-Audit installer for Claude Code
# Copies agent and command files to .claude/ in the current project.
#
# Works in two modes:
#   - Local:      run from a cloned repo (bash claude-code/install.sh)
#   - Standalone: piped from the web (curl -fsSL .../install.sh | bash) --
#                 it shallow-clones the repo to a temp dir first.

REPO_URL="https://github.com/GiulioDER/cca-audit.git"
REPO_REF="master"

# Resolve the directory this script lives in (empty/unreliable when piped via curl).
SOURCE_DIR=""
if [[ -n "${BASH_SOURCE[0]:-}" && -f "${BASH_SOURCE[0]}" ]]; then
  SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
fi

CLEANUP_DIR=""
cleanup() { [[ -n "$CLEANUP_DIR" ]] && rm -rf "$CLEANUP_DIR"; }
trap cleanup EXIT

# Decide where to copy the files from.
if [[ -n "$SOURCE_DIR" && -d "$SOURCE_DIR/agents" ]]; then
  # Local mode: run from a checkout.
  SRC_DIR="$SOURCE_DIR"
else
  # Standalone mode: fetch the repo into a temp dir.
  command -v git >/dev/null 2>&1 || { echo "Error: git is required for the curl|bash install." >&2; exit 1; }
  CLEANUP_DIR="$(mktemp -d)"
  echo "Fetching CCA-Audit ($REPO_REF)..."
  git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$CLEANUP_DIR/repo" >/dev/null 2>&1
  SRC_DIR="$CLEANUP_DIR/repo/claude-code"
fi

AGENTS_DIR=".claude/agents"
COMMANDS_DIR=".claude/commands"
mkdir -p "$AGENTS_DIR" "$COMMANDS_DIR"

# Copy agents
agents=("$SRC_DIR/agents/"cca-*.md)
[[ ${#agents[@]} -gt 0 ]] || { echo "Error: no agent files found in $SRC_DIR/agents/" >&2; exit 1; }
for agent in "${agents[@]}"; do
  cp "$agent" "$AGENTS_DIR/$(basename "$agent")"
  echo "  Installed $(basename "$agent") -> $AGENTS_DIR/"
done

# Copy orchestrator commands (canonical + DEEP alias)
commands=("$SRC_DIR/commands/"audit-fix*.md)
[[ ${#commands[@]} -gt 0 ]] || { echo "Error: no command files found in $SRC_DIR/commands/" >&2; exit 1; }
for cmd in "${commands[@]}"; do
  cp "$cmd" "$COMMANDS_DIR/$(basename "$cmd")"
  echo "  Installed $(basename "$cmd") -> $COMMANDS_DIR/"
done

echo ""
echo "CCA-Audit installed. Run /audit-fix in Claude Code to start."
