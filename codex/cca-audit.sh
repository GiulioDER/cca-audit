#!/usr/bin/env bash
set -euo pipefail

# CCA-Audit orchestrator for OpenAI Codex CLI
# Runs 6 parallel auditors, consolidates findings, optionally fixes and reviews.

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
AGENTS_DIR="$SCRIPT_DIR/agents"
AUDIT_DIR=".claude/audits"
NO_FIX=false
P1_ONLY=false
DEFERRED=false
AUDITORS_FILTER=""

usage() {
  cat <<EOF
Usage: $(basename "$0") [OPTIONS]

Options:
  --no-fix              Audit only, do not implement fixes
  --p1-only             Fix only P1 Critical findings
  --deferred            Second pass: fix P3 items deferred from previous round
  --auditors LIST       Comma-separated auditor names (e.g., security,bug,code)
  --help                Show this help

Examples:
  $(basename "$0")                          # Full pipeline (Round 1: P1+P2)
  $(basename "$0") --deferred              # Second pass (Round 2: fix deferred P3)
  $(basename "$0") --no-fix                 # Audit only
  $(basename "$0") --auditors security,bug  # Specific auditors only
  $(basename "$0") --p1-only               # Fix only critical findings
EOF
  exit 0
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --no-fix) NO_FIX=true; shift ;;
    --p1-only) P1_ONLY=true; shift ;;
    --deferred) DEFERRED=true; shift ;;
    --auditors) AUDITORS_FILTER="$2"; shift 2 ;;
    --help) usage ;;
    *) echo "Unknown option: $1"; usage ;;
  esac
done

# Second Pass: fix deferred P3 items from previous round
if [[ "$DEFERRED" == true ]]; then
  echo "=== Second Pass: Fixing deferred P3 items ==="
  LAST_CCA_MSG=$(git log -5 --format=%B --grep="audit fixes from.*CCA review" | head -60)
  if [[ -z "$LAST_CCA_MSG" ]]; then
    echo "No deferred items found. Run a full audit first."
    exit 0
  fi

  DEFERRED_PROMPT="You are fixing deferred P3 items from a previous CCA audit.

Here is the commit message from the last CCA audit round:
$LAST_CCA_MSG

Extract the P3/Deferred items. For each one:
1. Read the target file and check if the issue still exists
2. If still relevant: fix it (minimal diff)
3. If code moved/deleted: mark STALE and skip

After fixing, run tests and lint to verify nothing broke."

  codex --prompt "$DEFERRED_PROMPT" 2>/dev/null || echo "Deferred fix failed"

  echo ""
  echo "=== Second Pass Complete ==="
  exit 0
fi

# Step 0: Detect changed files
echo "=== Step 0: Detecting changed files ==="
FILES=$(git diff --name-only --diff-filter=ACMR HEAD 2>/dev/null || true)
if [[ -z "$FILES" ]]; then
  FILES=$(git diff --name-only HEAD~1 2>/dev/null || true)
  DIFF_CMD="git diff HEAD~1"
else
  DIFF_CMD="git diff HEAD"
fi

if [[ -z "$FILES" ]]; then
  echo "No changed files to audit."
  exit 0
fi

FILE_COUNT=$(echo "$FILES" | wc -l | tr -d ' ')
echo "Auditing $FILE_COUNT files:"
echo "$FILES" | head -20
[[ $FILE_COUNT -gt 20 ]] && echo "... and $((FILE_COUNT - 20)) more"

# Step 0.5: Language detection
echo ""
echo "=== Step 0.5: Language detection ==="
LANGS=""
echo "$FILES" | grep -q '\.py$' && LANGS="$LANGS Python"
echo "$FILES" | grep -qE '\.(ts|tsx|js|jsx)$' && LANGS="$LANGS TypeScript/JavaScript"
echo "$FILES" | grep -q '\.go$' && LANGS="$LANGS Go"
echo "$FILES" | grep -q '\.rs$' && LANGS="$LANGS Rust"
echo "$FILES" | grep -q '\.java$' && LANGS="$LANGS Java"
echo "$FILES" | grep -q '\.rb$' && LANGS="$LANGS Ruby"
LANGS="${LANGS:- Unknown}"
echo "Detected languages:$LANGS"

mkdir -p "$AUDIT_DIR"

# Build the shared context for all auditors
CONTEXT="Files changed:\n$FILES\n\nLanguages detected:$LANGS\nDiff command: $DIFF_CMD\nWorking directory: $(pwd)"

# Step 1: Launch 6 parallel auditors
echo ""
echo "=== Step 1: Launching parallel auditors ==="

