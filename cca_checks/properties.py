"""Metamorphic property assertions for numeric claims.

Deliberately free of any `hypothesis` import: these are per-example assertions,
called from inside a generated test. The generator lives in `hypo.py` so this
module stays importable with no optional dependency installed.

Every helper takes the *intended* relation as an explicit argument. A property
that merely restates the implementation therefore cannot be written through this
vocabulary — which is the whole point, since a tautological property passes on
buggy code.
"""

import math
from collections.abc import Callable, Sequence

from .config import MAX_EXAMPLES  # noqa: F401  (re-exported: hypo.py imports it from here)

# Comparison tolerance. Numeric audit targets are floating point; an exact
# equality test would produce counterexamples that are artifacts of
# representation rather than real defects.
REL_TOL = 1e-9
ABS_TOL = 1e-12


class PropertyViolation(AssertionError):
    """A declared property did not hold for a concrete input."""

    def __init__(self, prop: str, inputs, observed, required: str):
        super().__init__(
            f"PROPERTY {prop} violated | inputs={inputs!r} | "
            f"observed={observed!r} | required={required}"
        )
        self.prop = prop
        self.inputs = inputs
        self.observed = observed
        self.required = required


def _close(a: float, b: float) -> bool:
    if math.isnan(a) or math.isnan(b):
        return False
    if math.isinf(a) or math.isinf(b):
        # Two genuinely-infinite values are a real equality (e.g. a limit that
        # legitimately diverges), not a floating-point artifact -- `isclose`
        # only has a finite-vs-finite contract, so treating "non-finite" as a
        # blanket mismatch would flag correct code as a defect. A finite vs.
        # infinite mismatch, or opposite-signed infinities, is still a
        # genuine defect and must still fail.
        return a == b
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _replaced(args: Sequence, index: int, value) -> tuple:
    out = list(args)
    out[index] = value
    return tuple(out)


def _require_harness_finite(original, perturbed, what: str) -> None:
    """Reject a perturbation that our own arithmetic pushed out of range.

    Every helper that probes `fn` at a MODIFIED input owns the arithmetic that
    builds that input. If scaling or stepping overflows a finite argument to
    inf/nan, any resulting mismatch is an artifact of the harness, not a defect in
    the code under test -- `a/b` is exactly scale-invariant, yet scaling 1e300 by
    1e10 yields inf/inf = nan and would "falsify" it. Hypothesis's default float
    strategy reaches these magnitudes routinely, and `derandomize=True` makes the
    bogus counterexample stable, so it reads as a solid artifact.

    ValueError (not PropertyViolation) is deliberate: it produces no
    "PROPERTY ... violated" line, so property_check maps it to UNCERTAIN -- the
    safe direction. Only the code's own non-finite output may be a violation.
    """
    if math.isfinite(original) and not math.isfinite(perturbed):
        raise ValueError(
            f"harness overflow: {what} left the representable range "
            f"({original!r} -> {perturbed!r}); this example proves nothing about the "
            f"code under test"
        )


def assert_bounded(fn: Callable, args: Sequence, lo: float, hi: float) -> None:
    """The result must lie within [lo, hi] (inclusive). Non-finite results are
    violations. The boundary check is magnitude-aware: at large lo/hi/result
    magnitudes, ordinary floating-point representation noise (e.g. a result
    that is mathematically exactly `hi` but lands one ULP above it) must not
    read as a defect."""
    # Validate the declared relation before testing anything against it. lo > hi
    # makes `lo <= y <= hi` unsatisfiable for EVERY input, so the first generated
    # example confirms against arbitrary code -- turning a swapped-operand typo in
    # the auditor's `properties:` block (precisely the operand-order class this
    # tool exists to catch) into a binding CONFIRMED. The `not (lo <= hi)` form
    # also rejects NaN bounds. Every sibling helper validates its own arguments;
    # this one was the exception.
    if not (lo <= hi):
        raise ValueError(f"lo must not exceed hi, got lo={lo!r} hi={hi!r}")
    y = fn(*args)
    if not math.isfinite(y):
        raise PropertyViolation("bounded", tuple(args), y, f"{lo} <= result <= {hi}")
    eps = max(ABS_TOL, REL_TOL * max(abs(lo), abs(hi), abs(y)))
    if y < lo - eps or y > hi + eps:
        raise PropertyViolation("bounded", tuple(args), y, f"{lo} <= result <= {hi}")


