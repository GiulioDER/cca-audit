# Remote semgrep Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** When semgrep cannot run locally and `CCA_REMOTE_SEMGREP` names an ssh destination, settle taint claims by running semgrep on that host through a forced-command runner, and report honestly where it ran or why it could not.

**Architecture:** A stdlib-only runner (`cca_checks/remote/runner.py`) installed on the host as the only command its key may run: it reads a tar archive on stdin, refuses anything but a `version` request or exactly `rules.yaml` plus one `target.py|rs`, runs semgrep with fixed flags under limits, and answers with one JSON envelope. A client module (`cca_checks/remote/__init__.py`) builds that archive and calls ssh. `run_semgrep` uses it only when the local execution probe fails; `capabilities` reports it.

**Tech Stack:** Python 3.10+ stdlib (`tarfile`, `subprocess`, `json`, `fcntl`/`msvcrt`), OpenSSH client, semgrep 1.178.0 on the host, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-remote-semgrep-design.md`

## Global Constraints

- Only semgrep goes remote. No other tool, runner or code path gains a remote hook.
- The setting is `CCA_REMOTE_SEMGREP`, environment only, matching `^[A-Za-z0-9._-]+(@[A-Za-z0-9._-]+)?$`; anything else is reported invalid and ignored.
- Nothing from the audited repository appears in any command line, local or remote. Files travel in the tar archive under the fixed names `request.json`, `rules.yaml`, `target.py` or `target.rs`.
- Archive limit 5 MB (`5 * 1024 * 1024` bytes of file content); extensions `py` and `rs` only; protocol `1`; operations `version` and `semgrep` only.
- Remote semgrep flags are exactly the local ones: `--config rules.yaml --json --metrics=off --disable-version-check --quiet --disable-nosem --no-git-ignore target.<ext>`.
- Host limits: `systemd-run --user --scope --quiet --collect -p MemoryMax=2G -p MemorySwapMax=0 -p CPUQuota=100% nice -n 15`, 120 s timeout, at most 2 concurrent runs (a third is refused, not queued).
- ssh runs as `ssh -T -o BatchMode=yes -o ConnectTimeout=10 <dest>`, resolved through `toolpath.resolve_tool`.
- Local first: with local semgrep runnable, the remote is never contacted.
- Every remote failure ends as UNCERTAIN with its reason; never FALSE_POSITIVE.
- Pinned host semgrep: `1.178.0` (released 2026-09-23).
- The runner imports nothing from `cca_checks`: it is copied to the host alone.
- Every behaviour test is shown red under a named mutation before it is trusted green, receipt in its docstring (`Red proof: <mutation> -> <failure>`). Run tests as `python -m pytest ...` from the worktree root.
- No dash used as punctuation in prose, comments or commit messages.

## Review Focus

1. ssh would prompt (a key with a passphrase and no agent, an unknown host): the run must fail fast, never hang waiting for input. Pinned in Task 2 by asserting `BatchMode=yes` and `ConnectTimeout=10` in the argv.
2. `CCA_REMOTE_SEMGREP` is set but local semgrep works: no byte leaves the machine. Pinned in Task 3.
3. An audited file named `X.PY`: the extension is lowercased before the allowlist check. Pinned in Task 3.
4. A `request.json` that is valid JSON but not an object (a list, a string): refused, never a crash. Pinned in Task 1 as the `json-not-object` refusal case.
5. The developer has `CCA_REMOTE_SEMGREP` set while running the existing suite: existing semgrep tests must not start calling a remote. Pinned in Task 2 by an autouse fixture in `tests/conftest.py`.

---

### Task 1: The runner

**Files:**
- Create: `cca_checks/remote/runner.py`
- Create: `tests/remote_testkit.py` (helpers, not collected)
- Test: `tests/test_remote_runner.py`

**Interfaces:**
- Produces: `runner.PROTOCOL = 1`, `runner.MAX_BYTES`, `runner.TARGET_EXTS = ("py", "rs")`, `runner.MAX_RUNS = 2`, `runner.SEMGREP_FLAGS`, `runner.LIMITS`; `runner.Refused(Exception)`; `runner.read_request(data: bytes) -> tuple[dict, dict[str, bytes]]`; `runner.semgrep_argv(semgrep_cmd: list[str], target: str, wrap: bool) -> list[str]`; `runner.acquire_slot(lock_dir: str)` returning an open file or `None`; `runner.serve(data: bytes, semgrep_cmd: list[str], wrap: bool, lock_dir: str, timeout: float, tmp_root: str | None = None) -> dict`; `runner.main(argv: list[str] | None = None) -> int`.
- Test kit produces: `tar_of(entries: list[tuple[str, bytes, str]]) -> bytes` (kind `file`, `symlink`, `hardlink`), `request(op: str, files: dict[str, bytes] | None = None, protocol: int = 1) -> bytes`, `fake_semgrep(tmp_path, mode: str = "ok") -> tuple[list[str], pathlib.Path]` (command list and marker path; modes `ok`, `crash`, `sleep`).

- [ ] **Step 1: Write the test kit**

`tests/remote_testkit.py`:

```python
"""Helpers for the remote semgrep tests: archives and a fake semgrep. Not collected."""
from __future__ import annotations

import io
import json
import pathlib
import sys
import tarfile

FAKE = r'''
import json, pathlib, sys, time
marker = pathlib.Path({marker!r})
marker.write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
if "--version" in sys.argv:
    print("9.9.9")
    sys.exit(0)
if {mode!r} == "crash":
    sys.exit(2)
if {mode!r} == "sleep":
    time.sleep(30)
print(json.dumps({{"version": "9.9.9", "results": [], "errors": [],
                  "paths": {{"scanned": [sys.argv[-1]]}}}}))
'''


