import math

import pytest

from cca_checks.properties import (
    PropertyViolation,
    assert_bounded,
    assert_limit,
    assert_monotonic_in,
    assert_round_trips,
    assert_scale_invariant,
    assert_sign_symmetric,
)


# --- assert_bounded --------------------------------------------------------

def test_bounded_passes_inside_the_range():
    assert_bounded(lambda x: x / 2, (1.0,), lo=0.0, hi=1.0)


def test_bounded_violation_names_the_observed_value():
    with pytest.raises(PropertyViolation) as e:
        assert_bounded(lambda x: x * 5, (1.0,), lo=0.0, hi=1.0)
    assert "bounded" in str(e.value)
    assert "5" in str(e.value)


def test_bounded_rejects_nan():
    with pytest.raises(PropertyViolation):
        assert_bounded(lambda x: math.nan, (1.0,), lo=0.0, hi=1.0)


def test_bounded_tolerates_a_one_ulp_overshoot_at_the_upper_bound():
    # Same class of bug as the monotonic finding: a hard, zero-tolerance
    # boundary check is magnitude-blind. At a large-magnitude bound (~1e6),
    # a result that is mathematically exactly `hi` but lands one ULP above
    # it due to floating-point representation is not a real defect.
    hi = 1_000_000.0
    y = math.nextafter(hi, math.inf)
    assert_bounded(lambda: y, (), lo=0.0, hi=hi)


def test_bounded_still_catches_a_real_violation_at_large_magnitude():
    # Guard against overcorrecting: a genuinely out-of-range result at large
    # magnitude must still be caught, not swallowed by the widened epsilon.
    hi = 1_000_000.0
    with pytest.raises(PropertyViolation):
        assert_bounded(lambda: hi * 1.1, (), lo=0.0, hi=hi)


# --- assert_monotonic_in ---------------------------------------------------

def test_monotonic_increasing_passes():
    assert_monotonic_in(lambda a, b: a + b, (1.0, 2.0), index=1,
                        direction="increasing", delta=0.5)


def test_monotonic_decreasing_catches_a_flipped_sign():
    # The motivating bug shape: a term that must reduce the result increases it.
    def buggy(mu, vol):
        return mu + 0.5 * vol ** 2

    with pytest.raises(PropertyViolation) as e:
        assert_monotonic_in(buggy, (0.1, 0.3), index=1,
                            direction="decreasing", delta=0.5)
    assert "monotonic" in str(e.value)


def test_monotonic_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        assert_monotonic_in(lambda a: a, (1.0,), index=0,
                            direction="sideways", delta=0.1)


def test_monotonic_increasing_tolerates_float_noise_at_large_magnitude():
    # A bare ABS_TOL (1e-12) is magnitude-blind: on a flat region at prices/
    # notionals scale (~1e6), ordinary floating-point noise of ~1e-7 is
    # 1e5x the old absolute tolerance but only ~1e-13 relative to the
    # magnitude -- negligible, and not a real defect. This is the motivating
    # bug shape from the finding: correct, genuinely-flat code raising a
    # spurious PropertyViolation.
    BASE = 1_000_000.0
    NOISE = 1e-7  # >> old bare ABS_TOL=1e-12, << REL_TOL(1e-9) * BASE = 1e-3

    def flat_with_noise(a, b):
        return BASE if b < 1.0 else BASE - NOISE

    assert_monotonic_in(flat_with_noise, (0.0, 0.5), index=1,
                        direction="increasing", delta=1.0)


def test_monotonic_decreasing_tolerates_float_noise_at_large_magnitude():
    BASE = 1_000_000.0
    NOISE = 1e-7

    def flat_with_noise(a, b):
        return BASE if b < 1.0 else BASE + NOISE

    assert_monotonic_in(flat_with_noise, (0.0, 0.5), index=1,
                        direction="decreasing", delta=1.0)


