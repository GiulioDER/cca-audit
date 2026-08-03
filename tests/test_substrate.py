import dataclasses
import math
import os
import sys

import pytest

import cca_checks.properties as _properties_module
from cca_checks.properties import PropertyViolation
from cca_checks.substrate import (
    MIN_DPS,
    SubstrateResult,
    assert_substrate_agrees,
    mpmath_bindings,
    run_under_substrate,
)

mpmath = pytest.importorskip("mpmath", reason="substrate extra not installed")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "fixtures", "substrate"))
import targets  # noqa: E402


@pytest.fixture(autouse=True)
def _restore_properties_module():
    """Undo the global-state damage `test_properties_imports_without_mpmath` does.

    That test calls `importlib.reload(cca_checks.properties)`. A `class` statement
    mints a brand-new class object every time it executes, so the reload leaves
    `cca_checks.properties.PropertyViolation` pointing at a NEW class -- call it
    C1 -- distinct from the original, C0. `cca_checks.substrate` is never
    reloaded; `assert_substrate_agrees` does `from .properties import
    PropertyViolation` INSIDE the function body, so after the reload every call
    raises a C1 instance. Meanwhile this file (and test_properties.py,
    test_selfaudit_hardening.py) did `from cca_checks.properties import
    PropertyViolation` at MODULE scope -- all executed at collection time, before
    any test runs, so all of them are stuck holding C0 forever. Once that split
    exists, `isinstance`/`pytest.raises(PropertyViolation)` against the C0 name
    silently stops matching C1 exceptions, for the rest of the pytest session,
    nowhere near the test that caused it -- and it is invisible today only because
    this file happens to sort/run last, so nothing downstream observes the drift.

    A second reload in teardown would NOT fix this: re-executing the module body
    again mints yet another class, C2, still mismatched against the C0 already
    captured by every module-scope import. The only way back is to put the
    *original* objects back, verbatim -- so snapshot this module's namespace
    before the test and restore it byte-for-byte after. This mirrors the autouse
    `_restore` fixture in tests/test_config.py, generalised from "a reload
    reproduces an equal value" (true there -- config holds plain ints/floats,
    compared by value) to "a reload must be undone with the identical object"
    (required here -- classes are compared by identity, and reload cannot recreate
    an existing identity, only mint a new one).
    """
    snapshot = vars(_properties_module).copy()
    yield
    vars(_properties_module).clear()
    vars(_properties_module).update(snapshot)


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


def test_cross_module_precision_loss_is_detected_before_repromotion_can_hide_it():
    r = run_under_substrate(targets.cross_module_cancellation, (1e-8,))
    assert r.reason == "substrate_lost"
    assert r.value is None
    assert r.detail is not None
    assert "__float__" in r.detail
    assert "helper_module.degraded_cos" in r.detail


@pytest.mark.parametrize(
    "target",
    [targets.default_binding_cancellation, targets.closure_binding_cancellation],
)
def test_non_global_math_bindings_are_detected_as_precision_loss(target):
    r = run_under_substrate(target, (1e-8,))
    assert r.reason == "substrate_lost"
    assert r.value is None
    assert r.detail is not None and "__float__" in r.detail


def test_explicit_float_conversion_carries_actionable_detail():
    r = run_under_substrate(targets.loses_substrate, (0.5,))
    assert r.reason == "substrate_lost"
    assert r.detail is not None
    assert "__float__" in r.detail
    assert "targets.loses_substrate" in r.detail


def test_profile_replacement_is_substrate_loss():
    def existing(frame, event, arg):
        return None

    sys.setprofile(existing)
    try:
        r = run_under_substrate(targets.replaces_profiler, (0.5,))
        assert r.reason == "substrate_lost"
        assert r.detail is not None
        assert "profile hook replaced" in r.detail
        assert sys.getprofile() is existing
    finally:
        sys.setprofile(None)


def test_existing_profiler_is_composed_and_restored():
    events = []

    def existing(frame, event, arg):
        if event == "call":
            events.append(frame.f_code.co_name)

    sys.setprofile(existing)
    try:
        r = run_under_substrate(targets.stable, (1e-8,))
        assert r.reason is None
        assert sys.getprofile() is existing
        assert events
    finally:
        sys.setprofile(None)


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
    with pytest.raises(dataclasses.FrozenInstanceError):
        r.value = 1