def tar_of(entries: list[tuple[str, bytes, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tf.addfile(info)
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, "rules.yaml"
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def request(op: str, files: dict[str, bytes] | None = None, protocol: int = 1) -> bytes:
    head = json.dumps({"protocol": protocol, "op": op}).encode()
    return tar_of([("request.json", head, "file"),
                   *[(n, d, "file") for n, d in (files or {}).items()]])


def fake_semgrep(tmp_path: pathlib.Path, mode: str = "ok") -> tuple[list[str], pathlib.Path]:
    marker = tmp_path / f"semgrep-ran-{mode}.json"
    script = tmp_path / f"fake_semgrep_{mode}.py"
    script.write_text(FAKE.format(marker=str(marker), mode=mode), encoding="utf-8")
    return [sys.executable, str(script)], marker
```

- [ ] **Step 2: Write the failing tests**

`tests/test_remote_runner.py`:

```python
"""Tests for cca_checks.remote.runner, the only program the remote key may start."""
import json
import subprocess
import sys

import pytest
from remote_testkit import fake_semgrep, request, tar_of

from cca_checks.remote import runner

RULES = b"rules: []\n"
OK_FILES = {"rules.yaml": RULES, "target.py": b"def f():\n    return 1\n"}


def _serve(tmp_path, data, mode="ok", timeout=20.0):
    cmd, marker = fake_semgrep(tmp_path, mode)
    (tmp_path / "work").mkdir(exist_ok=True)
    out = runner.serve(data, cmd, wrap=False, lock_dir=str(tmp_path / "locks"),
                       timeout=timeout, tmp_root=str(tmp_path / "work"))
    return out, marker


REFUSALS = {
    # id: (archive, mutation that turns it red)
    "symlink": (lambda: tar_of([("request.json", json.dumps({"protocol": 1, "op": "semgrep"}).encode(), "file"),
                                ("rules.yaml", RULES, "file"), ("target.py", b"", "symlink")]),
                "read_request: `if not m.isreg():` -> `if False:`"),
    "hardlink": (lambda: tar_of([("request.json", json.dumps({"protocol": 1, "op": "semgrep"}).encode(), "file"),
                                 ("rules.yaml", RULES, "file"), ("target.py", b"", "hardlink")]),
                 "read_request: `if not m.isreg():` -> `if False:`"),
    "dotdot": (lambda: request("semgrep", {"rules.yaml": RULES, "../target.py": b"x = 1\n"}),
               "read_request: `if name not in ALLOWED_NAMES:` -> `if False:`"),
    "absolute": (lambda: request("semgrep", {"rules.yaml": RULES, "/target.py": b"x = 1\n"}),
                 "read_request: `if name not in ALLOWED_NAMES:` -> `if False:`"),
    "wrong-ext": (lambda: request("semgrep", {"rules.yaml": RULES, "target.js": b"x = 1\n"}),
                  "read_request: `if name not in ALLOWED_NAMES:` -> `if False:`"),
    "extra": (lambda: request("semgrep", {**OK_FILES, "target.rs": b"fn f() {}\n"}),
              "read_request: `len(targets) != 1` -> `len(targets) < 1`"),
    "missing": (lambda: request("semgrep", {"rules.yaml": RULES}),
                "read_request: `len(targets) != 1` -> `len(targets) > 1`"),
    "oversize": (lambda: request("semgrep", {"rules.yaml": RULES, "target.py": b"#" * (5 * 1024 * 1024)}),
                 "read_request: `if total > MAX_BYTES:` -> `if False:`"),
    "unknown-op": (lambda: request("shell", OK_FILES),
                   "read_request: `if op != \"semgrep\":` -> `if op == \"version\":`"),
    "protocol": (lambda: request("semgrep", OK_FILES, protocol=2),
                 "read_request: `!= PROTOCOL` -> `== -1`"),
    "not-a-tar": (lambda: b"this is not an archive",
                  "read_request: drop the tarfile.TarError handler -> ReadError in the call"),
    # Review Focus 4
    "json-not-object": (lambda: tar_of([("request.json", b"[1, 2]", "file")]),
                        "read_request: drop `not isinstance(req, dict) or` -> AttributeError in the call"),
}


@pytest.mark.parametrize("case", sorted(REFUSALS))
def test_a_bad_request_is_refused_before_anything_runs(tmp_path, case):
    """Red proof: each case's mutation in REFUSALS -> semgrep runs (marker written) or the call raises."""
    out, marker = _serve(tmp_path, REFUSALS[case][0]())
    assert out["ok"] is False and out["error"]
    assert not marker.exists()
    assert list((tmp_path / "work").iterdir()) == []


def test_the_scan_uses_exactly_the_local_flags(tmp_path):
    """Red proof: drop "--disable-nosem" from SEMGREP_FLAGS -> the recorded argv differs."""
    out, marker = _serve(tmp_path, request("semgrep", OK_FILES))
    assert out["ok"] is True
    assert out["result"]["paths"]["scanned"] == ["target.py"]
    assert json.loads(marker.read_text(encoding="utf-8")) == [
        "--config", "rules.yaml", "--json", "--metrics=off", "--disable-version-check",
        "--quiet", "--disable-nosem", "--no-git-ignore", "target.py"]


def test_the_limits_wrap_semgrep(tmp_path):
    """Red proof: drop "-p", "MemoryMax=2G" from LIMITS -> the argv lacks the memory cap."""
    argv = runner.semgrep_argv(["/opt/semgrep"], "target.rs", wrap=True)
    assert argv[:3] == ["systemd-run", "--user", "--scope"]
    assert "MemoryMax=2G" in argv and "MemorySwapMax=0" in argv and "CPUQuota=100%" in argv
    assert argv[argv.index("nice"):argv.index("nice") + 3] == ["nice", "-n", "15"]
    assert argv[-1] == "target.rs"
    assert runner.semgrep_argv(["/opt/semgrep"], "target.rs", wrap=False)[0] == "/opt/semgrep"


@pytest.mark.parametrize("mode", ["ok", "crash", "sleep"])
def test_the_work_folder_is_removed_whatever_happens(tmp_path, mode):
    """Red proof: drop the shutil.rmtree in run_semgrep's finally -> a cca- folder is left."""
    out, _ = _serve(tmp_path, request("semgrep", OK_FILES), mode=mode, timeout=3.0)
    assert out["ok"] is (mode == "ok")
    assert list((tmp_path / "work").iterdir()) == []


def test_a_third_concurrent_run_is_refused(tmp_path):
    """Red proof: acquire_slot loops over MAX_RUNS + 1 slots -> the third run proceeds."""
    held = [runner.acquire_slot(str(tmp_path / "locks")) for _ in range(runner.MAX_RUNS)]
    assert all(h is not None for h in held)
    try:
        out, marker = _serve(tmp_path, request("semgrep", OK_FILES))
        assert out["ok"] is False and "in progress" in out["error"]
        assert not marker.exists()
    finally:
        for h in held:
            h.close()


def test_version_answers_and_carries_no_files(tmp_path):
    """Red proof: read_request accepts files on a version request -> the second assertion fails."""
    out, _ = _serve(tmp_path, request("version"))
    assert out == {"ok": True, "semgrep": "9.9.9"}
    out, _ = _serve(tmp_path, request("version", {"rules.yaml": RULES}))
    assert out["ok"] is False


def test_main_answers_one_json_document_on_stdout(tmp_path):
    """Red proof: main prints the envelope with print(out) (a Python repr) -> json.loads fails."""
    cmd, marker = fake_semgrep(tmp_path)
    (tmp_path / "work").mkdir()
    proc = subprocess.run([sys.executable, runner.__file__, "--semgrep", *cmd, "--no-limits",
                           "--lock-dir", str(tmp_path / "locks"), "--tmp-root", str(tmp_path / "work")],
                          input=request("version"), capture_output=True, timeout=60)
    assert proc.returncode == 0
    assert json.loads(proc.stdout) == {"ok": True, "semgrep": "9.9.9"}
```

- [ ] **Step 3: Run them and see them fail**

Run: `python -m pytest tests/test_remote_runner.py -q`
Expected: collection error, `No module named 'cca_checks.remote'`.

- [ ] **Step 4: Write the runner**

`cca_checks/remote/runner.py`:

```python
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
```

Also create `cca_checks/remote/__init__.py` with only a docstring for now (Task 2 fills it):

```python
"""Run semgrep on an operator-configured remote host. See runner.py for the host side."""
```

- [ ] **Step 5: Run them green**

Run: `python -m pytest tests/test_remote_runner.py -q`
Expected: `20 passed` (12 refusal cases, 1 flags, 1 limits, 3 cleanup cases, 1 concurrency, 1 version, 1 main).

- [ ] **Step 6: Prove each test red**

Apply each named mutation to `cca_checks/remote/runner.py`, run that test (for the parametrized refusals, that case's id), confirm it fails in its assertion or in the call as the docstring states, revert. Run the file green again. Under the `absolute` case's mutation the runner tries to write `/target.py`; on Windows that is the drive root and normally fails with a permission error (a red in the call). If it succeeds, delete the file before going on.

- [ ] **Step 7: Lint and commit**

```bash
ruff check cca_checks tests
git add cca_checks/remote/__init__.py cca_checks/remote/runner.py tests/remote_testkit.py tests/test_remote_runner.py
git commit -m "feat(remote): the forced-command runner that runs semgrep on a remote host" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: The client transport, and keeping the existing suite local

**Files:**
- Modify: `cca_checks/remote/__init__.py`
- Modify: `tests/conftest.py` (append one autouse fixture)
- Test: `tests/test_remote_client.py`

**Interfaces:**
- Consumes: `runner.PROTOCOL`, `runner.MAX_BYTES`, `runner.TARGET_EXTS` (Task 1); `toolpath.resolve_tool`; `config.TIMEOUT_S`.
- Produces: `remote.ENV = "CCA_REMOTE_SEMGREP"`; `remote.destination() -> tuple[str | None, str | None]` (dest, invalid reason); `remote.build_request(op: str, files: dict[str, bytes] | None = None) -> bytes`; `remote._ssh_argv(dest: str) -> list[str] | None`; `remote._call(dest: str, payload: bytes, timeout: float) -> tuple[dict | None, str | None]`; `remote.remote_semgrep_version(dest: str) -> tuple[str | None, str | None]` (cached with `functools.cache`); `remote.remote_semgrep(dest: str, rules: bytes, target: bytes, ext: str) -> tuple[dict | None, str | None]`; `remote.semgrep_status(local_reason: str) -> tuple[str | None, str | None]` (unavailable reason, serving description).

- [ ] **Step 1: Write the failing tests**

`tests/test_remote_client.py`:

```python
"""Tests for cca_checks.remote: the setting, the archive, and the ssh call."""
import pathlib
import sys

import pytest
from remote_testkit import fake_semgrep

from cca_checks import remote

RUNNER = pathlib.Path(remote.__file__).with_name("runner.py")
OK = (b"rules: []\n", b"def f():\n    return 1\n")


@pytest.fixture
def via_local_runner(tmp_path, monkeypatch):
    """ssh replaced by a direct call to the real runner, with a fake semgrep behind it."""
    def install(mode="ok"):
        cmd, marker = fake_semgrep(tmp_path, mode)
        (tmp_path / "work").mkdir(exist_ok=True)
        argv = [sys.executable, str(RUNNER), "--semgrep", *cmd, "--no-limits",
                "--lock-dir", str(tmp_path / "locks"), "--tmp-root", str(tmp_path / "work"),
                "--timeout", "5"]
        monkeypatch.setattr(remote, "_ssh_argv", lambda dest: argv)
        remote.remote_semgrep_version.cache_clear()
        return marker
    yield install
    remote.remote_semgrep_version.cache_clear()


@pytest.mark.parametrize("value,dest", [
    ("cca-analyzer", "cca-analyzer"), ("cca-analyzer@host.example", "cca-analyzer@host.example"),
    ("", None), ("-oProxyCommand=calc", None), ("user@host extra", None), ("a;b", None),
])
def test_the_destination_is_a_plain_ssh_name(monkeypatch, value, dest):
    """Red proof: destination() skips the _DEST fullmatch -> '-oProxyCommand=calc' is accepted."""
    monkeypatch.setenv(remote.ENV, value)
    got, invalid = remote.destination()
    assert got == dest
    assert (invalid is not None) == (value != "" and dest is None)


def test_ssh_never_prompts(monkeypatch):
    """Review Focus 1. Red proof: drop "BatchMode=yes" from _ssh_argv -> the assertion fails."""
    monkeypatch.setattr(remote, "resolve_tool", lambda name: "/usr/bin/ssh")
    argv = remote._ssh_argv("cca-analyzer")
    assert argv == ["/usr/bin/ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10",
                    "cca-analyzer"]


def test_a_round_trip_returns_semgrep_json(via_local_runner):
    """Red proof: remote_semgrep returns doc instead of doc["result"] -> no "paths" key."""
    marker = via_local_runner()
    data, why = remote.remote_semgrep("cca-analyzer", *OK, "py")
    assert why is None
    assert data["paths"]["scanned"] == ["target.py"] and data["version"] == "9.9.9"
    assert marker.exists()


def test_the_version_probe(via_local_runner):
    """Red proof: remote_semgrep_version reads doc["version"] -> None."""
    via_local_runner()
    assert remote.remote_semgrep_version("cca-analyzer") == ("9.9.9", None)


def test_a_crash_on_the_host_is_a_reason_not_a_result(via_local_runner):
    """Red proof: _call ignores doc["ok"] -> a refusal envelope comes back as data."""
    via_local_runner("crash")
    data, why = remote.remote_semgrep("cca-analyzer", *OK, "py")
    assert data is None and "without JSON" in why


@pytest.mark.parametrize("argv,expect", [
    (None, "ssh is not on PATH"),
    ([sys.executable, "-c", "print('hello')"], "no runner answer"),
    ([sys.executable, "-c", "import time; time.sleep(10)"], "no answer within"),
    (["Z:/no/such/ssh.exe"], "ssh could not start"),
])
def test_every_transport_failure_is_none_with_a_reason(monkeypatch, argv, expect):
    """Red proofs, one per case: no ssh -> _call drops its `argv is None` check (TypeError in the
    call); hello -> _call returns (None, "") on a JSON error (expect not in reason); sleep -> _call
    lets subprocess.TimeoutExpired propagate (raises); missing exe -> _call drops its OSError
    handler (raises)."""
    monkeypatch.setattr(remote, "_ssh_argv", lambda dest: argv)
    data, why = remote._call("cca-analyzer", remote.build_request("version"), timeout=2)
    assert data is None and expect in why


@pytest.mark.parametrize("ext,target", [("js", b"x"), ("py", b"#" * (5 * 1024 * 1024))])
def test_refused_before_anything_is_sent(monkeypatch, ext, target):
    """Red proof: remote_semgrep skips its size check -> _call is reached for the 5 MB case."""
    sent = []
    monkeypatch.setattr(remote, "_call", lambda *a, **k: sent.append(a) or ({}, None))
    data, why = remote.remote_semgrep("cca-analyzer", b"rules: []\n", target, ext)
    assert data is None and why and sent == []


def test_semgrep_status(monkeypatch):
    """Red proof: semgrep_status returns local_reason when the remote answers -> taint stays unavailable."""
    monkeypatch.setenv(remote.ENV, "cca-analyzer")
    monkeypatch.setattr(remote, "remote_semgrep_version", lambda dest: ("1.178.0", None))
    assert remote.semgrep_status("blocked") == (None, "semgrep 1.178.0 on cca-analyzer")
    monkeypatch.setattr(remote, "remote_semgrep_version", lambda dest: (None, "host key not known"))
    assert remote.semgrep_status("blocked") == (
        "local: blocked; remote cca-analyzer: host key not known", None)
    monkeypatch.delenv(remote.ENV)
    assert remote.semgrep_status("blocked") == ("blocked", None)
    monkeypatch.setenv(remote.ENV, "-oProxyCommand=x")
    reason, desc = remote.semgrep_status("blocked")
    assert reason.startswith("blocked; ") and "not a plain ssh destination" in reason and desc is None


def test_the_suite_runs_without_a_remote_by_default():
    """Review Focus 5. Red proof: remove the autouse fixture from conftest.py -> fails when the developer exported CCA_REMOTE_SEMGREP."""
    import os
    assert os.environ.get(remote.ENV) is None or os.environ.get("CCA_TESTS_USE_REMOTE") == "1"
```

- [ ] **Step 2: Append the isolation fixture to `tests/conftest.py`**

```python
@pytest.fixture(autouse=True)
def _no_remote_semgrep_unless_asked(monkeypatch):
    """Keep the suite local even when the developer exports CCA_REMOTE_SEMGREP.

    The existing semgrep tests stub subprocess.run globally; with a remote configured
    they would route through ssh instead and test the wrong thing. CCA_TESTS_USE_REMOTE=1
    keeps the variable, for a deliberate live run against a real host.
    """
    if os.environ.get("CCA_TESTS_USE_REMOTE") != "1":
        monkeypatch.delenv("CCA_REMOTE_SEMGREP", raising=False)
```

(`os` and `pytest` are already imported in `tests/conftest.py`.)

To make the red proof of `test_the_suite_runs_without_a_remote_by_default` meaningful, run it once with `CCA_REMOTE_SEMGREP=cca-analyzer` exported in the shell and the fixture removed: it must fail; then restore the fixture and see it pass with the variable still exported.

- [ ] **Step 3: Run them and see them fail**

Run: `python -m pytest tests/test_remote_client.py -q`
Expected: failures with `AttributeError: module 'cca_checks.remote' has no attribute ...` (not a red proof; Step 6 gives those).

- [ ] **Step 4: Write the client**

Replace `cca_checks/remote/__init__.py` with:

```python
"""Run semgrep on an operator-configured remote host, for machines that cannot run it.

The host side is runner.py, installed as the only command a dedicated key may start.
This side builds the tar archive the runner reads and calls ssh. Two operations exist,
the version probe and a semgrep scan, and nothing here can send anything else: no
function takes a command, and the archive's member names are fixed.

The destination comes from CCA_REMOTE_SEMGREP in the environment, never from the
repository under audit, and must be a plain ssh name: a value such as
"-oProxyCommand=..." would otherwise be read by ssh as an option.
"""
from __future__ import annotations

import functools
import io
import json
import os
import re
import subprocess
import tarfile

from ..config import TIMEOUT_S
from ..toolpath import PROBE_TIMEOUT_S, resolve_tool
from .runner import MAX_BYTES, PROTOCOL, TARGET_EXTS

ENV = "CCA_REMOTE_SEMGREP"
_DEST = re.compile(r"[A-Za-z0-9._-]+(@[A-Za-z0-9._-]+)?")
_MARGIN_S = 30


def destination() -> tuple[str | None, str | None]:
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return None, None
    if not _DEST.fullmatch(raw):
        return None, f"{ENV}={raw!r} is not a plain ssh destination (alias or user@host); ignored"
    return raw, None


def build_request(op: str, files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        entries = {"request.json": json.dumps({"protocol": PROTOCOL, "op": op}).encode()}
        entries.update(files or {})
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _ssh_argv(dest: str) -> list[str] | None:
    ssh = resolve_tool("ssh")
    if ssh is None:
        return None
    return [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", dest]


def _call(dest: str, payload: bytes, timeout: float) -> tuple[dict | None, str | None]:
    argv = _ssh_argv(dest)
    if argv is None:
        return None, "ssh is not on PATH"
    try:
        proc = subprocess.run(argv, input=payload, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"no answer within {timeout:g}s"
    except OSError as exc:
        return None, f"ssh could not start ({exc})"
    try:
        doc = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        return None, f"no runner answer (ssh exit {proc.returncode}: {lines[-1] if lines else 'no output'})"
    if not isinstance(doc, dict):
        return None, "malformed runner answer"
    if not doc.get("ok"):
        return None, f"runner refused: {doc.get('error')}"
    return doc, None


@functools.cache
def remote_semgrep_version(dest: str) -> tuple[str | None, str | None]:
    doc, why = _call(dest, build_request("version"), PROBE_TIMEOUT_S + _MARGIN_S)
    if doc is None:
        return None, why
    version = doc.get("semgrep")
    return (str(version), None) if version else (None, "runner gave no version")


def remote_semgrep(dest: str, rules: bytes, target: bytes,
                   ext: str) -> tuple[dict | None, str | None]:
    if ext not in TARGET_EXTS:
        return None, f"only .py and .rs files are sent to {dest}, not .{ext}"
    if len(rules) + len(target) > MAX_BYTES:
        return None, "larger than 5 MB; not sent"
    files = {"rules.yaml": rules, f"target.{ext}": target}
    doc, why = _call(dest, build_request("semgrep", files), TIMEOUT_S + _MARGIN_S)
    if doc is None:
        return None, why
    result = doc.get("result")
    if not isinstance(result, dict):
        return None, "runner answer has no semgrep result"
    return result, None


def semgrep_status(local_reason: str) -> tuple[str | None, str | None]:
    """Given why local semgrep cannot run: (reason taint stays unavailable, or None; what serves it)."""
    dest, invalid = destination()
    if invalid:
        return f"{local_reason}; {invalid}", None
    if dest is None:
        return local_reason, None
    version, why = remote_semgrep_version(dest)
    if version is None:
        return f"local: {local_reason}; remote {dest}: {why}", None
    return None, f"semgrep {version} on {dest}"
```

- [ ] **Step 5: Run them green**

Run: `python -m pytest tests/test_remote_client.py tests/test_remote_runner.py -q`
Expected: all pass.

- [ ] **Step 6: Prove each test red**

Apply each docstring's mutation, run that test alone, confirm the failure, revert. Run both files green.

- [ ] **Step 7: Lint and commit**

```bash
ruff check cca_checks tests
git add cca_checks/remote/__init__.py tests/conftest.py tests/test_remote_client.py
git commit -m "feat(remote): the client side: setting, archive and ssh call" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `run_semgrep` goes remote only when it must, and says so

**Files:**
- Modify: `cca_checks/semgrep_check.py` (imports; `run_semgrep`; `verdict_for_taint` split into an outer function and `_settle`)
- Test: `tests/test_remote_semgrep_check.py`

**Interfaces:**
- Consumes: `remote.destination`, `remote.remote_semgrep` (Task 2); `toolpath.tool_unavailable_reason`.
- Produces: `semgrep_check._results_of(data) -> list | None`; `semgrep_check._last_remote: dict | None` (`{"dest", "version", "error"}`); `verdict_for_taint` unchanged in signature.

- [ ] **Step 1: Write the failing tests**

`tests/test_remote_semgrep_check.py`:

```python
"""Tests for the remote path of cca_checks.semgrep_check."""
from cca_checks import remote
from cca_checks import semgrep_check as sc
from cca_checks.claim import Claim

PAYLOAD = {"version": "1.178.0", "errors": [], "paths": {"scanned": ["target.py"]}, "results": []}


def _remote_only(monkeypatch, answer):
    monkeypatch.setenv(remote.ENV, "cca-analyzer")
    monkeypatch.setattr(sc, "tool_unavailable_reason", lambda name: "blocked here")
    calls = []

    def fake(dest, rules, target, ext):
        calls.append((dest, ext, len(rules) > 0, target))
        return answer
    monkeypatch.setattr(remote, "remote_semgrep", fake)
    return calls


def test_local_first_nothing_leaves_when_local_semgrep_runs(monkeypatch, tmp_path):
    """Review Focus 2. Red proof: run_semgrep skips the tool_unavailable_reason test -> the remote is called."""
    monkeypatch.setenv(remote.ENV, "cca-analyzer")
    monkeypatch.setattr(sc, "tool_unavailable_reason", lambda name: None)
    called = []
    monkeypatch.setattr(remote, "remote_semgrep", lambda *a: called.append(a) or (PAYLOAD, None))
    monkeypatch.setattr(sc, "resolve_tool", lambda name: None)  # local path then returns None
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target))
    assert called == []


def test_remote_results_pass_the_same_validation(monkeypatch, tmp_path):
    """Red proof: _run_remote returns data["results"] without _results_of -> the errors payload is accepted."""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _remote_only(monkeypatch, (PAYLOAD, None))
    assert sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target)) == []
    _remote_only(monkeypatch, ({**PAYLOAD, "errors": [{"message": "parse"}]}, None))
    assert sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target)) is None


