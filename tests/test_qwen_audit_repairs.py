"""Audit regressions. Baseline and mutation receipts are retained by CCA.

PERF-001 targets enumeration before archive reads; DAT-001 targets policy test
roots at the proof consumer; DOC-001 targets the installed workflow resources.
"""
import base64
import gzip
import hashlib
import io
import tarfile

import pytest

from cca_checks import qwen_tests as q
from cca_checks.plugin import installer


@pytest.mark.parametrize("mode", ["entries", "timeout"])
def test_enumeration_refuses_before_unbounded_archive_work(git_repo, monkeypatch, mode):
    original = q.subprocess.Popen
    process = []

    def launch(command, **kwargs):
        if "ls-files" in command:
            # A real child exercises stream consumption, termination and reaping.
            import sys
            script = ("import sys; sys.stdout.buffer.write(b'x.py\\0' * 10001)" if mode == "entries"
                      else "import sys,time; sys.stdout.buffer.write(b'app.py\\0'); "
                           "sys.stdout.flush(); time.sleep(.5)")
            command = [sys.executable, "-c", script]
            child = original(command, **kwargs)
            process.append(child)
            return child
        return original(command, **kwargs)

    monkeypatch.setattr(q.subprocess, "Popen", launch)
    if mode == "timeout":
        monkeypatch.setattr(q, "_GIT_ENUM_TIMEOUT", .05, raising=False)
    refused = False
    try:
        q.prepare(git_repo, "example", "run-1", ["app.py"], "entry-bound")
    except ValueError as exc:
        refused = "enumeration" in str(exc)
    assert refused, "Git enumeration must enforce its entry and wall-time budgets"
    assert process and process[0].poll() is not None


def _request(helper):
    stream = io.BytesIO()
    with gzip.GzipFile(fileobj=stream, mode="wb", mtime=0) as gz, \
            tarfile.open(fileobj=gz, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
        files = {"app.py": b"x=1\n", "checks/test_x.py": b"def test_x(): assert True\n",
                 "checks/helper.py": helper}
        for name, content in files.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    content = stream.getvalue()
    return {"project": "example", "profile": "pytest", "cca_run_id": "run-1",
            "revision": "a" * 40, "selectors": ["checks/test_x.py::test_x"],
            "snapshot_b64": base64.b64encode(content).decode(),
            "snapshot_sha256": hashlib.sha256(content).hexdigest()}


def test_proof_protects_all_signed_test_roots(monkeypatch):
    # Signature/identity validation has its own controls; exercise proof policy.
    before, after = _request(b"value=0\n"), _request(b"value=1\n")
    common = {"test_roots": ["checks"], "image": "image", "runner_version": 1}
    red = {**before, **common, "state": "failed", "report": {
        "collected": 1, "passed": 0, "failed": 1, "errors": 0, "skipped": 0,
        "assertion_failures": [hashlib.sha256(before["selectors"][0].encode()).hexdigest()]}}
    green = {**after, **common, "state": "passed", "report": {
        "collected": 1, "passed": 1, "failed": 0, "errors": 0, "skipped": 0}}
    monkeypatch.setattr(q, "verify", lambda envelope, *_: envelope)
    refused = False
    try:
        q.proof(red, before, green, after, None)
    except ValueError as exc:
        refused = "harness changed" in str(exc)
    assert refused, "an alternate test-root helper change cannot establish production red proof"


def test_installed_workflows_include_the_remote_test_guide(tmp_path):
    codex = dict(installer.iter_codex_skill())
    assert "references/qwen-tests.md" in codex
    installer.install_codex(tmp_path / "skills")
    assert (tmp_path / "skills/cca-audit/references/qwen-tests.md").read_text() == codex[
        "references/qwen-tests.md"]
    installer.install(tmp_path / "project")
    assert (tmp_path / "project/.claude/guides/qwen-tests.md").is_file()


def test_patched_crypto_floor_is_declared():
    """Nonbehavioral manifest control; no red behavior proof is claimed."""
    from pathlib import Path
    try:
        import tomllib
    except ModuleNotFoundError:
        import tomli as tomllib
    extras = tomllib.loads((Path(__file__).parents[1] / "pyproject.toml").read_text())["project"][
        "optional-dependencies"]
    assert "cryptography>=50.0.0,<51.0.0" in extras["remote-tests"]


@pytest.mark.parametrize("method", ["initialize", "tools/call"])
@pytest.mark.parametrize("reply", [None, []])
def test_mcp_nonobject_responses_fail_closed(monkeypatch, method, reply):
    """Pre-fix Client.call leaked AttributeError before its fail-closed handler."""
    monkeypatch.setenv("FIXTURE_TOKEN", "disposable-test-token")
    client = q.Client({"url": "http://127.0.0.1:1/mcp", "token_env": "FIXTURE_TOKEN"})

    def rpc(name, *args, **kwargs):
        if name == method:
            return reply
        return {"protocolVersion": "2024-11-05"}

    monkeypatch.setattr(client, "_rpc", rpc)
    refused = False
    try:
        client.call("test_status", {"job_id": "a" * 64})
    except ValueError:
        refused = True
    except AttributeError:
        pass
    assert refused, "malformed MCP result must reach the client's fail-closed error path"
