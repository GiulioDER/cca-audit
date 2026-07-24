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
#
# The agent/command markdown lives under cca_checks/plugin/ rather than beside
# this script: a wheel can only carry package data, so that is the only
# location from which BOTH `pip install cca-audit` and this script can serve
# the same files. One copy on disk, so the two install paths cannot drift.
ASSETS_REL="cca_checks/plugin"
if [[ -n "$SOURCE_DIR" && -d "$SOURCE_DIR/../$ASSETS_REL/agents" ]]; then
  # Local mode: run from a checkout.
  SRC_DIR="$(cd "$SOURCE_DIR/../$ASSETS_REL" && pwd)"
else
  # Standalone mode: fetch the repo into a temp dir.
  command -v git >/dev/null 2>&1 || { echo "Error: git is required for the curl|bash install." >&2; exit 1; }
  CLEANUP_DIR="$(mktemp -d)"
  echo "Fetching CCA-Audit ($REPO_REF)..."
  # Let stderr through. Suppressing it meant a network, proxy or auth failure
  # exited non-zero with no explanation, and the only symptom the user saw was the
  # generic "no agent files found" further down.
  if ! git clone --depth 1 --branch "$REPO_REF" "$REPO_URL" "$CLEANUP_DIR/repo" >/dev/null; then
    echo "Error: could not clone $REPO_URL (network/proxy/auth?)." >&2
    exit 1
  fi
  SRC_DIR="$CLEANUP_DIR/repo/$ASSETS_REL"
fi

AGENTS_DIR=".claude/agents"
COMMANDS_DIR=".claude/commands"
mkdir -p "$AGENTS_DIR" "$COMMANDS_DIR"

BACKED_UP=0

# Copy one file, preserving any local customization as <name>.bak.
#
# claude-code/README.md tells users to CONFIGURE this tool by editing the very
# files the installer writes -- the *_PATHS lists, the CUSTOMIZE: blocks, the FAST
# thresholds. The configuration surface and the install surface are the same
# files, so overwriting unconditionally makes upgrade == silent config loss.
install_file() {
  local src="$1" dest_dir="$2" name dest
  name="$(basename "$src")"
  dest="$dest_dir/$name"
  if [[ -f "$dest" ]] && ! cmp -s "$src" "$dest"; then
    cp "$dest" "$dest.bak"
    BACKED_UP=$((BACKED_UP + 1))
    echo "  Updated   $name -> $dest_dir/ (previous version kept as $name.bak)"
  else
    echo "  Installed $name -> $dest_dir/"
  fi
  cp "$src" "$dest"
}

