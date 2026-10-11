#!/usr/bin/python3 -I
"""cca-analyzer runner: the only program the remote analyzer key may start.

Installed on the host as /home/cca-analyzer/bin/cca-analyzer-runner and named in that
account's authorized_keys as `restrict,command="..."`, so sshd runs it whatever the client
asks; the client's own command line never reaches it. It reads ONE tar archive on stdin:
`request.json` ({"protocol": 1, "op": "version" | "semgrep"}) and, for `semgrep`, exactly
`rules.yaml` and one `target.py` or `target.rs`. It answers with one JSON document:
{"ok": true, ...} or {"ok": false, "error": "..."}.

What it refuses, before anything runs: an unknown protocol or operation; any member that
is not a plain regular file (sparse members included: reading one fills its holes up to a
declared size, so a 2.5 KB archive could make this process allocate gigabytes); any member
name outside the fixed set (so no path, no `..`, no other extension); a missing or extra
member; more than 5 MB, judged from each header BEFORE its data is read; a request that
does not arrive within the read deadline; a third concurrent run, the version probe
included. Malformed input is answered with a refusal, never a traceback.

The audited file is untrusted: it is unpacked into a fresh private folder and scanned by
semgrep under a memory and CPU cap (a systemd user scope) and a coreutils `timeout` that
signals the whole process group, so semgrep-core cannot outlive its run. The folder is
removed whatever happens. The version probe goes through the same scope, so a host where
the scope cannot start reports semgrep unavailable instead of claiming a remote that
cannot scan.

Standard library only, and nothing imported from cca_checks: this file is copied alone.
The shebang's `-I` keeps this file's folder off sys.path and ignores PYTHON* variables.
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
import threading

PROTOCOL = 1
MAX_BYTES = 5 * 1024 * 1024
STDIN_CAP = MAX_BYTES + 64 * 1024  # file content plus tar headers
TARGET_EXTS = ("py", "rs")
ALLOWED_NAMES = frozenset({"request.json", "rules.yaml", *(f"target.{e}" for e in TARGET_EXTS)})
PLAIN_FILE = (tarfile.REGTYPE, tarfile.AREGTYPE)
MAX_RUNS = 2
TIMEOUT_S = 120.0
KILL_AFTER_S = 5
READ_DEADLINE_S = 30.0
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
    members: dict[str, bytes] = {}
    budget = MAX_BYTES
    try:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as archive:
            for m in archive:
                if m.type not in PLAIN_FILE or m.issparse():
                    raise Refused(f"member {m.name!r} is not a regular file")
                if m.name not in ALLOWED_NAMES:
                    raise Refused(f"member name {m.name!r} is not allowed")
                if m.name in members:
                    raise Refused(f"duplicate member {m.name!r}")
                if m.size > budget:  # judged from the header, before any data is read
                    raise Refused("request larger than 5 MB")
                budget -= m.size
                fh = archive.extractfile(m)
                members[m.name] = fh.read(m.size) if fh is not None else b""
    except tarfile.TarError as exc:
        raise Refused(f"not a well-formed tar archive ({exc})") from None
    if "request.json" not in members:
        raise Refused("missing request.json")
    try:
        req = json.loads(members.pop("request.json"))
    except (ValueError, RecursionError):
        raise Refused("request.json is not usable JSON") from None
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


def _timeout_argv(timeout: float) -> list[str]:
    # coreutils timeout signals the whole process group (no --foreground), so the
    # semgrep-core child dies with pysemgrep instead of outliving the run.
    return ["timeout", "-k", str(KILL_AFTER_S), f"{timeout:g}"]


def semgrep_argv(semgrep_cmd: list[str], target: str, wrap: bool,
                 timeout: float = TIMEOUT_S) -> list[str]:
    limits = [*LIMITS, *_timeout_argv(timeout)] if wrap else []
    return [*limits, *semgrep_cmd, "--config", "rules.yaml", *SEMGREP_FLAGS, target]


def version_argv(semgrep_cmd: list[str], wrap: bool, timeout: float = TIMEOUT_S) -> list[str]:
    # Through the same scope as a scan: a host where the scope cannot start must fail the
    # probe, or capabilities would claim a remote that cannot scan.
    limits = [*LIMITS, *_timeout_argv(timeout)] if wrap else []
    return [*limits, *semgrep_cmd, "--version"]


def _try_lock(fh) -> bool:
    fh.seek(0)
    # A sys.platform test, not try/import: the type checker narrows on it, so each branch
    # is checked only for the platform it runs on.
    if sys.platform == "win32":  # where the tests run; the host is Linux
        import msvcrt
        try:
            msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
            return True
        except OSError:
            return False
    import fcntl
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
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    getuid = getattr(os, "getuid", None)
    if not runtime and getuid is not None:
        # A forced-command session may lack it; linger keeps the user manager here.
        runtime = f"/run/user/{getuid()}"
    if runtime:
        env["XDG_RUNTIME_DIR"] = runtime
    if os.environ.get("SYSTEMROOT"):  # Python on Windows, where the tests run
        env["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    return env


def _run(argv: list[str], cwd: str, timeout: float) -> subprocess.CompletedProcess:
    # The coreutils timeout inside argv is the real bound; this one is a backstop.
    try:
        return subprocess.run(argv, cwd=cwd, env=_env(cwd), capture_output=True,
                              timeout=timeout + KILL_AFTER_S + 10)
    except subprocess.TimeoutExpired:
        raise Refused(f"semgrep did not finish within {timeout:g}s") from None
    except OSError as exc:
        raise Refused(f"semgrep could not start ({exc})") from None


def run_semgrep(files: dict[str, bytes], semgrep_cmd: list[str], wrap: bool,
                timeout: float, tmp_root: str | None = None) -> dict:
    work = tempfile.mkdtemp(prefix="cca-", dir=tmp_root)
    try:
        for name, data in files.items():
            with open(os.path.join(work, name), "wb") as fh:
                fh.write(data)
        target = next(n for n in files if n != "rules.yaml")
        proc = _run(semgrep_argv(semgrep_cmd, target, wrap, timeout), work, timeout)
        try:
            return json.loads(proc.stdout.decode("utf-8", "replace"))
        except ValueError:
            raise Refused(f"semgrep exited {proc.returncode} without JSON") from None
    finally:
        shutil.rmtree(work, ignore_errors=True)


def semgrep_version(semgrep_cmd: list[str], wrap: bool, timeout: float,
                    tmp_root: str | None = None) -> str:
    work = tempfile.mkdtemp(prefix="cca-", dir=tmp_root)
    try:
        proc = _run(version_argv(semgrep_cmd, wrap, timeout), work, timeout)
    finally:
        shutil.rmtree(work, ignore_errors=True)
    text = proc.stdout.decode("utf-8", "replace").strip()
    if proc.returncode != 0 or not text:
        raise Refused(f"semgrep --version exited {proc.returncode}")
    return text.splitlines()[-1]


def serve(data: bytes, semgrep_cmd: list[str], wrap: bool, lock_dir: str,
          timeout: float, tmp_root: str | None = None) -> dict:
    try:
        req, files = read_request(data)
        slot = acquire_slot(lock_dir)
        if slot is None:
            raise Refused(f"{MAX_RUNS} runs already in progress")
        try:
            if req["op"] == "version":
                return {"ok": True,
                        "semgrep": semgrep_version(semgrep_cmd, wrap, timeout, tmp_root)}
            return {"ok": True, "result": run_semgrep(files, semgrep_cmd, wrap, timeout, tmp_root)}
        finally:
            slot.close()
    except Refused as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:  # last resort: the contract is one JSON answer, always
        return {"ok": False, "error": f"internal error ({exc.__class__.__name__})"}


def _read_stdin(deadline: float) -> bytes | None:
    """At most STDIN_CAP + 1 bytes of stdin, or None if none arrived within `deadline`."""
    box: list[bytes] = []
    reader = threading.Thread(target=lambda: box.append(sys.stdin.buffer.read(STDIN_CAP + 1)),
                              daemon=True)
    reader.start()
    reader.join(deadline)
    return box[0] if box else None


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="cca-analyzer runner (forced command)")
    p.add_argument("--semgrep", nargs="+", default=DEFAULT_SEMGREP)
    p.add_argument("--no-limits", action="store_true")
    p.add_argument("--lock-dir", default=DEFAULT_LOCK_DIR)
    p.add_argument("--timeout", type=float, default=TIMEOUT_S)
    p.add_argument("--tmp-root", default=None)
    p.add_argument("--read-deadline", type=float, default=READ_DEADLINE_S)
    a = p.parse_args(argv)
    data = _read_stdin(a.read_deadline)
    if data is None:
        out = {"ok": False, "error": f"no request within {a.read_deadline:g}s"}
    else:
        out = serve(data, a.semgrep, not a.no_limits, a.lock_dir, a.timeout, a.tmp_root)
    sys.stdout.write(json.dumps(out))
    sys.stdout.flush()
    return 0


if __name__ == "__main__":
    # os._exit, not SystemExit: a reader thread still blocked on a silent client must
    # not keep the process (and the sshd session) alive.
    os._exit(main())
