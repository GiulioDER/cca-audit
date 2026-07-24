#!/usr/bin/env python3
"""CCA red-state proof — the tautological-test detector.

A red→green test is the pipeline's evidence that a fix resolved a real defect. But
a test written *after* the fix can pass against the pre-fix code too, in which case
it proves nothing and the "verified" fix is unverified. This is the reward-hacking
surface of the fix stage: the cheapest way to make L5 green is to write a test that
was never red.

This tool settles it by execution instead of by reading the test:

    snapshot   save pre-fix content of the files about to be edited (run at L4 start)
    verify     restore that content, re-run each claimed proof test, assert it FAILS,
               then put the fixed content back

Three verdicts, not two — because "did not pass" is not the same as "proved the bug":

  RED           failed pre-fix with a real test failure   -> genuine proof
  TAUTOLOGICAL  PASSED pre-fix                            -> proves nothing; fix unverified
  INCONCLUSIVE  errored / not collected pre-fix           -> cannot tell a defect-pin from
                                                             an unrelated breakage (e.g. the
                                                             test imports a symbol the fix
                                                             introduced). Must be reported,
                                                             never counted as proof.

SAFETY: the working tree is mutated only between the stash and the restore, both
guarded by try/finally, and the restore is verified by hash. The fixed content is
copied to the snapshot dir BEFORE any revert, so it is recoverable even on a hard
kill — the recovery path is printed if the hash check ever fails.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

DEFAULT_SNAPDIR = Path(".claude/audits/.prefix_snapshot")
MANIFEST = "manifest.json"

RED, TAUTOLOGICAL, INCONCLUSIVE = "RED", "TAUTOLOGICAL", "INCONCLUSIVE"


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


@dataclass
class ProofResult:
    finding: str
    nodeid: str
    verdict: str
    detail: str

    @property
    def is_proof(self) -> bool:
        return self.verdict == RED


# ---------------------------------------------------------------- snapshot

def cmd_snapshot(files: list[Path], snapdir: Path) -> int:
    if snapdir.exists():
        shutil.rmtree(snapdir)
    (snapdir / "prefix").mkdir(parents=True)
    manifest = {}
    for f in files:
        if not f.exists():
            print(f"snapshot: {f} does not exist — skipping (new file, no pre-fix state)")
            continue
        dest = snapdir / "prefix" / f
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, dest)
        manifest[str(f).replace("\\", "/")] = {"sha": _sha(f)}
    (snapdir / MANIFEST).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"snapshot: stored pre-fix content of {len(manifest)} file(s) in {snapdir}")
    return 0


# ---------------------------------------------------------------- verify

# A test that references a symbol the FIX introduced blows up on name resolution
# rather than on behaviour. pytest reports that as "1 failed" (an exception in the
# test body is a failure, not an error), so the exit code alone cannot tell it apart
# from a genuine defect-pin — the traceback has to be read.
#
# Note this is deliberately NOT "AssertionError only": a real red->green test can
# legitimately fail pre-fix with a domain exception (an injection test failing with
# sqlite3.OperationalError is the defect manifesting). Only symbol-resolution
# failures mean the test never reached the code under test.
_UNREACHED = re.compile(
    r"\b(ImportError|ModuleNotFoundError|AttributeError|NameError|"
    r"fixture '.*' not found|errors? during collection)\b"
)


def _run_one(nodeid: str, extra: list[str]) -> tuple[str, str]:
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", nodeid, "-q", "--tb=line",
         "-p", "no:cacheprovider", *extra],
        capture_output=True, text=True,
    )
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out.strip().splitlines()[-1] if out.strip() else f"(rc={proc.returncode})"
    if proc.returncode == 0:
        return TAUTOLOGICAL, tail
    if proc.returncode == 5 or re.search(r"\b\d+ errors?\b", out):
        return INCONCLUSIVE, tail                      # not collected / collection error
    if _UNREACHED.search(out):
        m = _UNREACHED.search(out)
        return INCONCLUSIVE, f"{m.group(0)} — test never reached the code under test"
    if re.search(r"\b\d+ failed", out):
        return RED, tail
    return INCONCLUSIVE, tail


def verify(proofs: list[tuple[str, str]], snapdir: Path = DEFAULT_SNAPDIR,
           extra_pytest: list[str] | None = None) -> list[ProofResult]:
    extra = extra_pytest or []
    man_path = snapdir / MANIFEST
    if not man_path.exists():
        raise SystemExit(f"no snapshot at {snapdir} — run `snapshot` at Layer 4 start "
                         f"BEFORE applying fixes, or the pre-fix state is unrecoverable")
    manifest = json.loads(man_path.read_text(encoding="utf-8"))
    if not manifest:
        raise SystemExit(f"snapshot at {snapdir} is empty — nothing to prove red against")

    fixed_dir = snapdir / "fixed"
    if fixed_dir.exists():
        shutil.rmtree(fixed_dir)
    fixed_dir.mkdir(parents=True)

    live = [Path(r) for r in manifest]
    # Stash the FIXED content first so it survives a hard kill.
    for rel in live:
        dest = fixed_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(rel, dest)

    results: list[ProofResult] = []
    try:
        for rel in live:                                   # revert to pre-fix
            shutil.copy2(snapdir / "prefix" / rel, rel)
        for finding, nodeid in proofs:
            verdict, detail = _run_one(nodeid, extra)
            results.append(ProofResult(finding, nodeid, verdict, detail))
    finally:
        bad = []
        for rel in live:                                   # ALWAYS restore
            shutil.copy2(fixed_dir / rel, rel)
            if _sha(rel) != _sha(fixed_dir / rel):
                bad.append(str(rel))
        if bad:
            print(f"\n*** RESTORE FAILED for {bad} — recover with:\n"
                  f"***   cp -r {fixed_dir}/. .\n", file=sys.stderr)
    return results


def render(results: list[ProofResult]) -> str:
    out = [f"{'finding':<12} {'verdict':<14} nodeid"]
    for r in results:
        out.append(f"{r.finding:<12} {r.verdict:<14} {r.nodeid}")
    taut = [r for r in results if r.verdict == TAUTOLOGICAL]
    inc = [r for r in results if r.verdict == INCONCLUSIVE]
    out.append("")
    out.append(f"Red-state proof: {sum(r.is_proof for r in results)}/{len(results)} genuine "
               f"| tautological: {len(taut)} | inconclusive: {len(inc)}")
    if taut:
        out.append("  FAIL — these tests PASS against the pre-fix code, so they prove nothing. "
                   "Their findings are UNVERIFIED:")
        out.extend(f"    {r.finding}: {r.nodeid}" for r in taut)
    if inc:
        out.append("  WARN — these could not run against the pre-fix code (error/not collected). "
                   "Not proof; a human must look:")
        out.extend(f"    {r.finding}: {r.nodeid} — {r.detail}" for r in inc)
    return "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("snapshot", help="save pre-fix content (run at Layer 4 start)")
    s.add_argument("files", nargs="+", type=Path)
    s.add_argument("--snapdir", type=Path, default=DEFAULT_SNAPDIR)

    v = sub.add_parser("verify", help="prove each claimed red->green test was really red")
    v.add_argument("--proof", action="append", default=[], metavar="FINDING=NODEID",
                   help="repeatable, e.g. --proof FIX-004=tests/test_x.py::test_injection")
    v.add_argument("--snapdir", type=Path, default=DEFAULT_SNAPDIR)
    v.add_argument("--json", action="store_true")

    a = ap.parse_args()
    if a.cmd == "snapshot":
        return cmd_snapshot(a.files, a.snapdir)

    proofs = []
    for spec in a.proof:
        if "=" not in spec:
            raise SystemExit(f"--proof needs FINDING=NODEID, got {spec!r}")
        f, n = spec.split("=", 1)
        proofs.append((f, n))
    if not proofs:
        raise SystemExit("verify needs at least one --proof FINDING=NODEID")

    results = verify(proofs, a.snapdir)
    if a.json:
        print(json.dumps([asdict(r) for r in results], indent=2))
    else:
        print(render(results))
    # Non-zero if any claimed proof is not a proof — this is a gate.
    return 1 if any(r.verdict != RED for r in results) else 0


if __name__ == "__main__":
    raise SystemExit(main())
