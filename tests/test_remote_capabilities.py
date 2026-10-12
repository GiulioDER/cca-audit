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
