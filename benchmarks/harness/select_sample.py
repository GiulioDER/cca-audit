#!/usr/bin/env python3
"""Pre-registered, reproducible sampler for the CCA x BugsInPy detection benchmark.

Rule (pre-registered, do not tune after seeing results):
  1. seed = 1337.
  2. Seeded-shuffle the 17 project names; walk them in that order.
  3. Take the first N=12 DISTINCT projects (=> max project diversity for 12 slots).
  4. Within each project, seeded-shuffle its bug ids and take the FIRST bug whose
     patch modifies >= 1 non-test .py source file (so the defect is localizable in
     real source, not a test-only or config-only change).

Blind: no bug is hand-picked; the only filter is "has a localizable source fix".
"""
import os, re, random, json

ROOT = os.path.join(os.environ.get("CCA_BENCH_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), "BugsInPy", "projects")
SEED = 1337
N = 12


def is_test_path(p: str) -> bool:
    p = "/" + p.replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    return ("/tests/" in p or "/test/" in p
            or base.startswith("test_") or base.endswith("_test.py"))


def patch_source_files(patch_path: str):
    """Non-test .py files modified by the patch (new-side b/ paths)."""
    files = []
    if not os.path.exists(patch_path):
        return files
    with open(patch_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r"^\+\+\+ b/(.+)", line.rstrip("\n"))
            if m:
                path = m.group(1).strip()
                if path.endswith(".py") and not is_test_path(path):
                    files.append(path)
    return files


def list_bugs(proj: str):
    bd = os.path.join(ROOT, proj, "bugs")
    if not os.path.isdir(bd):
        return []
    return sorted([d for d in os.listdir(bd) if d.isdigit()], key=int)


def main():
    projects = sorted([d for d in os.listdir(ROOT)
                       if os.path.isdir(os.path.join(ROOT, d))])
    rng = random.Random(SEED)
    proj_order = projects[:]
    rng.shuffle(proj_order)

    selected = []
    for proj in proj_order:
        if len(selected) >= N:
            break
        bugs = list_bugs(proj)
        rng.shuffle(bugs)
        for bid in bugs:
            patch = os.path.join(ROOT, proj, "bugs", bid, "bug_patch.txt")
            srcs = patch_source_files(patch)
            if srcs:
                selected.append({"project": proj, "bug_id": bid,
                                 "source_files": srcs})
                break

    out = {
        "seed": SEED,
        "n": len(selected),
        "rule": ("seeded-shuffle 17 projects; first 12 distinct; within each, "
                 "seeded-shuffle bugs, take first whose patch modifies >=1 "
                 "non-test .py file"),
        "bugs": selected,
    }
    print(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
