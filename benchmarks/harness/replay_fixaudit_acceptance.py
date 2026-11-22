"""Acceptance replay of the fix audit (L5.4): its deterministic checks over the real fix diff.

What it replays: the 22 P2 fixes of the self hunt (run 20261120T201246Z) as they stood when the
DEEP DIFF run 20261120T225224Z audited them, which confirmed 7 P2s in or next to those fixes.

- prefix tree: the tree of 191d9c5, master before the P2 fixes;
- fixed tree: 79e700460b55cd2c6dbfa156af777dfdfc86d771, the `base_tree` that run 20261120T225224Z
  recorded in its summary.json (its start fingerprint, the tree its auditors read). It is checked
  against the final squash tree (acde2a3, b77c246 and c156fd5 share it) with the 7 audit fixes'
  files reverted to the pre-fix copies in `.claude/audits/.prefix_snapshot/prefix/` of that run;
- L4 entries: rebuilt here (`FIXES`) from the hunt's L2 finding files, the commit message of
  acde2a3 and the diff, because the P2 fixes were never recorded as an L4 file.

It calls `fixaudit`'s own functions (`fix_hunks`, `sibling_hits`, `mechanism_flags`,
`size_report`, `auditors_for`) in a throwaway shared clone with the fixed tree checked out, so the
sibling sweep's `git grep` searches the audited content. Nothing in the run archives is written.
For each of BUG-001, SEC-001, NUM-001 (sibling sweep) and BUG-002, BUG-003 (mechanism flags) it
prints what the checks list at or for the confirmed finding's site.

    PYTHONPATH=. python benchmarks/harness/replay_fixaudit_acceptance.py

Run from the root of a checkout that holds those objects (the trees are unreferenced, so a
`git gc` can prune 79e700 from this clone; then the replay reports it missing).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
import subprocess
import sys
import tempfile

from cca_checks import fixaudit

PREFIX_COMMIT = "191d9c5"
FIXED_TREE = "79e700460b55cd2c6dbfa156af777dfdfc86d771"
FINAL_COMMIT = "acde2a3"
SNAPSHOT = pathlib.Path(".claude/audits/.prefix_snapshot")
HUNT_L2 = pathlib.Path(".claude/audits/runs/20261120T201246Z/L2.json")

# Files per fix, from the hunt's L2 file of each finding, the commit message's one line per fix
# and the diff. A file several fixes changed is listed by each (size_report splits it evenly).
FIXES = {
    "BUG-004": ["cca_checks/bugs/anchor_log.py"],
    "BUG-005": ["cca_checks/bugs/store.py"],
    "DAT-003": ["cca_checks/bugs/store.py"],
    "SEC-004": ["cca_checks/bugs/store.py", "cca_checks/bugs/fp_records.py"],
    "CODE-006": ["cca_checks/bugs/relink.py"],
    "SEC-005": ["cca_checks/bugs/gitread.py", "cca_checks/bugs/github.py",
                "cca_checks/bugs/holdout.py", "cca_checks/bugs/pending.py"],
    "NUM-001": ["cca_checks/properties.py"],
    "NUM-002": ["cca_checks/plugin/tools/cca_scorecard.py"],
    "NUM-003": ["cca_checks/plugin/tools/cca_scorecard.py"],
    "PERF-001": ["cca_checks/bugs/anchors.py", "cca_checks/bugs/query_anchors.py"],
    "PERF-002": ["cca_checks/plugin/tools/cca_tautology_check.py"],
    "STAKES-006": ["cca_checks/plugin/tools/cca_tautology_check.py"],
    "STAKES-004": ["cca_checks/plugin/installer.py"],
    "CODE-003": ["claude-code/install.sh", "claude-code/install.ps1"],
    "DEPLOY-002": ["claude-code/install.ps1"],
    "CODE-001": ["cca_checks/plugin/hooks/cca_gate_core.py", "cca_checks/pipeline_state.py"],
    "CODE-002": ["cca_checks/pipeline_state.py"],
    # a drift guard over rules that agree today: its only change is its test
    "CODE-004": ["tests/test_self_hunt_p2_consistency.py"],
    "DEPLOY-003": ["cca_checks/plugin/hooks/cca_gate_core.py", "cca_checks/pipeline_state.py",
                   "cca_checks/plugin/commands/audit-fix.md",
                   "cca_checks/plugin/codex/cca-audit/SKILL.md"],
    "DOC-003": ["cca_checks/plugin/commands/audit-fix.md",
                "cca_checks/plugin/codex/cca-audit/SKILL.md"],
    "DOC-004": ["cca_checks/plugin/agents/cca-fp-check.md", "cca_checks/claim.py",
                "docs/pipeline-diagram.md", "cca_checks/plugin/commands/audit-fix.md"],
    "DOC-005": ["cca_checks/plugin/cli.py"],
}

# The confirmed findings of run 20261120T225224Z this replay asks about: (fix whose change they
# sit in or beside, their sites in the fixed tree as that run's L2 cites them).
CASES = {
    "BUG-001": ("BUG-005", [("cca_checks/bugs/store.py", 65), ("cca_checks/bugs/resolve.py", 145),
                            ("cca_checks/bugs/resolve.py", 189)]),
    "SEC-001": ("SEC-005", [("cca_checks/bugs/gitread.py", 38)]),
    "NUM-001": ("NUM-003", [("cca_checks/plugin/tools/cca_scorecard.py", 180)]),
    "BUG-002": ("PERF-002", [("cca_checks/plugin/tools/cca_tautology_check.py", 126)]),
    "BUG-003": ("NUM-003", [("cca_checks/plugin/tools/cca_scorecard.py", 165)]),
}

# Declared patterns a fixer could have given (`sibling_patterns`), tried one at a time. They were
# chosen after reading the findings, so they show what the mechanism can do, not what a fixer
# would have written. NUM-001 is a missed source ("where else is a run's parent recorded"), not
# a textual copy, so only a declared pattern can reach it; BUG-001's automatic pattern is dropped
# as too broad, so a narrower declared one is tried for it too.
DECLARED = {
    "BUG-001": ['ledger.read_text(encoding="utf-8")'],
    "NUM-001": ["re:\\bparent\\b *=", "re:\\.parent\\b", "summary.json", "parent=",
                '"parent"', "state.parent"],
}


def _git(repo, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                          check=True).stdout


def _exists(obj: str) -> bool:
    return subprocess.run(["git", "cat-file", "-e", obj], capture_output=True).returncode == 0


def check_fixed_tree() -> list[str]:
    """Where the fixed tree and the final tree differ, and whether each differing non-test file
    equals the run's pre-fix snapshot copy (a second source for the same content)."""
    notes = []
    manifest = json.loads((SNAPSHOT / "manifest.json").read_text(encoding="utf-8"))
    final_tree = _git(".", "rev-parse", f"{FINAL_COMMIT}^{{tree}}").strip()
    changed = _git(".", "diff", "--name-only", FIXED_TREE, final_tree).split()
    for path in changed:
        blob = subprocess.run(["git", "show", f"{FIXED_TREE}:{path}"], capture_output=True,
                              check=True).stdout
        snap = SNAPSHOT / "prefix" / path
        if path in manifest:
            # The snapshot holds the checkout's bytes (CRLF on Windows), the tree holds LF.
            same = snap.is_file() and snap.read_bytes().replace(b"\r\n", b"\n") \
                == blob.replace(b"\r\n", b"\n")
            intact = snap.is_file() and \
                hashlib.sha256(snap.read_bytes()).hexdigest() == manifest[path]["sha"]
            notes.append(f"{path}: equals the snapshot copy up to line endings: {same}; "
                         f"snapshot copy matches its manifest sha: {intact}")
        else:
            notes.append(f"{path}: not in the snapshot (audited content from {FIXED_TREE[:7]})")
    return notes