AUDITORS=("code" "bug" "security" "perf" "doc" "env" "dep")
PIDS=()

should_run_auditor() {
  local name="$1"
  if [[ -z "$AUDITORS_FILTER" ]]; then
    return 0
  fi
  echo ",$AUDITORS_FILTER," | grep -qi ",$name,"
}

for auditor in "${AUDITORS[@]}"; do
  if ! should_run_auditor "$auditor"; then
    echo "  Skipping $auditor (filtered)"
    continue
  fi

  AGENT_FILE="$AGENTS_DIR/cca-${auditor}-auditor.md"
  [[ "$auditor" == "env" ]] && AGENT_FILE="$AGENTS_DIR/cca-env-validator.md"
  [[ "$auditor" == "dep" ]] && AGENT_FILE="$AGENTS_DIR/cca-dep-auditor.md"

  if [[ ! -f "$AGENT_FILE" ]]; then
    echo "  Warning: $AGENT_FILE not found, skipping"
    continue
  fi

  OUTPUT_FILE="$AUDIT_DIR/AUDIT_$(echo "$auditor" | tr '[:lower:]' '[:upper:]').md"

  echo "  Launching $auditor auditor..."
  (
    PROMPT="$(cat "$AGENT_FILE")

---

Audit context:
$(echo -e "$CONTEXT")

Focus ONLY on new/changed code. Use '$DIFF_CMD' to see the changes.
Write your findings report. Be specific with file:line references."

    codex --prompt "$PROMPT" --output-file "$OUTPUT_FILE" 2>/dev/null || \
      echo "# $auditor Auditor\n\nError: codex command failed" > "$OUTPUT_FILE"
  ) &
  PIDS+=($!)
done

echo "  Waiting for ${#PIDS[@]} auditors to complete..."
FAILED=0
for pid in "${PIDS[@]}"; do
  wait "$pid" || FAILED=$((FAILED + 1))
done
echo "  All auditors complete ($FAILED failures)"

# Step 2: Consolidate
echo ""
echo "=== Step 2: Consolidating findings ==="
CONSOLIDATE_PROMPT="Read all audit reports in $AUDIT_DIR/AUDIT_*.md.
Deduplicate findings: same file:line = merge, keep highest severity.
Priority: P1 Critical (security, data corruption) > P2 High (DRY, config) > P3 Nice-to-have.
Output a consolidated FIXES.md to $AUDIT_DIR/FIXES.md."

codex --prompt "$CONSOLIDATE_PROMPT" 2>/dev/null || echo "Consolidation failed"

if [[ "$NO_FIX" == true ]]; then
  echo ""
  echo "=== Audit complete (no-fix mode) ==="
  echo "Reports in $AUDIT_DIR/"
  ls -la "$AUDIT_DIR/"
  exit 0
fi

# Steps 3-4: Fix
echo ""
echo "=== Steps 3-4: Implementing fixes ==="
FIX_SCOPE="P1 and P2"
[[ "$P1_ONLY" == true ]] && FIX_SCOPE="P1 only"

FIX_PROMPT="Read $AUDIT_DIR/FIXES.md. Implement $FIX_SCOPE fixes.
Rules: minimal diffs, fix only what the audit found, do not refactor unrelated code.
After each fix, mark it done in FIXES.md."

codex --prompt "$FIX_PROMPT" 2>/dev/null || echo "Fix implementation failed"

# Step 5: Re-verify
echo ""
echo "=== Step 5: Re-verification ==="
# Detect and run test/lint commands
[[ -f "pytest.ini" || -f "pyproject.toml" ]] && pytest 2>/dev/null && echo "  pytest: PASS" || true
[[ -f "package.json" ]] && npm test 2>/dev/null && echo "  npm test: PASS" || true
[[ -f "go.mod" ]] && go test ./... 2>/dev/null && echo "  go test: PASS" || true
[[ -f "Cargo.toml" ]] && cargo test 2>/dev/null && echo "  cargo test: PASS" || true

# Step 6: Architect review
echo ""
echo "=== Step 6: Architect review ==="
REVIEW_PROMPT="$(cat "$AGENTS_DIR/cca-architect-reviewer.md")

---

Review the full diff of changes. Read $AUDIT_DIR/FIXES.md for context.
Assess: Completeness, Quality, Correctness, Security.
Verdict: APPROVED / REVISE / BLOCKED."

codex --prompt "$REVIEW_PROMPT" 2>/dev/null || echo "Review failed"

echo ""
echo "=== CCA Audit+Fix Complete ==="
echo "Reports in $AUDIT_DIR/"