def test_an_upper_case_extension_is_lowered(monkeypatch, tmp_path):
    """Review Focus 3. Red proof: _run_remote keeps the extension's case -> ext 'PY' sent."""
    target = tmp_path / "A.PY"
    target.write_text("x = 1\n", encoding="utf-8")
    calls = _remote_only(monkeypatch, (PAYLOAD, None))
    sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target))
    assert calls[0][1] == "py" and calls[0][2] is True and calls[0][3] == b"x = 1\n"


def _claim(tmp_path):
    f = tmp_path / "svc.py"
    f.write_text("def handler(q):\n    cur.execute(q)\n    return 1\n", encoding="utf-8")
    return Claim(finding_id="SEC-001", file=str(f), line=2, claim_type="taint",
                 symbol="", sink_class="sql")


def test_a_remote_verdict_names_its_host(monkeypatch, tmp_path):
    """Red proof: verdict_for_taint drops the remote note -> evidence lacks the host."""
    hit = {"check_id": "rules.sink-strict-sql", "start": {"line": 2}, "extra": {"message": "m"}}
    _remote_only(monkeypatch, ({**PAYLOAD, "results": [hit]}, None))
    monkeypatch.setattr(sc, "enclosing_span", lambda p, line: (1, 3))
    v = sc.verdict_for_taint(_claim(tmp_path))
    assert v.verdict == "UNCERTAIN" and v.source == "semgrep"
    assert v.evidence.endswith("(semgrep 1.178.0 on cca-analyzer)")


