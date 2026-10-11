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


@pytest.mark.parametrize("ext,target", [("js", b"x"), ("py", b"#" * (5 * 1024 * 1024))],
                         ids=["wrong-ext", "oversize"])
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
