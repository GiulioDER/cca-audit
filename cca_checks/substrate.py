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


def _math_to_mp() -> dict:
    """Map each `math` function object to its mpmath counterpart.

    Keyed by the function object itself, so a name bound via `from math import cos`
    can be recognised wherever it was rebound to.
    """
    if mpmath is None:
        return {}
    out = {}
    for name in dir(math):
        fn = getattr(math, name)
        if callable(fn) and hasattr(mpmath, name):
            out[fn] = getattr(mpmath, name)
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

    if not math.isfinite(observed):
        # A non-finite float against a finite reference is a defect, not a
        # tolerance question.
        raise PropertyViolation(
            "substrate_agrees", tuple(args), observed,
            f"finite result (reference is {mpmath.nstr(reference, 12)})",
        )

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