def test_a_remote_failure_is_uncertain_with_its_reason(monkeypatch, tmp_path):
    """Red proof: the sinks-is-None branch ignores _last_remote -> the generic message, no reason."""
    _remote_only(monkeypatch, (None, "host key verification failed"))
    v = sc.verdict_for_taint(_claim(tmp_path))
    assert v.verdict == "UNCERTAIN" and v.source == "llm"
    assert "cca-analyzer" in v.evidence and "host key verification failed" in v.evidence
```

Before writing, check `Claim`'s constructor fields in `cca_checks/claim.py` and adjust `_claim` to them if they differ; record any change as a ruling.

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_remote_semgrep_check.py -q`
Expected: failures (`tool_unavailable_reason` is not an attribute of `semgrep_check`, and the evidence lacks the host).

- [ ] **Step 3: Change `semgrep_check.py`**

Imports:

```python
import dataclasses
import json
import os
import subprocess
from importlib import resources

from . import remote
from .claim import Claim, Verdict, make_verdict
from .config import TIMEOUT_S
from .scope import enclosing_span
from .toolpath import resolve_tool, tool_unavailable_reason
```

Below the module constants:

```python
#: What the most recent run_semgrep call did remotely, for the verdict's evidence:
#: None when it ran locally, else {"dest", "version", "error"}.
_last_remote: dict | None = None
```