def main() -> int:
    for obj in (PREFIX_COMMIT, FIXED_TREE, FINAL_COMMIT):
        if not _exists(obj):
            print(f"missing object {obj}: the replay cannot run in this clone")
            return 1
    out: dict = {"fixed_tree_check": check_fixed_tree()}
    l2 = json.loads(HUNT_L2.read_text(encoding="utf-8"))
    rows = l2 if isinstance(l2, list) else l2["findings"]
    categories = {r["id"]: r["auditor"].removeprefix("cca-") for r in rows if r.get("auditor")}
    categories = {k: fixaudit.DATA_AUDITOR if v == "data-auditor" else v
                  for k, v in categories.items()}
    fixes = [{"fix_id": f"FIX-{fid}", "finding": fid, "files": files, "sibling_patterns": [],
              "sibling_reason": "replay: automatic patterns only"}
             for fid, files in FIXES.items()]
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        clone = pathlib.Path(tmp) / "replay"
        subprocess.run(["git", "clone", "-q", "--shared", "--no-checkout", ".", str(clone)],
                       check=True)
        _git(clone, "read-tree", "-u", "--reset", FIXED_TREE)
        prefix_tree = _git(clone, "rev-parse", f"{PREFIX_COMMIT}^{{tree}}").strip()
        hunks, skipped = fixaudit.fix_hunks(clone, prefix_tree, FIXED_TREE)
        diff_files = {h.path for h in hunks} | set(skipped)
        listed = {f for files in FIXES.values() for f in files}
        out["unlisted_non_test_files"] = sorted(
            p for p in diff_files - listed if not fixaudit.is_test_path(p))
        sweep = fixaudit.sibling_hits(clone, hunks, fixes)
        flags = fixaudit.mechanism_flags(hunks, fixes)
        out["hunks"] = len(hunks)
        out["sibling_hits"] = len(sweep.hits)
        out["too_broad"] = sweep.too_broad
        out["mechanism_flags"] = len(flags)
        out["oversized"] = [s for s in fixaudit.size_report(hunks, fixes) if s["over"]]
        out["auditors"] = fixaudit.auditors_for(fixes, flags, categories)
        cases = {}
        for finding, (fix, sites) in CASES.items():
            fix_id = f"FIX-{fix}"
            own_hits = [h for h in sweep.hits if h["fix_id"] == fix_id]
            cases[finding] = {
                "fix": fix_id,
                "sites": [f"{p}:{n}" for p, n in sites],
                "hits_at_sites": [f"{h['path']}:{h['line']} via {h['pattern']!r}"
                                  for h in own_hits if (h["path"], h["line"]) in sites],
                "fix_hits": len(own_hits),
                "fix_hit_sample": [f"{h['path']}:{h['line']} via {h['pattern']!r}"
                                   for h in own_hits[:12]],
                "flags_at_sites": [f"{f['mechanism']} {f['path']}:{f['line']}"
                                   for f in flags if f["fix_id"] == fix_id
                                   and (f["path"], f["line"]) in sites],
                "fix_flags": sorted({f"{f['mechanism']} {f['path']}:{f['line']}"
                                     for f in flags if f["fix_id"] == fix_id}),
                "site_is_an_added_line": [any(h.path == p and n in h.added_at for h in hunks)
                                          for p, n in sites],
            }
        for finding, patterns in DECLARED.items():
            fix_name, sites = CASES[finding]
            declared = []
            for pattern in patterns:
                fix = {"fix_id": f"FIX-{fix_name}", "finding": fix_name,
                       "files": FIXES[fix_name], "sibling_patterns": [pattern]}
                try:
                    hits = [h for h in fixaudit.sibling_hits(clone, hunks, [fix]).hits
                            if h["pattern"] == pattern]
                except fixaudit.FixAuditRefused as exc:
                    declared.append({"pattern": pattern, "refused": str(exc)})
                    continue
                declared.append({
                    "pattern": pattern, "hits": len(hits),
                    "at_sites": [f"{h['path']}:{h['line']}" for h in hits
                                 if (h["path"], h["line"]) in sites],
                    "sample": [f"{h['path']}:{h['line']}" for h in hits[:15]]})
            cases[finding]["declared"] = declared
        out["cases"] = cases
    json.dump(out, sys.stdout, indent=1)
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