def assert_monotonic_in(fn: Callable, args: Sequence, index: int,
                        direction: str, delta: float,
                        domain_hi: float | None = None, strict: bool = False) -> None:
    """Increasing args[index] by delta must move the result in `direction`.

    `domain_hi` is the declared upper bound of args[index]'s input domain. Without
    it this helper evaluates `fn` at `args[index] + delta`, which by construction
    steps OUTSIDE the domain Hypothesis was told to generate from -- so a function
    that is correct on its domain but behaves differently past the boundary yields
    a counterexample production can never reach. Worse, the violation used to report
    `args` (the in-domain point), giving no trace of the out-of-domain probe that
    actually failed. Pass `domain_hi` and the probe steps DOWNWARD at the boundary
    instead, keeping both evaluations inside the declared domain.

    `strict` requires a real change, not merely a non-adverse one. The default
    non-strict test with a magnitude-relative epsilon passes on the two defects
    this helper most exists to catch: a term dropped entirely (`mu - 0.5*vol**2`
    reduced to `mu`) and a wrong-signed term small relative to a notional-scale
    base. Declare `strict` when the term's presence IS the claim.
    """
    if direction not in ("increasing", "decreasing"):
        raise ValueError(f"direction must be 'increasing' or 'decreasing', got {direction!r}")
    if delta <= 0:
        raise ValueError(f"delta must be positive, got {delta!r}")

    base = args[index]
    step_up = base + delta
    if domain_hi is not None and step_up > domain_hi:
        # At the declared upper bound: probe downward so both points stay in domain.
        low_args, high_args = _replaced(args, index, base - delta), tuple(args)
    else:
        low_args, high_args = tuple(args), _replaced(args, index, step_up)
    _require_harness_finite(base, low_args[index], f"arg {index} stepped by {delta}")
    _require_harness_finite(base, high_args[index], f"arg {index} stepped by {delta}")
    if low_args[index] == high_args[index]:
        # delta vanished into rounding at this magnitude; the probe tests nothing.
        raise ValueError(
            f"harness underflow: delta={delta!r} does not change arg {index} at "
            f"{base!r}; this example proves nothing about the code under test")

    y_low = fn(*low_args)
    y_high = fn(*high_args)
    if not (math.isfinite(y_low) and math.isfinite(y_high)):
        raise PropertyViolation("monotonic", (low_args, high_args), (y_low, y_high),
                                "finite results")
    # Magnitude-aware epsilon: a bare ABS_TOL (1e-12) is blind to the scale of
    # the outputs being compared. For large-magnitude results (prices,
    # notionals in the 1e6+ range), ordinary floating-point noise on a flat
    # or correctly-monotonic region routinely exceeds 1e-12 in absolute terms
    # while being negligible relative to the values themselves -- which would
    # otherwise raise a spurious violation on correct code.
    eps = max(ABS_TOL, REL_TOL * max(abs(y_low), abs(y_high)))
    inputs = (low_args, high_args)
    if direction == "increasing":
        if y_high < y_low - eps:
            raise PropertyViolation("monotonic", inputs, (y_low, y_high),
                                    f"result non-decreasing in arg {index}")
        if strict and not (y_high > y_low + eps):
            raise PropertyViolation("monotonic", inputs, (y_low, y_high),
                                    f"result STRICTLY increasing in arg {index}")
    if direction == "decreasing":
        if y_high > y_low + eps:
            raise PropertyViolation("monotonic", inputs, (y_low, y_high),
                                    f"result non-increasing in arg {index}")
        if strict and not (y_high < y_low - eps):
            raise PropertyViolation("monotonic", inputs, (y_low, y_high),
                                    f"result STRICTLY decreasing in arg {index}")


def assert_limit(fn: Callable, args: Sequence, index: int,
                 approaching: float, expected: float) -> None:
    """With args[index] set to its degenerate value, the result must equal `expected`."""
    args0 = _replaced(args, index, approaching)
    y = fn(*args0)
    if not _close(y, expected):
        raise PropertyViolation("limit", args0, y,
                                f"result == {expected} when arg {index} == {approaching}")


