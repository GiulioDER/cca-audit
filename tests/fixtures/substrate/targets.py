"""Targets exercising each way the substrate can survive or be lost.

`unstable` deliberately uses a bare `cos` imported by name: a module holds its own
binding, so patching `math.cos` alone would not reach it. That is the case the
runner must handle.
"""

import math
from math import cos


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