def test_monotonic_still_catches_a_real_violation_at_large_magnitude():
    # Guard against overcorrecting: a genuine sign flip at large magnitude
    # must still be caught, not swallowed by the widened epsilon.
    BASE = 1_000_000.0

    def actually_decreasing(a, b):
        return BASE if b < 1.0 else BASE - 500.0

    with pytest.raises(PropertyViolation):
        assert_monotonic_in(actually_decreasing, (0.0, 0.5), index=1,
                            direction="increasing", delta=1.0)


# --- assert_limit ----------------------------------------------------------

def test_limit_passes_at_the_degenerate_case():
    assert_limit(lambda mu, vol: mu - 0.5 * vol ** 2, (0.2, 1.0), index=1,
                 approaching=0.0, expected=0.2)


def test_limit_violation_reports_expected_and_observed():
    with pytest.raises(PropertyViolation) as e:
        assert_limit(lambda mu, vol: mu + vol + 1.0, (0.2, 1.0), index=1,
                     approaching=0.0, expected=0.2)
    assert "limit" in str(e.value)


def test_limit_treats_matching_infinities_as_equal():
    # A property whose degenerate case genuinely diverges (e.g. a limit that
    # correctly goes to +inf) must not be flagged as a defect merely because
    # `_close` used to treat any non-finite input as an automatic mismatch.
    assert_limit(lambda x: math.inf, (1.0,), index=0,
                 approaching=0.0, expected=math.inf)


def test_limit_rejects_opposite_signed_infinities():
    # A genuine defect (e.g. a flipped sign turning +inf into -inf) must
    # still be caught -- infinities are only "close" when they agree in sign.
    with pytest.raises(PropertyViolation):
        assert_limit(lambda x: -math.inf, (1.0,), index=0,
                     approaching=0.0, expected=math.inf)


def test_limit_rejects_infinity_against_a_finite_expectation():
    with pytest.raises(PropertyViolation):
        assert_limit(lambda x: math.inf, (1.0,), index=0,
                     approaching=0.0, expected=1.0)


# --- assert_scale_invariant ------------------------------------------------

def test_scale_invariant_passes_for_a_ratio():
    assert_scale_invariant(lambda a, b: a / b, (4.0, 2.0), factor=10.0,
                           indices=(0, 1))


def test_scale_invariant_catches_a_stray_absolute_term():
    with pytest.raises(PropertyViolation) as e:
        assert_scale_invariant(lambda a, b: a / b + a, (4.0, 2.0), factor=10.0,
                               indices=(0, 1))
    assert "scale" in str(e.value)


# --- assert_sign_symmetric -------------------------------------------------

def test_sign_symmetric_odd_passes():
    assert_sign_symmetric(lambda x: x ** 3, (2.0,), index=0, kind="odd")


def test_sign_symmetric_odd_catches_a_swapped_subtraction():
    with pytest.raises(PropertyViolation):
        assert_sign_symmetric(lambda x: x + 1.0, (2.0,), index=0, kind="odd")


def test_sign_symmetric_even_passes():
    assert_sign_symmetric(lambda x: x ** 2, (2.0,), index=0, kind="even")


def test_sign_symmetric_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        assert_sign_symmetric(lambda x: x, (1.0,), index=0, kind="weird")


# --- assert_round_trips ----------------------------------------------------

def test_round_trip_passes():
    assert_round_trips(lambda x: x * 100.0, lambda y: y / 100.0, 1.23)


def test_round_trip_catches_a_lost_factor():
    with pytest.raises(PropertyViolation) as e:
        assert_round_trips(lambda x: x * 100.0, lambda y: y / 10.0, 1.23)
    assert "round" in str(e.value)


# --- message shape ---------------------------------------------------------

def test_violation_message_carries_inputs_observed_and_required():
    with pytest.raises(PropertyViolation) as e:
        assert_bounded(lambda x: 9.0, (1.0,), lo=0.0, hi=1.0)
    msg = str(e.value)
    assert msg.startswith("PROPERTY ")
    assert "inputs=" in msg and "observed=" in msg and "required=" in msg
