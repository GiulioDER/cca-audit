"""A reference substrate for numeric claims — the decorrelation the property
vocabulary cannot provide.

Every property in `properties.py` is authored by the same agent that raised the
finding, so property and finding stay correlated: a wrong declared relation
yields a real counterexample to a wrong claim. This module has no authored
relation at all. It runs the target twice — once in float64, once at high
precision — and lets the substrates disagree where an author could not.

WHY mpmath AND NOT Fraction OR Decimal. Both were measured and both fail, in the
worst direction. A float literal in the source (`0.5 * vol**2`) collapses a
Fraction argument back to float, and `math.cos(Fraction)` returns a float — both
SILENTLY, so a naive implementation compares float64 against float64 and reports
agreement on essentially all real numeric code. Decimal raises TypeError on mixed
float literals, which is at least loud, but then cannot run most targets at all.
mpmath's own functions return `mpf`, so the substrate survives — provided the
target's `math` bindings are swapped, which is what `mpmath_bindings` does.

THE INTEGRITY GATE IS THE POINT. `run_under_substrate` checks the RESULT TYPE. If
the value came back as anything but an `mpf`, the substrate was lost somewhere in
the call and the comparison would be float-against-float. That returns a reason
and NEVER a value, because a value could be compared and read as agreement. This
is the package's own "a check that could not run never passes" rule applied to
itself.

THE GATE'S SCOPE IS NARROWER THAN IT SOUNDS. It proves the RETURNED VALUE is an
`mpf` — which catches substrate loss anywhere in the target's OWN module — but it
does not prove every intermediate computation stayed at high precision. A target
that delegates to a helper living in a second, unpatched module gets a plain
`float` back from that helper's `math.sin(mpf)` call; the outer arithmetic then
re-promotes that float back into an `mpf`, so the gate sees a real `mpf` and
passes, while the value it holds is already float64-degraded. The consequence is
bounded and one-directional: a degraded reference can only produce a false
UNCERTAIN (no divergence found where a sound reference would have found one),
never a false CONFIRMED — `assert_substrate_agrees` only ever raises on disagreement,
so a reference silently carrying float64 precision biases toward agreement, not
away from it. See `test_gate_does_not_catch_cross_module_precision_loss` in
`tests/test_substrate.py` for a concrete, measured case.

WARNING: this executes the target's code, twice. Targets must be pure. A target
with side effects will fire them on both runs.

NOT THREAD-SAFE. `mpmath_bindings` mutates the target module's globals for the
duration of the call, so two threads checking targets in the same module would see
each other's patches. The generated property file runs under `pytest -x` in its own
subprocess, single-threaded, which is the only context this is used from.
"""

import contextlib
import math
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from .config import SUBSTRATE_DPS

try:
    import mpmath
except ImportError:  # optional extra
    mpmath = None

# A reference less precise than float64 (~15-17 significant decimal digits)
# proves nothing about float64. 30 leaves a margin above the boundary rather
# than sitting on it.
MIN_DPS = 30


@dataclass(frozen=True)
class SubstrateResult:
    """Either a high-precision value, or the reason there isn't one. Never both."""

    value: object | None
    reason: str | None


_MATH_TO_MP_CACHE: dict | None = None


def _math_to_mp() -> dict:
    """Map each `math` function object to its mpmath counterpart.

    Keyed by the function object itself, so a name bound via `from math import cos`
    can be recognised wherever it was rebound to.

    Built once and cached: `mpmath_bindings` runs on every generated Hypothesis
    example, and both `math` and `mpmath` are immutable module namespaces for the
    life of the process, so rebuilding the table per call re-walked `dir(math)`
    a few hundred times per property for an identical result. Cached on first use
    rather than at import so the module stays importable with mpmath absent.
    """
    global _MATH_TO_MP_CACHE
    if _MATH_TO_MP_CACHE is not None:
        return _MATH_TO_MP_CACHE
    if mpmath is None:
        return {}          # not cached: mpmath may be monkeypatched in by a test
    out = {}
    for name in dir(math):
        fn = getattr(math, name)
        if callable(fn) and hasattr(mpmath, name):
            out[fn] = getattr(mpmath, name)
    _MATH_TO_MP_CACHE = out
    return out


@contextlib.contextmanager
def mpmath_bindings(fn: Callable):
    """Swap the target module's math bindings for mpmath ones, then restore.

    Yields True if the module was found and patched, False if it could not be
    located. Restoration runs in a finally block, so an exception in the body
    cannot leave the target module permanently rebound.
    """
    module = sys.modules.get(getattr(fn, "__module__", None) or "")
    if module is None or not hasattr(module, "__dict__"):
        yield False
        return

    table = _math_to_mp()
    globals_ = module.__dict__
    saved = {}
    for name, value in list(globals_.items()):
        if value is math:                       # `import math`
            saved[name] = value
            globals_[name] = mpmath
        elif callable(value) and table.get(value) is not None:   # `from math import cos`
            # `callable` first: module globals hold unhashable objects such as
            # __spec__, and a bare `value in table` raises TypeError on those.
            saved[name] = value
            globals_[name] = table[value]
    try:
        yield True
    finally:
        globals_.update(saved)


