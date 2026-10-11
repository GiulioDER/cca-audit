"""Replay the Step 0.5/0.6 content regexes of audit-fix.md over recent commits.

Read-only: runs `git log` and `git show` against origin/master in the given repo.
Path globs are ignored (content check only), so this is a LOWER bound on DEEP.
"""
import re
import subprocess
import sys

REPO = sys.argv[1]
N = int(sys.argv[2]) if len(sys.argv) > 2 else 60

STAKES = re.compile(r"(?i)\b(transfer|withdraw|deposit|payout|refund|charge|settle|execute|submit|sign|approve|grant|revoke|drop|truncate|delete|destroy|purge)\b")
NUM = re.compile(r"(?i)\b(price|qty|quantity|amount|notional|balance|rate|ratio|pct|percent|bps|decimal|round|floor|ceil|scale|convert)\b")


def git(*args):
    return subprocess.run(["git", "-C", REPO, *args], capture_output=True, text=True,
                          encoding="utf-8", errors="replace").stdout


shas = git("log", "--no-merges", f"-{N}", "--format=%h", "origin/master").split()
rows = []
for sha in shas:
    diff = git("show", "--format=", "-U3", sha)
    changed = "\n".join(l[1:] for l in diff.splitlines()
                        if (l.startswith("+") or l.startswith("-"))
                        and not l.startswith("+++") and not l.startswith("---"))
    stat = git("show", "--format=", "--shortstat", sha)
    m = re.findall(r"(\d+) (?:insertion|deletion)", stat)
    size = sum(int(x) for x in m)
    files = len(git("show", "--format=", "--name-only", sha).split())
    hs_full = bool(STAKES.search(diff) or NUM.search(diff))
    hs_changed = bool(STAKES.search(changed) or NUM.search(changed))
    small = size <= 40 and files <= 2
    rows.append((sha, size, files, hs_full, hs_changed, small))

n = len(rows)
deep_full = sum(r[3] for r in rows)
deep_changed = sum(r[4] for r in rows)
small = [r for r in rows if r[5]]
small_deep = sum(r[3] for r in small)
print(f"commits replayed: {n}")
print(f"DEEP by content regex over full diff text: {deep_full}/{n}")
print(f"DEEP by content regex over changed lines only: {deep_changed}/{n}")
print(f"small diffs (<=40 lines, <=2 files): {len(small)}; of those routed DEEP instead of FAST: {small_deep}")
hits = {}
for sha in shas[:N]:
    diff = git("show", "--format=", "-U3", sha)
    for w in STAKES.findall(diff) + NUM.findall(diff):
        hits[w.lower()] = hits.get(w.lower(), 0) + 1
top = sorted(hits.items(), key=lambda kv: -kv[1])[:10]
print("most frequent trigger words:", ", ".join(f"{k}={v}" for k, v in top))
