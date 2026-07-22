import math
import sys

import pytest

from cca_checks.substrate import (
    MIN_DPS,
    SubstrateResult,
    mpmath_bindings,
    run_under_substrate,
)

pytest.importorskip("mpmath", reason="substrate extra not installed")

sys.path.insert(0, "tests/fixtures/substrate")
import targets  # noqa: E402


def test_arithmetic_only_survives():
    r = run_under_substrate(targets.arithmetic_only, (1.0, 2.0))
    assert r.reason is None
    assert float(r.value) == pytest.approx(1.5)


def test_from_math_import_binding_is_patched():
    # `unstable` uses a bare `cos` bound at import time. If the runner only
    # patched the `math` module, this would silently stay float64 and the
    # reference would be as wrong as the code under test.
    r = run_under_substrate(targets.unstable, (1e-8,))
    assert r.reason is None
    assert float(r.value) == pytest.approx(0.5, rel=1e-6)


def test_import_math_binding_is_patched():
    r = run_under_substrate(targets.stable, (1e-8,))
    assert r.reason is None
    assert float(r.value) == pytest.approx(0.5, rel=1e-6)


def test_substrate_lost_yields_no_value():
    # The spine of the design: a lost substrate must never produce a value that
    # could be compared and read as agreement.
    r = run_under_substrate(targets.loses_substrate, (0.5,))
    assert r.reason == "substrate_lost"
    assert r.value is None


def test_target_raising_is_reported_not_swallowed():
    r = run_under_substrate(targets.raises_always, (1.0,))
    assert r.reason == "raised"
    assert r.value is None


def test_dps_below_floor_is_rejected():
    # float64 carries ~15-17 significant digits; a reference below that is less
    # precise than the thing it references.
    r = run_under_substrate(targets.stable, (1e-8,), dps=MIN_DPS - 1)
    assert r.reason == "bad_dps"
    assert r.value is None


def test_bindings_are_restored_after_success():
    with mpmath_bindings(targets.stable):
        pass
    assert targets.math is math
    assert targets.cos is math.cos


def test_bindings_are_restored_after_exception():
    with pytest.raises(RuntimeError):
        with mpmath_bindings(targets.stable):
            raise RuntimeError("boom")
    assert targets.math is math
    assert targets.cos is math.cos


def test_unpatchable_target_is_reported():
    fn = lambda x: x  # noqa: E731
    fn.__module__ = "no.such.module.anywhere"
    r = run_under_substrate(fn, (1.0,))
    assert r.reason == "not_patchable"


def test_mpmath_absent_is_unavailable(monkeypatch):
    import cca_checks.substrate as sub
    monkeypatch.setattr(sub, "mpmath", None)
    r = run_under_substrate(targets.stable, (1e-8,))
    assert r.reason == "unavailable"
    assert r.value is None


def test_result_is_frozen():
    r = SubstrateResult(value=None, reason="unavailable")
    with pytest.raises(Exception):
        r.value = 1
