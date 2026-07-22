"""A SECOND module `mpmath_bindings` cannot reach — proves the integrity gate's blind spot.

`mpmath_bindings` patches only the CALLING target's own `__module__` globals. A target
in `targets.py` that delegates arithmetic to a function living here gets this module's
own, unpatched `math` binding: `degraded_cos` always computes with the real `math.cos`,
at float64 precision, regardless of what is patched in `targets.py`.
"""

import math


def degraded_cos(x):
    """Plain `math.cos`. Converts an `mpf` argument through `__float__` and returns a
    PLAIN `float` — this module's `math` is never swapped for `mpmath`, because the
    patch only ever touches the module of the function `run_under_substrate` was
    given, not modules that function calls into."""
    return math.cos(x)
