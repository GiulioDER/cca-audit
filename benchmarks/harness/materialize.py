#!/usr/bin/env python3
"""Materialize the benchmark sample from the FIXING COMMIT'S OWN DIFF (authoritative).

Ground truth = `gh api repos/{org}/{repo}/commits/{fixed_commit}` -> files[].patch,
which is the fix vs its parent: minimal hunks, every file the human fix touched, and
buggy version == the fixing commit's parent. (BugsInPy's bug_patch.txt is unreliable:
for some bugs it balloons to the full buggy..fixed tree diff and even drops files.)

Outputs under cca-bench/data/<project>__<bug>/:
  buggy/<path>        source at parent(fixed_commit)   (the last buggy state)
  fixed/<path>        source at fixed_commit
  groundtruth.json    per-file hunks {old_start,old_len,new_start,new_len} + meta
"""
import os, re, json, subprocess

BASE = os.environ.get("CCA_BENCH_DIR", os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
PROJ = os.path.join(BASE, "BugsInPy", "projects")
DATA = os.path.join(BASE, "data")
MANIFEST = os.path.join(BASE, "harness", "manifest.json")


def gh(args):
    r = subprocess.run(["gh", "api", *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    return (r.stdout, None) if r.returncode == 0 else (None, (r.stderr or "").strip())


def gh_raw(org_repo, path, ref):
    return gh(["-H", "Accept: application/vnd.github.raw",
               f"repos/{org_repo}/contents/{path}?ref={ref}"])


def parse_kv(path):
    d = {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            m = re.match(r'\s*(\w+)\s*=\s*"?(.*?)"?\s*$', line)
            if m:
                d[m.group(1)] = m.group(2)
    return d


def is_test_path(p):
    p = "/" + p.replace("\\", "/").lower()
    base = p.rsplit("/", 1)[-1]
    return ("/tests/" in p or "/test/" in p
            or base.startswith("test_") or base.endswith("_test.py"))


def patch_hunks(patch_text):
    hs = []
    for line in (patch_text or "").splitlines():
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            os_, ol, ns, nl = m.groups()
            hs.append({"old_start": int(os_), "old_len": int(ol) if ol is not None else 1,
                       "new_start": int(ns), "new_len": int(nl) if nl is not None else 1})
    return hs


def org_repo_from_url(url):
    return "/".join(url.rstrip("/").replace("https://github.com/", "").split("/")[:2])


def write_file(dest, content):
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "w", encoding="utf-8", newline="") as f:
        f.write(content)


def main():
    manifest = json.load(open(MANIFEST, encoding="utf-8"))
    summary = []
    for b in manifest["bugs"]:
        proj, bid = b["project"], b["bug_id"]
        info = parse_kv(os.path.join(PROJ, proj, "bugs", bid, "bug.info"))
        pinfo = parse_kv(os.path.join(PROJ, proj, "project.info"))
        org_repo = org_repo_from_url(pinfo.get("github_url", ""))
        fixed = info.get("fixed_commit_id", "")

        cj, err = gh([f"repos/{org_repo}/commits/{fixed}"])
        if cj is None:
            print(f"{proj}/{bid} COMMIT FETCH FAILED: {err}"); continue
        commit = json.loads(cj)
        parents = [p["sha"] for p in commit.get("parents", [])]
        merge = len(parents) > 1
        parent = parents[0] if parents else ""
        fixed_sha = commit["sha"]

        files = {}
        for f in commit.get("files", []):
            fn = f.get("filename", "")
            if fn.endswith(".py") and not is_test_path(fn) and f.get("patch"):
                files[fn] = patch_hunks(f["patch"])

        out = os.path.join(DATA, f"{proj}__{bid}")
        os.makedirs(out, exist_ok=True)
        errors = []
        for path in files:
            for ver, ref in (("buggy", parent), ("fixed", fixed_sha)):
                content, e = gh_raw(org_repo, path, ref)
                if content is None:
                    errors.append(f"{ver}:{path}: {e}")
                else:
                    write_file(os.path.join(out, ver, path), content)

        gt = {"project": proj, "bug_id": bid, "org_repo": org_repo,
              "buggy_commit": parent, "fixed_commit": fixed_sha,
              "merge_commit": merge, "python_version": info.get("python_version", ""),
              "test_file": info.get("test_file", ""), "files": files}
        json.dump(gt, open(os.path.join(out, "groundtruth.json"), "w",
                           encoding="utf-8"), indent=2)

        tot = sum(h["new_len"] for hs in files.values() for h in hs)
        summary.append({"bug": f"{proj}/{bid}", "org_repo": org_repo,
                        "src_files": len(files), "new_lines_changed": tot,
                        "merge": merge, "errors": errors})
        flag = "  <-- MERGE" if merge else ("  <-- BIG" if tot > 200 else "")
        print(f"{proj}/{bid:<4} {org_repo:<26} src_files={len(files)} "
              f"fix_lines={tot:<4} errors={len(errors)}{flag}")
        for e in errors:
            print(f"    ERR {e}")
    json.dump(summary, open(os.path.join(DATA, "materialize_summary.json"), "w",
                            encoding="utf-8"), indent=2)
    print(f"\nMaterialized {len(summary)} bugs -> {DATA}")


if __name__ == "__main__":
    main()
