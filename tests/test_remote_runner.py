"""Tests for cca_checks.remote.runner, the only program the remote key may start."""
import json
import subprocess
import sys
import tarfile

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


# Final review fixes (2026-10-11).

def _head(name: str, size: int, kind: bytes = tarfile.REGTYPE) -> bytes:
    info = tarfile.TarInfo(name)
    info.size, info.type = size, kind
    return info.tobuf(format=tarfile.GNU_FORMAT)


def _request_member() -> bytes:
    body = json.dumps({"protocol": 1, "op": "semgrep"}).encode()
    return _head("request.json", len(body)) + body + b"\0" * (512 - len(body))


def test_a_sparse_member_is_refused(tmp_path):
    """Review C1. Red proof: read_request accepts m.isreg() again -> the sparse target runs.

    isreg() is true for GNU sparse members, and reading one fills its holes up to the
    declared real size: a 2.5 KB archive declaring 400 MB allocated 400 MB in the
    runner, outside every cap, before the size check.
    """
    data = (_request_member() + _head("rules.yaml", 0) + _head("target.py", 0, tarfile.GNUTYPE_SPARSE)
            + b"\0" * 1024)
    out, marker = _serve(tmp_path, data)
    assert out["ok"] is False and "regular file" in out["error"]
    assert not marker.exists()


def test_a_declared_size_over_budget_is_refused_before_reading(tmp_path):
    """Review C1. Red proof: drop the running budget check in read_request -> ReadError in the call.

    The header declares 10 MB and the stream carries 600 bytes: the refusal must come
    from the header alone, before extractfile reads anything.
    """
    data = _request_member() + _head("target.py", 10 * 1024 * 1024) + b"x" * 600
    out, marker = _serve(tmp_path, data)
    assert out["ok"] is False and "5 MB" in out["error"]
    assert not marker.exists()


@pytest.mark.parametrize("case", ["truncated", "deep-json"])
def test_malformed_input_is_answered_not_crashed(tmp_path, case):
    """Review M1. Red proofs: truncated -> drop the TarError handler around the member loop
    (ReadError in the call); deep-json -> drop RecursionError from the json.loads handler
    (RecursionError in the call)."""
    # Asserted on read_request, not serve: serve's last-resort handler would answer any
    # crash, so through serve this test could not go red.
    if case == "truncated":
        data = _request_member() + _head("target.py", 10_000) + b"x" * 600
    else:
        data = tar_of([("request.json", b"[" * 100_000, "file")])
    with pytest.raises(runner.Refused):
        runner.read_request(data)


def test_an_unexpected_error_is_still_one_json_answer(tmp_path, monkeypatch):
    """Review M1. Red proof: drop serve's last-resort `except Exception` -> RuntimeError in the call."""
    def boom(data):
        raise RuntimeError("unforeseen")
    monkeypatch.setattr(runner, "read_request", boom)
    out, _ = _serve(tmp_path, request("version"))
    assert out == {"ok": False, "error": "internal error (RuntimeError)"}


def test_the_timeout_kills_the_whole_process_group(tmp_path):
    """Review I1. Red proof: drop the coreutils timeout from semgrep_argv -> no 'timeout' in argv.

    subprocess.run(timeout=) kills only pysemgrep; its semgrep-core child would keep
    running in its 2G scope after the slot is released. coreutils timeout signals the
    whole group, with a KILL 5 s later.
    """
    argv = runner.semgrep_argv(["/opt/semgrep"], "target.py", wrap=True, timeout=120)
    i = argv.index("timeout")
    assert argv[i:i + 4] == ["timeout", "-k", "5", "120"]
    assert argv.index("timeout") > argv.index("nice") and argv[i + 4] == "/opt/semgrep"


def test_the_version_probe_takes_a_slot_and_the_limits(tmp_path):
    """Review I2/I3. Red proofs: serve answers version before acquire_slot -> the probe runs
    with both slots held; version_argv ignores wrap -> no systemd-run in its argv."""
    held = [runner.acquire_slot(str(tmp_path / "locks")) for _ in range(runner.MAX_RUNS)]
    try:
        out, marker = _serve(tmp_path, request("version"))
        assert out["ok"] is False and "in progress" in out["error"]
        assert not marker.exists()
    finally:
        for h in held:
            h.close()
    argv = runner.version_argv(["/opt/semgrep"], wrap=True)
    assert argv[0] == "systemd-run" and argv[-2:] == ["/opt/semgrep", "--version"]


def test_the_user_manager_is_found_without_a_login_session(monkeypatch, tmp_path):
    """Review I3. Red proof: _env copies XDG_RUNTIME_DIR only when set -> the key is missing.

    A forced-command session may lack XDG_RUNTIME_DIR, and without it systemd-run --user
    cannot reach the user manager that linger starts at /run/user/<uid>.
    """
    monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
    monkeypatch.setattr(runner.os, "getuid", lambda: 1234, raising=False)
    assert runner._env(str(tmp_path))["XDG_RUNTIME_DIR"] == "/run/user/1234"


def test_a_silent_client_is_dropped(tmp_path):
    """Review I2. Red proof: main reads stdin without the deadline -> the call times out."""
    cmd, marker = fake_semgrep(tmp_path)
    proc = subprocess.Popen([sys.executable, runner.__file__, "--semgrep", *cmd, "--no-limits",
                             "--lock-dir", str(tmp_path / "locks"), "--read-deadline", "1"],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    try:
        # stdin stays open and empty: communicate() would close it, which is an EOF,
        # not silence.
        proc.wait(timeout=20)
        out = proc.stdout.read()
    finally:
        proc.kill()
        proc.stdin.close()
    assert json.loads(out)["ok"] is False and "no request within" in json.loads(out)["error"]


def test_the_installed_runner_ignores_its_own_folder():
    """Review I4. Red proof: restore '#!/usr/bin/env python3' -> the first line differs.

    -I keeps the script's folder off sys.path and ignores PYTHON* variables, so a file
    planted beside the runner cannot be imported by it.
    """
    with open(runner.__file__, encoding="utf-8") as fh:
        assert fh.readline().rstrip("\r\n") == "#!/usr/bin/python3 -I"