Replace `run_semgrep` with (the docstring keeps its existing text, plus the paragraph shown):

```python
def run_semgrep(config: str, path: str) -> list[dict] | None:
    """<existing docstring, unchanged>

    REMOTE. When semgrep cannot run on this machine and CCA_REMOTE_SEMGREP names a host,
    the scan runs there instead (cca_checks.remote). Local first: with local semgrep
    runnable, nothing leaves the machine. The remote JSON goes through the same
    validation as a local run.
    """
    global _last_remote
    _last_remote = None
    dest, _invalid = remote.destination()
    if dest is not None and tool_unavailable_reason("semgrep") is not None:
        return _run_remote(config, path, dest)
    exe = resolve_tool("semgrep")
    if exe is None:
        # Missing from PATH, or resolved inside the audited tree (hijack attempt).
        return None
    cmd = [exe, "--config", config, "--json", "--metrics=off",
           "--disable-version-check", "--quiet", "--disable-nosem", "--no-git-ignore",
           os.path.abspath(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    except OSError:  # includes FileNotFoundError
        return None
    if not (proc.stdout or "").strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    return _results_of(data)


def _results_of(data) -> list[dict] | None:
    """semgrep's results, or None unless the run is clean, scanned something, and listed results."""
    try:
        if not isinstance(data, dict) or data.get("errors"):
            return None
        paths = data.get("paths")
        scanned = paths.get("scanned") if isinstance(paths, dict) else None
        if not isinstance(scanned, list) or len(scanned) < 1:
            return None
        results = data.get("results")
        return results if isinstance(results, list) else None
    except Exception:
        return None


def _run_remote(config: str, path: str, dest: str) -> list[dict] | None:
    global _last_remote
    ext = os.path.splitext(path)[1].lstrip(".").lower()
    try:
        with open(config, "rb") as fh:
            rules = fh.read()
        with open(path, "rb") as fh:
            target = fh.read()
    except OSError as exc:
        _last_remote = {"dest": dest, "version": None, "error": f"could not read the files ({exc})"}
        return None
    data, why = remote.remote_semgrep(dest, rules, target, ext)
    if data is None:
        _last_remote = {"dest": dest, "version": None, "error": why}
        return None
    results = _results_of(data)
    if results is None:
        _last_remote = {"dest": dest, "version": None,
                        "error": "semgrep reported errors or scanned nothing"}
        return None
    _last_remote = {"dest": dest, "version": str(data.get("version") or "unknown"), "error": None}
    return results
```

