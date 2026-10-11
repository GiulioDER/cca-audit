#!/usr/bin/env python3
"""cca-analyzer runner: the only program the remote analyzer key may start.

Installed on the host as /home/cca-analyzer/bin/cca-analyzer-runner and named in that
account's authorized_keys as `restrict,command="..."`, so sshd runs it whatever the client
asks; the client's own command line never reaches it. It reads ONE tar archive on stdin:
`request.json` ({"protocol": 1, "op": "version" | "semgrep"}) and, for `semgrep`, exactly
`rules.yaml` and one `target.py` or `target.rs`. It answers with one JSON document:
{"ok": true, ...} or {"ok": false, "error": "..."}.

What it refuses, before anything runs: an unknown protocol or operation; any member that
is not a regular file; any member name outside the fixed set (so no path, no `..`, no
other extension); a missing or extra member; more than 5 MB; a third concurrent run.
The audited file is untrusted: it is unpacked into a fresh private folder, scanned by
semgrep under a memory and CPU cap with the same flags the local checker uses, and the
folder is removed whatever happens.

Standard library only, and nothing imported from cca_checks: this file is copied alone.
The command-line options exist for tests; the installed authorized_keys line passes none.
"""
from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile

PROTOCOL = 1
MAX_BYTES = 5 * 1024 * 1024
STDIN_CAP = MAX_BYTES + 64 * 1024  # file content plus tar headers
TARGET_EXTS = ("py", "rs")
ALLOWED_NAMES = frozenset({"request.json", "rules.yaml", *(f"target.{e}" for e in TARGET_EXTS)})
MAX_RUNS = 2
TIMEOUT_S = 120.0
DEFAULT_SEMGREP = ["/home/cca-analyzer/venv/bin/semgrep"]
DEFAULT_LOCK_DIR = "/home/cca-analyzer/locks"
SEMGREP_FLAGS = ["--json", "--metrics=off", "--disable-version-check", "--quiet",
                 "--disable-nosem", "--no-git-ignore"]
LIMITS = ["systemd-run", "--user", "--scope", "--quiet", "--collect",
          "-p", "MemoryMax=2G", "-p", "MemorySwapMax=0", "-p", "CPUQuota=100%",
          "nice", "-n", "15"]


class Refused(Exception):
    pass


def read_request(data: bytes) -> tuple[dict, dict[str, bytes]]:
    if len(data) > STDIN_CAP:
        raise Refused("request larger than 5 MB")
    try:
        archive = tarfile.open(fileobj=io.BytesIO(data), mode="r:")
    except tarfile.TarError as exc:
        raise Refused(f"not a tar archive ({exc})") from None
    members: dict[str, bytes] = {}
    with archive:
        for m in archive.getmembers():
            if not m.isreg():
                raise Refused(f"member {m.name!r} is not a regular file")
            if m.name not in ALLOWED_NAMES:
                raise Refused(f"member name {m.name!r} is not allowed")
            if m.name in members:
                raise Refused(f"duplicate member {m.name!r}")
            fh = archive.extractfile(m)
            members[m.name] = fh.read() if fh is not None else b""
    total = sum(len(v) for v in members.values())
    if total > MAX_BYTES:
        raise Refused("request larger than 5 MB")
    if "request.json" not in members:
        raise Refused("missing request.json")
    try:
        req = json.loads(members.pop("request.json"))
    except ValueError:
        raise Refused("request.json is not JSON") from None
    if not isinstance(req, dict) or req.get("protocol") != PROTOCOL:
        raise Refused("unsupported protocol")
    op = req.get("op")
    if op == "version":
        if members:
            raise Refused("a version request carries no files")
        return req, {}
    if op != "semgrep":
        raise Refused(f"unknown operation {op!r}")
    targets = [n for n in members if n != "rules.yaml"]
    if "rules.yaml" not in members or len(targets) != 1:
        raise Refused("a semgrep request carries exactly rules.yaml and one target")
    return req, members


def semgrep_argv(semgrep_cmd: list[str], target: str, wrap: bool) -> list[str]:
    return [*(LIMITS if wrap else []), *semgrep_cmd, "--config", "rules.yaml",
            *SEMGREP_FLAGS, target]


def _try_lock(fh) -> bool:
    fh.seek(0)
    try:
        import fcntl
    except ImportError:  # Windows, where the tests run
        import msvcrt
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        return True
    except OSError:
        return False


def acquire_slot(lock_dir: str):
    os.makedirs(lock_dir, exist_ok=True)
    for i in range(MAX_RUNS):
        fh = open(os.path.join(lock_dir, f"slot{i}.lock"), "a+")
        if _try_lock(fh):
            return fh
        fh.close()
    return None


def _env(work: str) -> dict[str, str]:
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "HOME": work}
    for key in ("XDG_RUNTIME_DIR", "SYSTEMROOT"):  # systemd --user; Python on Windows
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def run_semgrep(files: dict[str, bytes], semgrep_cmd: list[str], wrap: bool,
                timeout: float, tmp_root: str | None = None) -> dict:
    work = tempfile.mkdtemp(prefix="cca-", dir=tmp_root)
    try:
        for name, data in files.items():
            with open(os.path.join(work, name), "wb") as fh:
                fh.write(data)
        target = next(n for n in files if n != "rules.yaml")
        try:
            proc = subprocess.run(semgrep_argv(semgrep_cmd, target, wrap), cwd=work,
                                  env=_env(work), capture_output=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise Refused(f"semgrep did not finish within {timeout:g}s") from None
        except OSError as exc:
            raise Refused(f"semgrep could not start ({exc})") from None
        try:
            return json.loads(proc.stdout.decode("utf-8", "replace"))
        except ValueError:
            raise Refused(f"semgrep exited {proc.returncode} without JSON") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def semgrep_version(semgrep_cmd: list[str], timeout: float) -> str:
    try:
        proc = subprocess.run([*semgrep_cmd, "--version"], capture_output=True, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise Refused(f"semgrep --version failed ({exc})") from None
    text = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not text:
        raise Refused(f"semgrep --version exited {proc.returncode}")
    return text.splitlines()[-1]


def serve(data: bytes, semgrep_cmd: list[str], wrap: bool, lock_dir: str,
          timeout: float, tmp_root: str | None = None) -> dict:
    try:
        req, files = read_request(data)
        if req["op"] == "version":
            return {"ok": True, "semgrep": semgrep_version(semgrep_cmd, timeout)}
        slot = acquire_slot(lock_dir)
        if slot is None:
            raise Refused(f"{MAX_RUNS} runs already in progress")
        try:
            return {"ok": True, "result": run_semgrep(files, semgrep_cmd, wrap, timeout, tmp_root)}
        finally:
            slot.close()
    except Refused as exc:
        return {"ok": False, "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="cca-analyzer runner (forced command)")
    p.add_argument("--semgrep", nargs="+", default=DEFAULT_SEMGREP)
    p.add_argument("--no-limits", action="store_true")
    p.add_argument("--lock-dir", default=DEFAULT_LOCK_DIR)
    p.add_argument("--timeout", type=float, default=TIMEOUT_S)
    p.add_argument("--tmp-root", default=None)
    a = p.parse_args(argv)
    data = sys.stdin.buffer.read(STDIN_CAP + 1)
    out = serve(data, a.semgrep, not a.no_limits, a.lock_dir, a.timeout, a.tmp_root)
    sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
