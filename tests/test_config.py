"""Tunables resolve from the environment, and a bad value never takes the layer down."""

import importlib

import pytest

from cca_checks import config


def _reload(monkeypatch, **env):
    for key, value in env.items():
        if value is None:
            monkeypatch.delenv(key, raising=False)
        else:
            monkeypatch.setenv(key, value)
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _restore():
    yield
    importlib.reload(config)


def test_defaults_when_unset(monkeypatch):
    cfg = _reload(monkeypatch, CCA_TIMEOUT_S=None, CCA_MAX_EXAMPLES=None)
    assert cfg.TIMEOUT_S == 120
    assert cfg.MAX_EXAMPLES == 200


def test_env_override(monkeypatch):
    cfg = _reload(monkeypatch, CCA_TIMEOUT_S="300", CCA_MAX_EXAMPLES="50")
    assert cfg.TIMEOUT_S == 300
    assert cfg.MAX_EXAMPLES == 50


@pytest.mark.parametrize("bad", ["fast", "", "0", "-5", "12.5"])
def test_malformed_value_falls_back_to_the_default(monkeypatch, bad):
    """A bad env value must not crash the checker.

    These modules exist to render a verdict; refusing to import because someone
    exported `CCA_TIMEOUT_S=fast` would take the whole deterministic layer down and
    silently degrade every claim to LLM-only adjudication.
    """
    cfg = _reload(monkeypatch, CCA_TIMEOUT_S=bad, CCA_MAX_EXAMPLES=bad)
    assert cfg.TIMEOUT_S == 120
    assert cfg.MAX_EXAMPLES == 200


def test_timeout_is_shared_by_every_checker():
    """The value was previously a literal repeated at four call sites, and retyped
    into the message next to one of them -- so changing it would have left the
    user-facing text lying."""
    from cca_checks import property_check, pyright_check, repro_runner, semgrep_check
    for module in (property_check, pyright_check, repro_runner, semgrep_check):
        assert module.TIMEOUT_S is config.TIMEOUT_S


def test_timeout_message_quotes_the_configured_value(monkeypatch):
    import subprocess

    from cca_checks import repro_runner as rr

    monkeypatch.setattr(rr, "TIMEOUT_S", 7)

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=7)

    monkeypatch.setattr(rr.subprocess, "run", boom)
    v = rr.run_repro("R", "t.py", expected_error=None)
    assert v.verdict == "UNCERTAIN"
    assert "7s" in v.evidence
