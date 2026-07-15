#!/usr/bin/env python3
"""Mine a FRESH, likely-uncontaminated corpus of real recent bug fixes.

Strategy: most-recent merged PRs labelled `bug` in Python repos, then keep only
non-fork, non-archived, mid-size repos where the fix is a SMALL non-test source
change AND ships a regression test (a strong 'genuine, well-defined bug' signal).
Dedup by repo. Contamination is certified later by the recognition probe, not here.
"""
import json, subprocess, sys

WINDOW = "2026-02-01..2026-07-10"
TARGET = 22


def gh_json(args):
    r = subprocess.run(["gh"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout)
    except Exception:
        return None


def is_test(p):
    p = "/" + p.lower()
    b = p.rsplit("/", 1)[-1]
    return "/tests/" in p or "/test/" in p or b.startswith("test_") or b.endswith("_test.py")


search = gh_json(["search", "prs", "--language=python", "--label=bug", "--merged",
                  "--merged-at=" + WINDOW, "--sort=updated", "--order=desc",
                  "--limit=120", "--json", "number,title,url,repository,closedAt"]) or []
print(f"search returned {len(search)} PRs", file=sys.stderr)

seen, cands = set(), []
for pr in search:
    if len(cands) >= TARGET:
        break
    repo = pr["repository"]["nameWithOwner"]
    if repo in seen:
        continue
    meta = gh_json(["api", f"repos/{repo}"])
    if not meta or meta.get("fork") or meta.get("archived"):
        continue
    stars = meta.get("stargazers_count", 0)
    if not (150 <= stars <= 20000):
        continue
    prfull = gh_json(["api", f"repos/{repo}/pulls/{pr['number']}"])
    mc = prfull.get("merge_commit_sha") if prfull else None
    if not mc:
        continue
    files = gh_json(["api", f"repos/{repo}/pulls/{pr['number']}/files", "--paginate"]) or []
    src = [f for f in files if f["filename"].endswith(".py") and not is_test(f["filename"]) and f.get("patch")]
    tests = [f for f in files if f["filename"].endswith(".py") and is_test(f["filename"])]
    src_changes = sum(f.get("additions", 0) + f.get("deletions", 0) for f in src)
    if not src or not tests or src_changes > 60 or len(src) > 3:
        continue
    seen.add(repo)
    cands.append({"bug": f"{repo}#{pr['number']}", "org_repo": repo, "pr": pr["number"],
                  "merge_commit_sha": mc, "stars": stars, "title": pr["title"],
                  "closed": pr.get("closedAt", "")[:10],
                  "source_files": [f["filename"] for f in src]})
    print(f"  KEEP {repo:<34} #{pr['number']:<6} ★{stars:<6} src={len(src)} "
          f"tests={len(tests)} chg={src_changes:<3} {pr['title'][:44]}", file=sys.stderr)

json.dump(cands, open("harness/fresh_manifest.json", "w"), indent=2)
print(f"\n{len(cands)} candidates -> harness/fresh_manifest.json", file=sys.stderr)