Split `verdict_for_taint`: keep its docstring and the catalog check, then:

```python
    global _last_remote
    _last_remote = None
    remote_used = None
    if sinks is None and taint is None:
        sink_rules, taint_rules = catalog
        sinks = run_semgrep(rules_path(sink_rules), claim.file)
        remote_used = _last_remote
        if sinks is not None:
            taint = run_semgrep(rules_path(taint_rules), claim.file) or []

    if sinks is None:
        if remote_used and remote_used.get("error"):
            return make_verdict(
                claim.finding_id, "UNCERTAIN",
                f"semgrep cannot run here and the remote scan on {remote_used['dest']} "
                f"failed ({remote_used['error']}); falling back to LLM", "llm")
        return make_verdict(claim.finding_id, "UNCERTAIN",
                            "semgrep unavailable; falling back to LLM", "llm")
    if taint is None:
        taint = []

    verdict = _settle(claim, sinks, taint, catalog)
    if remote_used and not remote_used.get("error") and verdict.source == "semgrep":
        verdict = dataclasses.replace(
            verdict,
            evidence=f"{verdict.evidence} (semgrep {remote_used['version']} on {remote_used['dest']})")
    return verdict


def _settle(claim: Claim, sinks: list, taint: list, catalog) -> Verdict:
    <the existing body from `try: lo, hi = enclosing_span(...)` to the final FALSE_POSITIVE return, unchanged>
```