@pytest.mark.parametrize(
    "value,reason,detail",
    [(None, None, None), (mpmath.mpf("1.0"), "raised", None),
     (mpmath.mpf("1.0"), None, "impossible")],
)
def test_result_rejects_invalid_state(value, reason, detail):
    with pytest.raises(ValueError):
        SubstrateResult(value, reason, detail)




def test_cancellation_is_a_violation():
    with pytest.raises(PropertyViolation) as e:
        assert_substrate_agrees(targets.unstable, (1e-8,))
    msg = str(e.value)
    assert msg.startswith("PROPERTY ")
    assert "substrate_agrees" in msg
    assert "inputs=" in msg


def test_stable_variant_does_not_violate():
    # Same maths, no cancellation. Proves the check discriminates rather than
    # flagging every float function.
    assert_substrate_agrees(targets.stable, (1e-8,))


def test_arithmetic_only_does_not_violate():
    assert_substrate_agrees(targets.arithmetic_only, (1.0, 2.0))


def test_sign_trap_does_not_violate():
    # THE BLINDNESS PROBE. The GBM sign defect is real and present, and both
    # substrates compute the same wrong formula, so they agree perfectly. This
    # layer cannot see formula errors; properties cover that class. Asserting it
    # keeps the documented division of labour honest.
    assert_substrate_agrees(targets.sign_trap, (0.1, 0.3, 1.0))


def test_stateful_target_escalates_instead_of_manufacturing_a_confirmation():
    targets.reset_stateful_counter()
    with pytest.raises(ValueError) as e:
        assert_substrate_agrees(targets.stateful_counter, (1.0,))
    assert "nondeterministic replay" in str(e.value)
    assert not isinstance(e.value, PropertyViolation)


def test_replay_treats_two_nans_as_stable():
    import cca_checks.substrate as sub

    monkeypatch_result = SubstrateResult(mpmath.mpf("nan"), None)
    original = sub.run_under_substrate
    sub.run_under_substrate = lambda fn, args, dps=None: monkeypatch_result
    try:
        with pytest.raises(PropertyViolation):
            assert_substrate_agrees(lambda x: math.nan, (1.0,))
    finally:
        sub.run_under_substrate = original


def test_replay_distinguishes_signed_zero():
    calls = iter([0.0, 0.0, -0.0])

    def alternating_zero(x):
        return next(calls)

    with pytest.raises(ValueError, match="nondeterministic replay"):
        assert_substrate_agrees(alternating_zero, (1.0,))


def test_second_float_run_happens_when_reference_fails(monkeypatch):
    import cca_checks.substrate as sub

    calls = []

    def target(x):
        calls.append(x)
        return 1.0

    monkeypatch.setattr(
        sub, "run_under_substrate",
        lambda fn, args: SubstrateResult(None, "bad_dps"),
    )
    with pytest.raises(ValueError, match="bad_dps"):
        assert_substrate_agrees(target, (1.0,))
    assert calls == [1.0, 1.0]


def test_replay_mismatch_takes_priority_over_reference_failure(monkeypatch):
    import cca_checks.substrate as sub

    values = iter([1.0, 2.0])

    def target(x):
        return next(values)

    monkeypatch.setattr(
        sub, "run_under_substrate",
        lambda fn, args: SubstrateResult(None, "bad_dps"),
    )
    with pytest.raises(ValueError, match="nondeterministic replay") as error:
        assert_substrate_agrees(target, (1.0,))
    assert "bad_dps" not in str(error.value)


def test_substrate_failure_raises_value_error_not_violation():
    # ValueError emits no "PROPERTY ... violated" line, so property_check maps it
    # to UNCERTAIN. A PropertyViolation here would let a lost substrate CONFIRM.
    with pytest.raises(ValueError) as e:
        assert_substrate_agrees(targets.loses_substrate, (0.5,))
    assert "substrate_lost" in str(e.value)
    assert not isinstance(e.value, PropertyViolation)


def test_non_callable_target_is_rejected():
    with pytest.raises(ValueError):
        assert_substrate_agrees("not a function", (1.0,))


def _fake_reference(value):
    """Build a stand-in for run_under_substrate that hands back a canned
    reference, so these tests exercise assert_substrate_agrees's finiteness
    comparison directly rather than needing arithmetic that happens to diverge
    identically in both float64 and mpmath (mpmath's exponent range is wide
    enough that this is not natural to construct)."""
    return lambda fn, args, dps=None: SubstrateResult(value, None)


