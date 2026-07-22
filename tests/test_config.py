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


def test_substrate_defaults(monkeypatch):
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL=None, CCA_SUBSTRATE_DPS=None)
    assert c.SUBSTRATE_TOL == 1e-9
    assert c.SUBSTRATE_DPS == 50


def test_substrate_tol_env_override(monkeypatch):
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL="1e-6")
    assert c.SUBSTRATE_TOL == 1e-6


def test_malformed_tol_falls_back_not_crashes(monkeypatch):
    # A bad env value must never take the deterministic layer down.
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL="loose")
    assert c.SUBSTRATE_TOL == 1e-9


def test_non_positive_tol_falls_back(monkeypatch):
    # A zero or negative tolerance would make every comparison a violation.
    for bad in ("0", "-1e-9"):
        c = _reload(monkeypatch, CCA_SUBSTRATE_TOL=bad)
        assert c.SUBSTRATE_TOL == 1e-9


def test_malformed_dps_falls_back(monkeypatch):
    c = _reload(monkeypatch, CCA_SUBSTRATE_DPS="lots")
    assert c.SUBSTRATE_DPS == 50


def test_positive_float_rejects_nan_and_inf(monkeypatch):
    # NaN compares false against everything; inf makes the check vacuous.
    for bad in ("nan", "inf", "-inf"):
        c = _reload(monkeypatch, CCA_SUBSTRATE_TOL=bad)
        assert c.SUBSTRATE_TOL == 1e-9


def test_tol_below_float64_noise_floor_falls_back(monkeypatch):
    """A tolerance tighter than float64's own rounding noise makes CORRECT code
    CONFIRM. Reproduced directly: with CCA_SUBSTRATE_TOL=1e-20,
    assert_substrate_agrees(targets.stable, (1e-8,)) used to raise
    PropertyViolation even though targets.stable's measured relative error
    (~8.3e-18) is ordinary float64 noise, not a real defect. Model: 1e-20 is below
    the noise floor and must fall back to the default rather than be honoured.
    """
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL="1e-20")
    assert c.SUBSTRATE_TOL == 1e-9


def test_tol_above_vacuity_ceiling_falls_back(monkeypatch):
    """A finite-but-huge tolerance makes the check permanently vacuous.
    Reproduced directly: CCA_SUBSTRATE_TOL=1e6 is a finite, positive value that
    _positive_float used to honour outright, so `relative > SUBSTRATE_TOL` could
    never fire again for any realistic divergence. Must fall back to the default.
    """
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL="1e6")
    assert c.SUBSTRATE_TOL == 1e-9


def test_tol_at_the_bounds_is_honoured(monkeypatch):
    # The bounds themselves are valid values, not excluded ones.
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL="1e-15")
    assert c.SUBSTRATE_TOL == 1e-15
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL="1.0")
    assert c.SUBSTRATE_TOL == 1.0


def test_module_restored_for_other_tests(monkeypatch):
    monkeypatch.delenv("CCA_SUBSTRATE_TOL", raising=False)
    monkeypatch.delenv("CCA_SUBSTRATE_DPS", raising=False)
    c = importlib.reload(config)
    assert c.SUBSTRATE_TOL == 1e-9
