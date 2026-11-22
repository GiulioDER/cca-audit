"""Q3 of docs/preregistrations/2026-11-22-fix-audit-loop.md: how large past CCA fixes were.

What it measures: for every bug ledger row of this repository (`GiulioDER/cca-audit`, matched
case-insensitively) that has a `fix_commit` present in this clone, the non-test changed lines
(added plus removed, `git show --numstat`, test paths by `fixaudit.is_test_path`, binary files
skipped) of that commit, divided by the number of ledger rows sharing the commit. It prints the
50th, 75th, 90th and 95th percentiles (nearest rank) of that per-row value.

Only CCA fix commits count (Correction 2 of the pre-registration): a commit with one parent whose
subject contains one of FIX_SUBJECTS. A merge's first-parent diff is its whole pull request, and
a commit with another subject may carry feature code, so either would put feature lines in the
numerator. Each excluded commit is printed with its reason.

This is a commit-level average: every fix of one commit gets the same value, so the sample has at
most as many distinct values as there are commits. It is not the statistic `fixaudit.size_report`
applies, which counts per fix only the files that fix lists, over the fix diff from the run's
prefix tree, splitting a file evenly between the fixes that list it.

    PYTHONPATH=. python benchmarks/harness/fix_size_q3.py [sha]

Run from the repository root. With a sha it prints (non-test lines, binary files) of that
commit only, the hand check recorded in the pre-registration.
"""
import math
import subprocess
import sys
from collections import Counter
from pathlib import Path

from cca_checks.bugs.gitread import git
from cca_checks.bugs.store import LEDGER, bug_rows, bugs_dir
from cca_checks.fixaudit import is_test_path

#: The subjects CCA's Step 7 gives its fix commits ("fix(x): N audit fixes from CCA (...)").
FIX_SUBJECTS = ("audit fixes from CCA", "deferred fixes from CCA")


def lines_of(sha):
    out = subprocess.run(["git", "show", "--numstat", "--format=", sha], capture_output=True,
                         text=True, check=True).stdout
    n = binary = 0
    for ln in out.splitlines():
        a, d, p = ln.split("\t", 2)
        if is_test_path(p):
            continue
        if a == "-":
            binary += 1
            continue
        n += int(a) + int(d)
    return n, binary


def excluded(sha):
    """Why `sha` is not a CCA fix commit, or None when it is."""
    if len(git(Path("."), "rev-list", "--parents", "-n1", sha).split()) > 2:
        return "merge (its first-parent diff is the whole pull request)"
    subject = git(Path("."), "log", "-1", "--format=%s", sha).strip()
    if not any(s in subject for s in FIX_SUBJECTS):
        return f"subject marks no CCA fix commit: {subject}"
    return None


def pct(v, q):  # nearest rank
    v = sorted(v)
    return v[max(0, math.ceil(q / 100 * len(v)) - 1)]


if len(sys.argv) > 1:
    print(lines_of(sys.argv[1]))
    sys.exit()
rows = [r for r in bug_rows(bugs_dir() / LEDGER) if r.fix_commit]
mine = [r for r in rows if r.repo.lower() == "giulioder/cca-audit"]
print("rows with fix_commit", len(rows), "this repo", len(mine), "other repo",
      len(rows) - len(mine))
have = {}
for sha in {r.fix_commit for r in mine}:
    have[sha] = subprocess.run(["git", "cat-file", "-e", sha + "^{commit}"],
                               capture_output=True).returncode == 0
ok = [r for r in mine if have[r.fix_commit]]
print("skipped missing commit: fixes", len(mine) - len(ok), "commits",
      sum(not v for v in have.values()))
why = {s: excluded(s) for s in {r.fix_commit for r in ok}}
for s, reason in sorted((s, w) for s, w in why.items() if w):
    print("excluded", s[:10], "fixes", sum(r.fix_commit == s for r in ok), reason)
ok = [r for r in ok if not why[r.fix_commit]]
share = Counter(r.fix_commit for r in ok)
cache = {s: lines_of(s) for s in share}
vals = [cache[r.fix_commit][0] / share[r.fix_commit] for r in ok]
print("fixes", len(vals), "commits", len(share), "binary files excluded",
      sum(b for _, b in cache.values()))
print("distinct values", len(set(vals)))
for q in (50, 75, 90, 95):
    print(q, pct(vals, q))
