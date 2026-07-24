#!/usr/bin/env python3
"""Materialize the FRESH corpus from each PR's merge/fix commit self-diff.
Ground truth = `gh api commits/{merge_commit_sha}` files[].patch (buggy = parent).
Also emits tasks_fresh_wf.json + bugs_fresh_index.json in one pass.
"""
import os, re, json, subprocess

BASE = os.environ.get("CCA_BENCH_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MAN = os.path.join(BASE, "harness", "fresh_manifest.json")
DATA = os.path.join(BASE, "data_fresh")


def gh(args):
    r = subprocess.run(["gh", "api"] + args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout, None) if r.returncode == 0 else (None, (r.stderr or "").strip())


def gh_raw(org_repo, path, ref):
    return gh(["-H", "Accept: application/vnd.github.raw",
               f"repos/{org_repo}/contents/{path}?ref={ref}"])


def is_test(p):
    p = "/" + p.replace("\\", "/").lower()
    b = p.rsplit("/", 1)[-1]
    return "/tests/" in p or "/test/" in p or b.startswith("test_") or b.endswith("_test.py")


def hunks(patch):
    hs = []
    for line in (patch or "").splitlines():
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            a, b, c, d = m.groups()
            hs.append({"old_start": int(a), "old_len": int(b) if b else 1,
                       "new_start": int(c), "new_len": int(d) if d else 1})
    return hs


def wf(dest, content):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def safe(bug):
    return bug.replace("/", "__").replace("#", "__")


def main():
    man = json.load(open(MAN, encoding="utf-8"))
    tasks, bugs = [], {}
    excluded, dropped_files = [], []
    for c in man:
        org_repo, fixed, bug = c["org_repo"], c["merge_commit_sha"], c["bug"]
        cj, err = gh([f"repos/{org_repo}/commits/{fixed}"])
        if cj is None:
            print(f"COMMIT FAIL {bug}: {err}"); continue
        commit = json.loads(cj)
        parents = [p["sha"] for p in commit.get("parents", [])]
        parent = parents[0] if parents else ""
        files = {f["filename"]: hunks(f["patch"]) for f in commit.get("files", [])
                 if f.get("filename", "").endswith(".py") and not is_test(f["filename"]) and f.get("patch")}
        if not files or not parent:
            print(f"skip (no src/parent) {bug}"); continue
        out = os.path.join(DATA, safe(bug))
        os.makedirs(out, exist_ok=True)
        bugs[bug] = {"project": org_repo, "files": {}}
        errs = 0
        for path, hs in files.items():
            contents = {}
            for ver, ref in (("buggy", parent), ("fixed", fixed)):
                content, e = gh_raw(org_repo, path, ref)
                if content is None:
                    errs += 1
                else:
                    wf(os.path.join(out, ver, path.replace("/", os.sep)), content)
                    contents[ver] = content
            numeric = any(s in (contents.get("buggy") or "")
                          for s in ("import numpy", "import math", "from math", "np."))
            bw = [[h["old_start"], h["old_start"] + max(h["old_len"], 1) - 1] for h in hs]
            fw = [[h["new_start"], h["new_start"] + max(h["new_len"], 1) - 1] for h in hs]
            # Index the file ONLY if both versions were fetched. The index is the
            # scorer's denominator, so a file no auditor was ever shown must not
            # sit in it: it can never be caught, and counting it turns a fetch
            # failure into a recall penalty against the tool. The commonest cause
            # is not a transient error but a structural one -- a fix that CREATES
            # a file has no buggy version to audit, which is exactly what
            # bartfeenstra/betty#4027 ("Add missing __init__.py files") is.
            if "buggy" in contents and "fixed" in contents:
                bugs[bug]["files"][path] = {"buggy_windows": bw, "fixed_windows": fw}
                tasks.append({
                    "bug": bug, "project": org_repo, "file": path, "numeric": numeric,
                    "buggy_path": os.path.join(out, "buggy", path.replace("/", os.sep)).replace("\\", "/"),
                    "fixed_path": os.path.join(out, "fixed", path.replace("/", os.sep)).replace("\\", "/"),
                })
            else:
                dropped_files.append(f"{bug}::{path}")
        # groundtruth.json keeps the FULL fix for the record; only the scoring
        # index is narrowed. The two answer different questions.
        gt = {"project": org_repo, "bug": bug, "org_repo": org_repo,
              "buggy_commit": parent, "fixed_commit": fixed, "pr": c["pr"],
              "stars": c["stars"], "title": c["title"], "files": files}
        json.dump(gt, open(os.path.join(out, "groundtruth.json"), "w"), indent=2)
        tot = sum(h["new_len"] for hs in files.values() for h in hs)
        if not bugs[bug]["files"]:
            # Every file unfetchable => the bug is uncatchable by construction.
            # Excluding it LOUDLY: a silently-dropped bug and a missed bug are the
            # same number in the denominator and opposite facts about the tool.
            del bugs[bug]
            excluded.append(bug)
            print(f"EXCLUDED {bug:<42} no auditable file (all {len(files)} unfetchable)")
            continue
        partial = "" if len(bugs[bug]["files"]) == len(files) else \
            f"  PARTIAL {len(bugs[bug]['files'])}/{len(files)} files auditable"
        print(f"{bug:<42} src={len(files)} fixlines={tot} errs={errs}{partial}")

    json.dump(tasks, open(os.path.join(BASE, "harness", "tasks_fresh_wf.json"), "w"), indent=2)
    json.dump(bugs, open(os.path.join(BASE, "harness", "bugs_fresh_index.json"), "w"), indent=2)
    # Written, not just printed: the exclusions are part of how the denominator
    # was arrived at, and a reader of the results has to be able to see them
    # without re-running anything.
    json.dump({"excluded_bugs": excluded, "dropped_files": dropped_files},
              open(os.path.join(BASE, "harness", "fresh_exclusions.json"), "w"), indent=2)
    print(f"\n{len(tasks)} file-tasks across {len(bugs)} bugs "
          f"({sum(t['numeric'] for t in tasks)} numeric)")
    if excluded or dropped_files:
        print(f"excluded {len(excluded)} bug(s), dropped {len(dropped_files)} unfetchable file(s) "
              f"-> harness/fresh_exclusions.json")


if __name__ == "__main__":
    main()
