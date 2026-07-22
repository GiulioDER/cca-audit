"""Targets exercising each way the substrate can survive or be lost.

`unstable` deliberately uses a bare `cos` imported by name: a module holds its own
binding, so patching `math.cos` alone would not reach it. That is the case the
runner must handle.
"""

import math
from math import cos

from helper_module import degraded_cos


def unstable(x):
    """Catastrophic cancellation: at x=1e-8 this returns 0.0, not 0.5."""
    return (1.0 - cos(x)) / (x * x)


def stable(x):
    """Algebraically identical to `unstable`, without the cancellation."""
    return 2.0 * math.sin(x / 2) ** 2 / (x * x)


def arithmetic_only(a, b):
    """No transcendentals — the substrate should survive on arithmetic alone."""
    return (a + b) / 2.0


def loses_substrate(x):
    """An explicit float() collapses the reference back to float64."""
    return float(x) * 2.0


def raises_always(x):
    raise RuntimeError("target exploded")


def sign_trap(mu, vol, t):
    """The v3.4 GBM sign bug. Both substrates compute the SAME wrong formula,
    so the substrate check is structurally blind to it. Kept as a probe."""
    return (mu + 0.5 * vol ** 2) * t


def cross_module_cancellation(x):
    """Same cancellation trap as `unstable`, but the `cos()` call is delegated to
    `helper_module.degraded_cos` — a second module `mpmath_bindings` cannot reach,
    because it only patches the CALLING target's own `__module__` globals.

    Proves the integrity gate's blind spot: `run_under_substrate` still returns a
    genuine `mpf` here (the outer `1.0 - float` subtraction and the following
    division against the still-`mpf` `x * x` re-promote the float64-degraded
    intermediate back into an `mpf`), so the gate's isinstance check passes — but
    the value it approves is float64-precision, computed via a cos() call that
    never touched mpmath at all.
    """
    return (1.0 - degraded_cos(x)) / (x * x)