def assert_scale_invariant(fn: Callable, args: Sequence, factor: float,
                           indices: Sequence[int]) -> None:
    """Scaling the named args by `factor` must leave the result unchanged."""
    # The module docstring claims a tautological property cannot be written through
    # this vocabulary. These three checks are what make that true here: factor==1.0
    # or an empty `indices` leaves `scaled == args`, reducing the assertion to
    # fn(args) == fn(args) -- vacuously true on ANY code, including code that is
    # not scale-invariant at all. A duplicate index is the mirror failure: it
    # compounds, scaling that arg by factor**k, which falsifies a genuinely
    # invariant function.
    if factor == 0:
        raise ValueError("factor must be non-zero")
    if factor == 1:
        raise ValueError("factor must differ from 1; a unit factor makes the property vacuous")
    if not indices:
        raise ValueError("indices must be non-empty; scaling nothing makes the property vacuous")
    if len(set(indices)) != len(indices):
        raise ValueError(f"duplicate entry in indices={tuple(indices)!r} would compound the factor")
    scaled = list(args)
    for i in indices:
        scaled[i] = scaled[i] * factor
        _require_harness_finite(args[i], scaled[i], f"arg {i} scaled by {factor}")
    y0 = fn(*args)
    y1 = fn(*scaled)
    if math.isnan(y0) or math.isnan(y1):
        # NaN only. Two NaNs technically satisfy "unchanged", so reporting them
        # under the scale-invariance banner would misdescribe the observation --
        # name the real problem instead. Deliberately NOT `not isfinite(...)`:
        # `_close` documents inf == inf as a genuine equality (a limit that
        # legitimately diverges), so rejecting all non-finite results here would
        # turn a documented pass into a violation.
        raise PropertyViolation("scale_invariant", (tuple(args), tuple(scaled)), (y0, y1),
                                "non-NaN results")
    if not _close(y0, y1):
        raise PropertyViolation("scale_invariant", (tuple(args), tuple(scaled)), (y0, y1),
                                f"result unchanged when args {tuple(indices)} scale by {factor}")


def assert_sign_symmetric(fn: Callable, args: Sequence, index: int,
                          kind: str = "odd") -> None:
    """Negating args[index] must negate the result ('odd') or leave it ('even')."""
    if kind not in ("odd", "even"):
        raise ValueError(f"kind must be 'odd' or 'even', got {kind!r}")
    negated = _replaced(args, index, -args[index])
    y0 = fn(*args)
    y1 = fn(*negated)
    if math.isnan(y0) or math.isnan(y1):
        # As in assert_scale_invariant: (nan, nan) satisfies the symmetry relation,
        # so reporting it as a symmetry failure would misdescribe the observation.
        # NaN only -- `_close` treats inf == inf as a real equality.
        raise PropertyViolation("sign_symmetric", (tuple(args), negated), (y0, y1),
                                "non-NaN results")
    want = -y0 if kind == "odd" else y0
    if not _close(y1, want):
        raise PropertyViolation("sign_symmetric", (tuple(args), negated), (y0, y1),
                                f"{kind} symmetry in arg {index}")


def assert_round_trips(fwd: Callable, inv: Callable, value: float,
                       quantum: float = 0.0) -> None:
    """inv(fwd(value)) must recover value to within `quantum`.

    `quantum` is the granularity the forward conversion lands on: 0.01 for money
    held as integer minor units, 1e-18 for token decimals, the tick size for a
    price grid. It defaults to 0.0 (exact round trip) but must be declared for any
    conversion that quantizes -- which is most of them.

    Without it this helper compared to within a 1e-9 relative tolerance and so was
    guaranteed to falsify every CORRECT quantizing converter over a continuous
    domain: money <-> minor units on floats in [0.01, 1e6] fails at x=1.625 (162
    cents -> 1.62, a 3e-3 relative error, ~1e6x the tolerance). Because the defect
    is in the comparison rather than in the declared relation, re-reading the
    property does not reveal it -- the relation is right, the equality semantics
    were wrong.
    """
    if quantum < 0 or not math.isfinite(quantum):
        raise ValueError(f"quantum must be a non-negative finite number, got {quantum!r}")
    y = inv(fwd(value))
    if quantum == 0:
        ok = _close(y, value)
    else:
        # Tolerate a full quantum rather than half: round-to-nearest loses at most
        # quantum/2, but floor/ceil/truncate conversions lose up to a full quantum,
        # and this helper is not told which. Erring wide costs a confirmation (the
        # property holds, so the run escalates to UNCERTAIN); erring narrow would
        # manufacture a binding CONFIRMED against correct code.
        ok = math.isfinite(y) and abs(y - value) <= quantum + max(
            ABS_TOL, REL_TOL * max(abs(value), abs(y)))
    if not ok:
        raise PropertyViolation(
            "round_trip", (value,), y,
            f"inv(fwd(x)) == x (within quantum={quantum}) for x == {value}")


# Re-exported so the vocabulary is one import. A module-level import is safe here
# even though `substrate` deals with an optional extra: substrate.py sets
# `mpmath = None` on ImportError rather than failing, so this succeeds whether or
# not the extra is installed, and `properties.assert_substrate_agrees` is the same
# object as `substrate.assert_substrate_agrees`.
from .substrate import assert_substrate_agrees  # noqa: E402,F401  (re-exported)