def run_under_substrate(fn: Callable, args: Sequence,
                        dps: int = SUBSTRATE_DPS) -> SubstrateResult:
    """Evaluate `fn(*args)` at `dps` decimal digits, or say why it could not be."""
    if mpmath is None:
        return SubstrateResult(None, "unavailable")
    if dps < MIN_DPS:
        return SubstrateResult(None, "bad_dps")

    with mpmath.workdps(dps):
        with mpmath_bindings(fn) as patched:
            if not patched:
                return SubstrateResult(None, "not_patchable")
            try:
                value = fn(*[mpmath.mpf(a) for a in args])
            except Exception:
                # An mpmath-specific failure is not evidence about the code under
                # test, so it escalates rather than counting as a divergence.
                return SubstrateResult(None, "raised")

    if not isinstance(value, mpmath.mpf):
        return SubstrateResult(None, "substrate_lost")
    return SubstrateResult(value, None)


def assert_substrate_agrees(fn: Callable, args: Sequence) -> None:
    """float64 must agree with the high-precision reference to within SUBSTRATE_TOL.

    Raises PropertyViolation on divergence — a real defect in the code under test.
    Raises ValueError when the check could not run, which `property_check` maps to
    UNCERTAIN. The distinction is the whole safety property: a substrate that was
    never applied must not be able to produce agreement OR a confirmation.
    """
    # Imported inside the function, not at module scope, to keep the dependency
    # one-directional: properties.py imports this function at ITS module scope
    # (the re-export at the bottom of that file), so a module-level
    # `from .properties import PropertyViolation` here would close the cycle.
    # SUBSTRATE_TOL is read here rather than bound at import so that a test
    # reloading cca_checks.config changes this function's behaviour at call time.
    from .config import SUBSTRATE_TOL
    from .properties import PropertyViolation

    if not callable(fn):
        raise ValueError(f"target must be callable, got {type(fn).__name__}")

    result = run_under_substrate(fn, args)
    if result.reason is not None:
        raise ValueError(
            f"substrate check could not run ({result.reason}); "
            f"this proves nothing about the code under test"
        )

    # `SubstrateResult` guarantees "never both": reason is None here, so
    # `run_under_substrate` took the success path, which only returns a value
    # when it is a real `mpmath.mpf` (the `isinstance` check right before its
    # `return SubstrateResult(value, None)`) -- and mpmath itself was
    # importable, or that path could not have been reached at all. That
    # invariant lives across two functions and a dataclass field typed
    # `object | None`, which is invisible to a type checker looking at this
    # function alone -- assert it locally so pyright can narrow both names for
    # every access below, rather than suppressing the check.
    assert mpmath is not None
    observed = fn(*args)
    reference = result.value
    assert isinstance(reference, mpmath.mpf)

    # Finiteness is checked SYMMETRICALLY, mirroring properties.py::_close. Checking
    # only `observed` (as this used to) is one-directional and wrong in both
    # directions: a legitimate divergence to the same infinity in both substrates
    # would be flagged (false positive), and -- the worse failure -- a non-finite
    # `reference` against a finite `observed` fell through to `diff/scale`, which
    # evaluates to NaN, and `NaN > SUBSTRATE_TOL` is False, so a genuine unbounded
    # divergence silently passed (false negative).
    observed_finite = math.isfinite(observed)
    reference_finite = mpmath.isfinite(reference)

    if observed_finite != reference_finite:
        # Exactly one side is non-finite: a real divergence, not a tolerance
        # question, regardless of which side it is.
        raise PropertyViolation(
            "substrate_agrees", tuple(args), observed,
            f"finiteness matching the reference (reference is "
            f"{mpmath.nstr(reference, 12)})",
        )

    if not observed_finite:
        # Both non-finite. Agreement requires the SAME non-finite value: two
        # substrates that both diverge to +inf are a legitimate limit (mirroring
        # `_close`'s `a == b` rule for two infinities), not a defect -- but +inf
        # vs -inf, or NaN paired with anything (NaN != NaN, by the comparison's
        # own semantics), is still a genuine mismatch.
        if observed == reference:
            return
        raise PropertyViolation(
            "substrate_agrees", tuple(args), observed,
            f"the same non-finite value as the reference "
            f"({mpmath.nstr(reference, 12)})",
        )

    # Deliberately NOT properties._close(). That helper combines a relative and an
    # absolute term because it compares two float64 values, where neither side is
    # authoritative. Here the reference is a 50-digit mpf, so relative error against
    # it is the meaningful quantity and an absolute floor would only mask real
    # divergence on small-magnitude results. SUBSTRATE_TOL's default coinciding with
    # properties.REL_TOL is convergence on float64's noise floor, not a shared knob —
    # the two are tuned independently.
    diff = abs(mpmath.mpf(observed) - reference)
    scale = abs(reference)
    relative = diff / scale if scale != 0 else diff
    if relative > SUBSTRATE_TOL:
        raise PropertyViolation(
            "substrate_agrees", tuple(args),
            (observed, mpmath.nstr(reference, 17), mpmath.nstr(relative, 4)),
            f"float64 within {SUBSTRATE_TOL} relative of the "
            f"{SUBSTRATE_DPS}-digit reference",
        )
