"""Remote receipts. Behavioral mutation receipts are in docs/qwen-tests-proof.md."""
import base64
import gzip
import hashlib
import io
import json
import tarfile

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from cca_checks import qwen_tests as q


@pytest.fixture
def signing(tmp_path):
    key = Ed25519PrivateKey.generate()
    path = tmp_path / "key.pem"
    path.write_bytes(key.public_key().public_bytes(serialization.Encoding.PEM,
                                                  serialization.PublicFormat.SubjectPublicKeyInfo))
    return key, path


def request(source=b"x = 1\n", test=b"def test_x(): assert x == 1\n"):
    buffer = io.BytesIO()
    with gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed, \
            tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
        for name, content in {"app.py": source, "tests/test_x.py": test}.items():
            member = tarfile.TarInfo(name)
            member.size = len(content)
            archive.addfile(member, io.BytesIO(content))
    content = buffer.getvalue()
    return {"project": "example", "cca_run_id": "run-1", "revision": "a" * 40,
            "snapshot_sha256": hashlib.sha256(content).hexdigest(),
            "snapshot_b64": base64.b64encode(content).decode(),
            "selectors": ["tests/test_x.py::test_x"], "profile": "pytest", "idempotency_key": "first"}


def signed(signing, expected, state="passed", **report_changes):
    key, _ = signing
    key_id = hashlib.sha256(key.public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).hexdigest()
    report = {"collected": 1, "passed": 1, "failed": 0, "skipped": 0, "errors": 0,
              "exit_code": 0, "assertion_failures": [], **report_changes}
    receipt = {k: v for k, v in expected.items() if k not in {"snapshot_b64", "idempotency_key"}}
    receipt.update(schema=q.SCHEMA, key_id=key_id, state=state, report=report,
                   image="sha256:" + "b" * 64, runner_version=1, test_roots=["tests"])
    return {"receipt": receipt, "signature": base64.b64encode(key.sign(q.canonical(receipt))).decode()}


def test_signature_and_expected_identity_are_required(signing):
    expected = request()
    envelope = signed(signing, expected)
    assert q.verify(envelope, expected, signing[1])["state"] == "passed"
    invalid = {**envelope, "signature": base64.b64encode(b"0" * 64).decode()}
    with pytest.raises(ValueError, match="signature"):
        q.verify(invalid, expected, signing[1])
    for field, value in (("cca_run_id", "other"), ("revision", "0" * 40),
                         ("selectors", ["tests/test_other.py"]), ("project", "other")):
        with pytest.raises(ValueError, match="match"):
            q.verify(envelope, {**expected, field: value}, signing[1])
    empty = signed(signing, expected, collected=0, passed=0)
    with pytest.raises(ValueError, match="empty"):
        q.verify(empty, expected, signing[1])


def test_proof_requires_same_test_and_behavioral_red(signing):
    before, after = request(source=b"x = 0\n"), request()
    green = signed(signing, after)
    failure = {"passed": 0, "failed": 1, "exit_code": 1}
    red = signed(signing, before, state="failed", **failure)
    assert q.proof(red, before, green, after, signing[1]) == "INCONCLUSIVE"
    red = signed(signing, before, state="failed", **failure,
                 assertion_failures=[hashlib.sha256(before["selectors"][0].encode()).hexdigest()])
    assert q.proof(red, before, green, after, signing[1]) == "RED"
    assert q.proof(signed(signing, before), before, green, after, signing[1]) == "TAUTOLOGICAL"
    bad_test = request(source=b"x=0\n", test=b"def test_x(): assert False\n")
    with pytest.raises(ValueError, match="harness changed"):
        q.proof(signed(signing, bad_test), bad_test, green, after, signing[1])
    with pytest.raises(ValueError, match="identical"):
        q.proof(green, after, green, after, signing[1])


def test_prepare_is_deterministic_binds_bytes_and_excludes_private_files(git_repo):
    (git_repo / "tests").mkdir()
    (git_repo / "tests/test_x.py").write_text("def test_x(): assert True\n")
    (git_repo / ".env").write_text("private-placeholder")
    first = q.prepare(git_repo, "example", "run-1", ["tests/test_x.py"], "key")
    assert q.prepare(git_repo, "example", "run-1", ["tests/test_x.py"], "key") == first
    assert ".env" not in q._files(first)
    assert first["snapshot_sha256"] == hashlib.sha256(base64.b64decode(first["snapshot_b64"])).hexdigest()
    (git_repo / "app.py").write_text("x=2\n")
    second = q.prepare(git_repo, "example", "run-1", ["tests/test_x.py"], "key")
    assert second["snapshot_sha256"] != first["snapshot_sha256"]


def test_current_tree_must_still_match_receipt_request(git_repo):
    expected = q.prepare(git_repo, "example", "run-1", ["app.py"], "key")
    q._matches_live(git_repo, expected)
    (git_repo / "app.py").write_text("x=2\n")
    with pytest.raises(ValueError, match="current audited"):
        q._matches_live(git_repo, expected)


def test_audited_repository_cannot_choose_endpoint_or_verification_key(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    config = root / "config.json"
    config.write_text(json.dumps({"url": "http://127.0.0.1:1/mcp", "token_env": "TEST_TOKEN",
                                  "public_key": str(tmp_path / "operator-key.pem")}))
    with pytest.raises(ValueError, match="outside"):
        q.load_operator_config(config, root)
    outside = tmp_path / "operator.json"
    outside.write_text(json.dumps({"url": "http://127.0.0.1:1/mcp", "token_env": "TEST_TOKEN",
                                   "public_key": str(root / "fake-key.pem")}))
    with pytest.raises(ValueError, match="pinned outside"):
        q.load_operator_config(outside, root)


def test_missing_verifier_does_not_become_a_pass(signing, monkeypatch):
    import builtins
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name.startswith("cryptography"):
            raise ImportError("deliberately unavailable")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", unavailable)
    with pytest.raises(ValueError, match="verification unavailable"):
        q.verify(signed(signing, request()), request(), signing[1])