# Copy agents
agents=("$SRC_DIR/agents/"cca-*.md)
[[ ${#agents[@]} -gt 0 ]] || { echo "Error: no agent files found in $SRC_DIR/agents/" >&2; exit 1; }
for agent in "${agents[@]}"; do
  install_file "$agent" "$AGENTS_DIR"
done

# Warn about pre-existing agents declaring a name we dispatch. Our files are named
# cca-*.md but their frontmatter `name:` is generic (code-auditor, security-auditor,
# ...), so a project that already defines one of those names has a collision the
# cca-*.md glob above cannot see.
for existing in "$AGENTS_DIR"/*.md; do
  base="$(basename "$existing")"
  [[ "$base" == cca-* ]] && continue
  if grep -qE '^name:[[:space:]]*((code|bug|security|perf|doc|numeric|dep|deploy)-auditor|env-validator|fp-check|fix-planner|differential-review|architect-reviewer)[[:space:]]*$' "$existing" 2>/dev/null; then
    echo "  WARNING: $base declares an agent name CCA-Audit also dispatches; one will shadow the other." >&2
  fi
done

# Copy orchestrator commands (canonical + DEEP alias)
commands=("$SRC_DIR/commands/"audit-fix*.md)
[[ ${#commands[@]} -gt 0 ]] || { echo "Error: no command files found in $SRC_DIR/commands/" >&2; exit 1; }
for cmd in "${commands[@]}"; do
  install_file "$cmd" "$COMMANDS_DIR"
done

# Install the cca_checks package so the deterministic verifier works.
#
# Interpreter choice matters: every agent prompt invokes bare `python`
# (`python -m cca_checks ...`). Installing into python3 on a box where `python`
# resolves elsewhere -- or nowhere -- produces a "successful" install whose
# deterministic layer never runs, degrading silently to LLM-only verification.
# So prefer `python`, and report which interpreter was used.
# SRC_DIR is <root>/cca_checks/plugin, so the project root is two levels up.
# This must track ASSETS_REL: a single `dirname` here would point pip at
# cca_checks/, which has no pyproject.toml, and the install would be skipped
# with the misleading "python/pip not found" note below.
REPO_ROOT="$(cd "$SRC_DIR/../.." && pwd)"
PY="$(command -v python || command -v python3 || true)"
if [[ -n "$PY" && -f "$REPO_ROOT/pyproject.toml" ]]; then
  echo "Installing cca_checks (deterministic verification helpers)..."
  PIP_LOG="$(mktemp)"
  # `--user` is refused outright inside a virtualenv ("User site-packages are not
  # visible in this virtualenv") and under PEP 668 ("externally-managed
  # environment") -- both the common case for a Python project being audited. Try
  # the plain install first and fall back to --user, not the reverse. The
  # `[numeric]` extra is installed by default because the numeric auditor ships
  # unconditionally; without it every numeric claim escalates to UNCERTAIN and,
  # on DEEP, cannot be fixed at all.
  # Third fallback: the published package. A local path is preferred so a
  # checkout installs the code you are looking at, but pip cannot always parse
  # one -- under Git Bash on Windows $REPO_ROOT is an MSYS path (/c/Users/...)
  # and pip rejects it as a requirement, which silently downgraded the whole
  # install to LLM-only verification. Now that cca-audit is on PyPI there is a
  # source that always parses.
  if "$PY" -m pip install --quiet "$REPO_ROOT[numeric]" >"$PIP_LOG" 2>&1 \
     || "$PY" -m pip install --user --quiet "$REPO_ROOT[numeric]" >"$PIP_LOG" 2>&1 \
     || "$PY" -m pip install --quiet "cca-audit[numeric]" >"$PIP_LOG" 2>&1 \
     || "$PY" -m pip install --user --quiet "cca-audit[numeric]" >"$PIP_LOG" 2>&1; then
    echo "  Installed cca_checks[numeric] -> $PY -m cca_checks"
    if ! "$PY" -m cca_checks --help >/dev/null 2>&1; then
      echo "  WARNING: '$PY -m cca_checks' does not run; the deterministic layer will not be used." >&2
    elif command -v python >/dev/null 2>&1 && ! python -m cca_checks --help >/dev/null 2>&1; then
      # The agents call bare `python`. If that is a different interpreter, say so.
      echo "  WARNING: cca_checks is not importable from bare 'python', which is what the agents invoke." >&2
      echo "           Install it there too:  python -m pip install '$REPO_ROOT[numeric]'" >&2
    fi
  else
    echo "  NOTE: cca_checks install failed; /audit-fix falls back to LLM-only verification."
    echo "  ---- pip output (last 10 lines) ----" >&2
    tail -n 10 "$PIP_LOG" >&2
  fi
  rm -f "$PIP_LOG"
else
  echo "  NOTE: python/pip not found; skipping cca_checks. /audit-fix falls back to LLM-only verification."
fi
echo "  For deterministic checks, also install: pyright, pytest, semgrep (on PATH)."

echo ""
if [[ "$BACKED_UP" -gt 0 ]]; then
  echo "$BACKED_UP customized file(s) were updated; the previous versions are saved as *.bak."
fi
echo "CCA-Audit installed. Run /audit-fix in Claude Code to start."
