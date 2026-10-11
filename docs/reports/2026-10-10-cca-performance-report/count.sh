#!/usr/bin/env bash
# Counts system-prompt bytes per tier from origin/master agent files.
cd /c/Users/gde00/Documents/cca-audit || exit 1
sz() { git show "origin/master:cca_checks/plugin/agents/cca-$1.md" | wc -c; }
code=$(sz code-auditor); bug=$(sz bug-auditor); sec=$(sz security-auditor); perf=$(sz perf-auditor)
doc=$(sz doc-auditor); env=$(sz env-validator); num=$(sz numeric-auditor); dep=$(sz dep-auditor)
dpl=$(sz deploy-auditor); fp=$(sz fp-check); dr=$(sz differential-review); ar=$(sz architect-reviewer)
pipe=$(git show origin/master:cca_checks/plugin/commands/audit-fix.md | wc -c)
gen=$((code+bug+sec+perf+doc+env))
fast=$((code+bug+sec+fp+ar))
std=$((gen+fp+dr+ar))
std_max=$((std+bug+dep+dpl))
deep0=$((gen+bug+num+fp+dr+ar))
deep_max_k2=$((gen+bug+num+bug+dep+dpl+fp*7+dr+ar))
echo "pipeline file bytes: $pipe"
echo "generic six auditors: $gen"
echo "FAST (3 auditors + fp-check + architect): $fast"
echo "STANDARD min (6 + fp + diff + arch): $std"
echo "STANDARD max (+DAT, DEP, DEPLOY): $std_max"
echo "DEEP typical (6 + STAKES + NUM + fp + diff + arch), k=0 panels: $deep0"
echo "DEEP max (all 11 auditors, k=2 high-stakes P1 panels): $deep_max_k2"
echo "fp-check share of DEEP max: $((fp*7*100/deep_max_k2))%"