- [ ] **Step 4: Run green, including the existing semgrep tests**

Run: `python -m pytest tests/test_remote_semgrep_check.py tests/test_semgrep_check.py tests/test_selfaudit_hardening.py tests/test_language_guard.py -q`
Expected: the new tests pass; the existing ones keep the pass/fail status they have on `origin/master` on this machine (compare by name).

- [ ] **Step 5: Prove each new test red, lint, commit**

Apply each docstring's mutation, see it fail, revert.

```bash
ruff check cca_checks tests
git add cca_checks/semgrep_check.py tests/test_remote_semgrep_check.py
git commit -m "feat(remote): run_semgrep uses the remote host only when local semgrep cannot run" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: `capabilities` reports the remote honestly

**Files:**
- Modify: `cca_checks/languages/python.py` (`unavailable_claim_types`; add `remote_claim_types`)
- Modify: `cca_checks/languages/rust.py` (same)
- Modify: `cca_checks/__main__.py` (`_capabilities`)
- Test: `tests/test_remote_capabilities.py`

**Interfaces:**
- Consumes: `remote.semgrep_status` (Task 2).
- Produces: backend method `remote_claim_types(self) -> dict[str, str]`; `_capabilities` output key `"remote"` present only when a claim type is served remotely.

- [ ] **Step 1: Write the failing tests**

`tests/test_remote_capabilities.py`:

```python
"""Tests for how capabilities reports a remote semgrep."""
import pytest

from cca_checks import remote
from cca_checks.__main__ import _capabilities
from cca_checks.languages import python as pyb
from cca_checks.languages import rust


@pytest.fixture
def semgrep_blocked_here(monkeypatch):
    blocked = lambda name: "semgrep blocked here" if name == "semgrep" else None  # noqa: E731
    monkeypatch.setattr(pyb, "tool_unavailable_reason", blocked)
    monkeypatch.setattr(rust, "tool_unavailable_reason", blocked)


@pytest.mark.parametrize("file", ["a.py", "a.rs"])
def test_a_serving_remote_makes_taint_available_and_says_where(monkeypatch, semgrep_blocked_here, file):
    """Red proof: unavailable_claim_types ignores semgrep_status -> taint stays unavailable."""
    monkeypatch.setenv(remote.ENV, "cca-analyzer")
    monkeypatch.setattr(remote, "remote_semgrep_version", lambda dest: ("1.178.0", None))
    out = _capabilities(file)
    assert "taint" not in out["unavailable"]
    assert out["remote"] == {"taint": "semgrep 1.178.0 on cca-analyzer"}


def test_a_failing_remote_keeps_taint_unavailable_with_both_reasons(monkeypatch, semgrep_blocked_here):
    """Red proof: semgrep_status drops the local half -> the reason lacks 'semgrep blocked here'."""
    monkeypatch.setenv(remote.ENV, "cca-analyzer")
    monkeypatch.setattr(remote, "remote_semgrep_version", lambda dest: (None, "host key not known"))
    out = _capabilities("a.py")
    assert out["unavailable"]["taint"] == (
        "local: semgrep blocked here; remote cca-analyzer: host key not known")
    assert "remote" not in out


