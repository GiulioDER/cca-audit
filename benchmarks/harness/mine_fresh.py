#!/usr/bin/env python3
"""Mine a FRESH, likely-uncontaminated corpus of real recent bug fixes.

Strategy: merged PRs labelled as bugs in Python repos, then keep only non-fork,
non-archived, mid-size repos where the fix is a SMALL non-test source change AND
ships a regression test (a strong 'genuine, well-defined bug' signal). Dedup by
repo. Contamination is certified later by the recognition probe, not here.

SCALED 2026-07-24 (docs/specs/2026-07-24-fresh-corpus-scale-design.md). The pilot
returned 10 candidates and 7 clean bugs, and 3/7 is not a number anyone should
publish. The binding constraint was never the filters -- it was the SEARCH POOL:
one `gh search prs --limit=120` query, whose survivors ran out at 10, so TARGET=22
never bound. Four widenings, none of which touch what makes a candidate
trustworthy:

  * shard the window by month, so each shard gets its own result budget
  * try several label vocabularies -- `bug` is a convention, not a standard
  * extend the window to today
  * widen the star band

Deliberately NOT relaxed, because these are what make the ground truth worth
citing rather than merely plentiful:

  * the regression-test requirement -- a merged fix that ships its own test is the
    signal that this is a real, well-defined defect with unambiguous ground truth
  * <=3 source files / <=60 changed lines -- localization within +/-3 lines is
    meaningless against a sprawling diff
  * one bug per repo -- relaxing it buys candidates by adding within-repo
    correlation, which silently narrows the confidence interval we are trying to
    earn honestly

If these widenings do not reach the target, widen the window BACKWARDS toward the
model cutoff and re-probe. Do not buy candidates with the filters above: reaching
n=30 by weakening ground truth is a worse outcome than reporting n=22 honestly.
"""
import json
import subprocess
import sys
import time

# Post-cutoff by construction: the assistant's knowledge cutoff is Jan 2026, so a
# PR merged from February onward cannot have been memorized. The recognition probe
# still certifies each file -- this window is a prior, not the evidence.
MONTHS = [
    ("2026-02-01", "2026-02-28"),
    ("2026-03-01", "2026-03-31"),
    ("2026-04-01", "2026-04-30"),
    ("2026-05-01", "2026-05-31"),
    ("2026-06-01", "2026-06-30"),
    ("2026-07-01", "2026-07-24"),
]
# `bug` is a GitHub default label, but plenty of projects use their own. Each is a
# separate query because `gh search prs` has no OR over labels.
LABELS = ["bug", "type: bug", "kind/bug", "bugfix", "defect"]
PER_QUERY = 100
STARS_MIN, STARS_MAX = 100, 60_000
MAX_SRC_FILES = 3
MAX_SRC_CHANGES = 60
TARGET = 55
# `gh search prs` is capped at 30/min. Six months x five labels is exactly 30
# queries, so pace them rather than burning the budget and silently getting
# short result sets back.
SEARCH_SLEEP = 2.5


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


def search_all():
    """Every (month x label) shard, deduplicated by PR url, in a stable order."""
    pool, seen_urls = [], set()
    queries = 0
    for start, end in MONTHS:
        for label in LABELS:
            hits = gh_json([
                "search", "prs", "--language=python", f"--label={label}", "--merged",
                f"--merged-at={start}..{end}", "--sort=updated", "--order=desc",
                f"--limit={PER_QUERY}", "--json", "number,title,url,repository,closedAt",
            ])
            queries += 1
            n_new = 0
            if hits:
                for pr in hits:
                    if pr["url"] not in seen_urls:
                        seen_urls.add(pr["url"])
                        pool.append(pr)
                        n_new += 1
            print(f"  {start[:7]}  {label:<10} -> {len(hits or []):>3} hits, {n_new:>3} new "
                  f"(pool {len(pool)})", file=sys.stderr)
            time.sleep(SEARCH_SLEEP)
    print(f"\n{queries} queries -> pool of {len(pool)} distinct PRs", file=sys.stderr)
    # Stable order so a re-run with the same inputs walks candidates identically.
    pool.sort(key=lambda pr: (pr["repository"]["nameWithOwner"], pr["number"]))
    return pool


def main():
    pool = search_all()

    seen_repos, cands = set(), []
    examined = 0
    for pr in pool:
        if len(cands) >= TARGET:
            break
        repo = pr["repository"]["nameWithOwner"]
        if repo in seen_repos:
            continue  # one bug per repo -- see the module docstring
        examined += 1

        meta = gh_json(["api", f"repos/{repo}"])
        if not meta or meta.get("fork") or meta.get("archived"):
            continue
        stars = meta.get("stargazers_count", 0)
        if not (STARS_MIN <= stars <= STARS_MAX):
            continue

        prfull = gh_json(["api", f"repos/{repo}/pulls/{pr['number']}"])
        mc = prfull.get("merge_commit_sha") if prfull else None
        if not mc:
            continue

        files = gh_json(["api", f"repos/{repo}/pulls/{pr['number']}/files", "--paginate"]) or []
        src = [f for f in files
               if f["filename"].endswith(".py") and not is_test(f["filename"]) and f.get("patch")]
        tests = [f for f in files if f["filename"].endswith(".py") and is_test(f["filename"])]
        src_changes = sum(f.get("additions", 0) + f.get("deletions", 0) for f in src)
        if not src or not tests or src_changes > MAX_SRC_CHANGES or len(src) > MAX_SRC_FILES:
            continue

        seen_repos.add(repo)
        cands.append({"bug": f"{repo}#{pr['number']}", "org_repo": repo, "pr": pr["number"],
                      "merge_commit_sha": mc, "stars": stars, "title": pr["title"],
                      "closed": pr.get("closedAt", "")[:10],
                      "source_files": [f["filename"] for f in src]})
        print(f"  KEEP {repo:<34} #{pr['number']:<6} *{stars:<6} src={len(src)} "
              f"tests={len(tests)} chg={src_changes:<3} {pr['title'][:44]}", file=sys.stderr)

    json.dump(cands, open("harness/fresh_manifest.json", "w"), indent=2)

    # Provenance is written beside the manifest, not inside it: fresh_materialize.py
    # consumes the manifest as a plain list, and a benchmark whose sample cannot be
    # shown to predate its own results has no standing to criticise anyone else's.
    prov = {
        "generated_for": "docs/specs/2026-07-24-fresh-corpus-scale-design.md",
        "months": MONTHS, "labels": LABELS, "per_query": PER_QUERY,
        "language": "python", "state": "merged",
        "stars_min": STARS_MIN, "stars_max": STARS_MAX,
        "max_src_files": MAX_SRC_FILES, "max_src_changes": MAX_SRC_CHANGES,
        "requires_regression_test": True, "one_bug_per_repo": True,
        "target": TARGET,
        "pool_size": len(pool), "repos_examined": examined, "candidates": len(cands),
    }
    json.dump(prov, open("harness/fresh_provenance.json", "w"), indent=2)

    print(f"\n{len(cands)} candidates -> harness/fresh_manifest.json", file=sys.stderr)
    print("provenance -> harness/fresh_provenance.json", file=sys.stderr)
    if len(cands) < 43:
        print(f"\nWARNING: {len(cands)} candidates. At the pilot's ~70% clean rate that is "
              f"~{int(len(cands) * 0.7)} clean bugs, short of the 30 the spec targets. "
              f"Widen the window BACKWARDS -- do not relax the quality filters.", file=sys.stderr)


if __name__ == "__main__":
    main()
