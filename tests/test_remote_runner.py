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
