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
from typing import Callable, Sequence

MAX_EXAMPLES = 200

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
    if not (math.isfinite(a) and math.isfinite(b)):
        return False
    return math.isclose(a, b, rel_tol=REL_TOL, abs_tol=ABS_TOL)


def _replaced(args: Sequence, index: int, value) -> tuple:
    out = list(args)
    out[index] = value
    return tuple(out)


def assert_bounded(fn: Callable, args: Sequence, lo: float, hi: float) -> None:
    """The result must lie within [lo, hi]. Non-finite results are violations."""
    y = fn(*args)
    if not math.isfinite(y) or y < lo or y > hi:
        raise PropertyViolation("bounded", tuple(args), y, f"{lo} <= result <= {hi}")


def assert_monotonic_in(fn: Callable, args: Sequence, index: int,
                        direction: str, delta: float) -> None:
    """Increasing args[index] by delta must move the result in `direction`."""
    if direction not in ("increasing", "decreasing"):
        raise ValueError(f"direction must be 'increasing' or 'decreasing', got {direction!r}")
    if delta <= 0:
        raise ValueError(f"delta must be positive, got {delta!r}")
    y0 = fn(*args)
    args1 = _replaced(args, index, args[index] + delta)
    y1 = fn(*args1)
    if not (math.isfinite(y0) and math.isfinite(y1)):
        raise PropertyViolation("monotonic", tuple(args), (y0, y1), "finite results")
    if direction == "increasing" and y1 < y0 - ABS_TOL:
        raise PropertyViolation("monotonic", tuple(args), (y0, y1),
                                f"result non-decreasing in arg {index}")
    if direction == "decreasing" and y1 > y0 + ABS_TOL:
        raise PropertyViolation("monotonic", tuple(args), (y0, y1),
                                f"result non-increasing in arg {index}")


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
    if factor == 0:
        raise ValueError("factor must be non-zero")
    scaled = list(args)
    for i in indices:
        scaled[i] = scaled[i] * factor
    y0 = fn(*args)
    y1 = fn(*scaled)
    if not _close(y0, y1):
        raise PropertyViolation("scale_invariant", tuple(args), (y0, y1),
                                f"result unchanged when args {tuple(indices)} scale by {factor}")


def assert_sign_symmetric(fn: Callable, args: Sequence, index: int,
                          kind: str = "odd") -> None:
    """Negating args[index] must negate the result ('odd') or leave it ('even')."""
    if kind not in ("odd", "even"):
        raise ValueError(f"kind must be 'odd' or 'even', got {kind!r}")
    y0 = fn(*args)
    y1 = fn(*_replaced(args, index, -args[index]))
    want = -y0 if kind == "odd" else y0
    if not _close(y1, want):
        raise PropertyViolation("sign_symmetric", tuple(args), (y0, y1),
                                f"{kind} symmetry in arg {index}")


def assert_round_trips(fwd: Callable, inv: Callable, value: float) -> None:
    """inv(fwd(value)) must recover value."""
    y = inv(fwd(value))
    if not _close(y, value):
        raise PropertyViolation("round_trip", (value,), y, f"inv(fwd(x)) == x for x == {value}")