def test_without_a_remote_the_output_is_unchanged(semgrep_blocked_here):
    """Red proof: _capabilities always adds a "remote" key -> the key set differs."""
    out = _capabilities("a.py")
    assert set(out) == {"file", "language", "claim_types", "unavailable"}
    assert out["unavailable"]["taint"] == "semgrep blocked here"
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_remote_capabilities.py -q`
Expected: the first two fail (taint still unavailable; no `remote` key).

- [ ] **Step 3: Change both backends and `_capabilities`**

In `cca_checks/languages/python.py`, add `from .. import remote` to the imports, and in `unavailable_claim_types` replace the semgrep lines with:

```python
        semgrep_reason = tool_unavailable_reason("semgrep")
        if semgrep_reason is not None:
            # A remote host may serve taint (cca_checks.remote); semgrep_status says so,
            # or returns why neither side can run it.
            semgrep_reason, _served = remote.semgrep_status(semgrep_reason)
        if semgrep_reason is not None:
            out["taint"] = semgrep_reason
        return out

    def remote_claim_types(self) -> dict[str, str]:
        """Claim types served by a remote host, with where: reported, never silent."""
        local = tool_unavailable_reason("semgrep")
        if local is None:
            return {}
        _reason, served = remote.semgrep_status(local)
        return {"taint": served} if served else {}
```

Make the same two changes in `cca_checks/languages/rust.py` (its `unavailable_claim_types` has the same three semgrep lines; keep its cargo lines unchanged).

In `cca_checks/__main__.py`, `_capabilities`, after `unavailable = check()`:

```python
    out = {"file": file, "language": backend.name,
           "claim_types": sorted(backend.claim_types),
           "unavailable": unavailable}
    served = getattr(backend, "remote_claim_types", None)
    remote_map = served() if served is not None else {}
    if remote_map:
        out["remote"] = remote_map
    return out
```

- [ ] **Step 4: Run green, with the existing capability tests**

Run: `python -m pytest tests/test_remote_capabilities.py tests/test_capabilities.py tests/test_language_registry.py -q`
Expected: all pass, or the same pass/fail set as `origin/master` for the existing files.

- [ ] **Step 5: Prove each new test red, lint, commit**

```bash
ruff check cca_checks tests
git add cca_checks/languages/python.py cca_checks/languages/rust.py cca_checks/__main__.py tests/test_remote_capabilities.py
git commit -m "feat(remote): capabilities reports a remote semgrep, and why it cannot serve" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Documentation and the full suite

**Files:**
- Modify: `docs/configuration.md` (one row in the `CCA_` table, around line 87)
- Create: `docs/remote-semgrep.md` (operator setup, copied from spec §4 with the pinned version filled in, plus the isolation checks)

- [ ] **Step 1: The configuration row**

Add to the table in `docs/configuration.md`:

```markdown
| `CCA_REMOTE_SEMGREP` | unset | an ssh destination (alias or `user@host`) where semgrep runs when it cannot run locally; see [remote-semgrep.md](remote-semgrep.md). Read from the environment only, never from the audited repository |
```

- [ ] **Step 2: `docs/remote-semgrep.md`**

Write it from spec §4 and §5, in this order: what it is for (one paragraph); the workstation key and `~/.ssh/config` block; the host commands with `semgrep==1.178.0`; installing the runner (`scp cca_checks/remote/runner.py <admin>@<host>:runner.py` then the `install` line); accepting the host key once by hand; the two isolation checks of Task 6 Step 2 as commands the operator can rerun; and how to confirm it serves (`python -m cca_checks capabilities --file x.py` shows `"remote"`). No host name or account other than `cca-analyzer` and placeholders.

- [ ] **Step 3: Full suite and lint**

Run: `python -m pytest -q` and `ruff check cca_checks tests`.
Expected: the same failing set as `origin/master` on this workstation (the semgrep and Rust-tool tests that cannot run here), all new tests passing. Compare by test name and report both counts.

- [ ] **Step 4: Commit**

```bash
git add docs/configuration.md docs/remote-semgrep.md
git commit -m "docs: CCA_REMOTE_SEMGREP, and how an operator sets up the remote host" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Live setup and measurement (operator and author, no code)

- [ ] **Step 1: The operator's setup.** The user runs `docs/remote-semgrep.md`'s host commands with sudo. On the workstation, the key, the ssh config block and the one manual connection that records the host key. Nothing here is run by the executor without the user.

- [ ] **Step 2: Isolation checks**, on the host and from the workstation:

```bash
ssh cca-analyzer id
```

Expected: a JSON refusal from the runner (`not a tar archive`), never a uid line.

```bash
sudo -u cca-analyzer sh -c 'cat /home/*/.env; ls /home/$(stat -c %U /proc/1/cwd 2>/dev/null)' 2>&1 | head -3
```

Expected: permission denied for every other account's files (adjust the second path to the host's service account). Any readable secret stops the task and goes to the user.

- [ ] **Step 3: Pre-register** with `/preregister` before Step 4: how many of the semgrep-dependent tests that fail on this workstation pass with `CCA_REMOTE_SEMGREP=cca-analyzer CCA_TESTS_USE_REMOTE=1`, and whether `capabilities` reports `"remote"`. List the tests by name first (`python -m pytest -q` on `origin/master`, failing set), and for each say whether it reaches semgrep through `run_semgrep` (expected to pass) or by calling the binary another way (expected to stay failing).

- [ ] **Step 4: Measure.**

```bash
CCA_REMOTE_SEMGREP=cca-analyzer CCA_TESTS_USE_REMOTE=1 python -m pytest <the listed tests> -q
CCA_REMOTE_SEMGREP=cca-analyzer python -m cca_checks capabilities --file cca_checks/claim.py
```

Append the result under the pre-registration without editing the prediction, and report to the user.