def test_both_substrates_diverging_to_same_infinity_does_not_violate(monkeypatch):
    # The false positive named in the audit: a function that legitimately
    # diverges to +inf, where BOTH substrates agree, must not be flagged.
    import cca_checks.substrate as sub
    monkeypatch.setattr(sub, "run_under_substrate", _fake_reference(mpmath.mpf("inf")))
    assert_substrate_agrees(lambda x: math.inf, (1.0,))  # must not raise


def test_opposite_signed_infinities_still_violate(monkeypatch):
    # Both non-finite is not automatically agreement: opposite signs is a real
    # divergence and must still raise.
    import cca_checks.substrate as sub
    monkeypatch.setattr(sub, "run_under_substrate", _fake_reference(mpmath.mpf("inf")))
    with pytest.raises(PropertyViolation):
        assert_substrate_agrees(lambda x: -math.inf, (1.0,))


def test_nonfinite_reference_with_finite_observed_violates(monkeypatch):
    # The false negative named in the audit: this used to fall through to
    # diff/scale, which evaluates to NaN, and `NaN > SUBSTRATE_TOL` is False --
    # so a genuine, unbounded divergence silently passed. No existing test
    # covered this direction.
    import cca_checks.substrate as sub
    monkeypatch.setattr(sub, "run_under_substrate", _fake_reference(mpmath.mpf("inf")))
    with pytest.raises(PropertyViolation):
        assert_substrate_agrees(lambda x: 1.0, (1.0,))


def test_finite_reference_with_nonfinite_observed_still_violates(monkeypatch):
    # The pre-existing (correct) direction: must keep working after the symmetric
    # rewrite.
    import cca_checks.substrate as sub
    monkeypatch.setattr(sub, "run_under_substrate", _fake_reference(mpmath.mpf(1.0)))
    with pytest.raises(PropertyViolation):
        assert_substrate_agrees(lambda x: math.inf, (1.0,))


def test_nan_reference_with_nan_observed_still_violates(monkeypatch):
    # NaN != NaN: two NaNs are not "the same non-finite value" the way two
    # matching infinities are, so this must still raise rather than agree.
    import cca_checks.substrate as sub
    monkeypatch.setattr(sub, "run_under_substrate", _fake_reference(mpmath.mpf("nan")))
    with pytest.raises(PropertyViolation):
        assert_substrate_agrees(lambda x: math.nan, (1.0,))


def test_helper_is_reexported_from_properties():
    from cca_checks import properties
    assert properties.assert_substrate_agrees is assert_substrate_agrees


def test_substrate_tol_is_reexported_from_properties():
    from cca_checks import properties
    from cca_checks.config import SUBSTRATE_TOL
    assert properties.SUBSTRATE_TOL == SUBSTRATE_TOL


def test_absurdly_tight_tol_env_var_does_not_confirm_correct_code(monkeypatch):
    """End-to-end reproduction of the P1-2 bug, at the level it was actually
    demonstrated: CCA_SUBSTRATE_TOL=1e-20 used to make assert_substrate_agrees
    raise PropertyViolation against `targets.stable` -- the fixture's
    deliberately-correct counterpart to `unstable` -- whose measured relative
    error against the mpmath reference (~8.3e-18) is ordinary float64 noise, not a
    defect. A CONFIRMED here is binding (no-overturn rule, no adversarial panel),
    so the env var must degrade to the default rather than be honoured.
    """
    import importlib

    from cca_checks import config as _config

    monkeypatch.setenv("CCA_SUBSTRATE_TOL", "1e-20")
    importlib.reload(_config)
    try:
        assert _config.SUBSTRATE_TOL == 1e-9  # fell back, did not adopt 1e-20
        assert_substrate_agrees(targets.stable, (1e-8,))  # must not raise
    finally:
        monkeypatch.delenv("CCA_SUBSTRATE_TOL", raising=False)
        importlib.reload(_config)


def test_properties_imports_without_mpmath(monkeypatch):
    # properties.py must stay importable when the optional extra is absent.
    import importlib
    monkeypatch.setitem(sys.modules, "mpmath", None)
    importlib.reload(importlib.import_module("cca_checks.properties"))
