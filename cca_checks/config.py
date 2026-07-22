"""Tunables for the deterministic checkers, in one place and overridable.

Every knob here used to be a literal repeated across the four checker modules --
and, worse, retyped into the user-facing message next to it, so changing the value
would have left the message lying. A timeout maps to UNCERTAIN, so on a slow
machine or a large file an unreachable timeout is a silent loss of deterministic
coverage; the only remedy was editing installed site-packages.

Environment overrides use the `CCA_` prefix. A malformed value falls back to the
default rather than crashing the checker: this package's job is to render a
verdict, and refusing to start because someone exported `CCA_TIMEOUT_S=fast` would
take the whole deterministic layer down.
"""

import math
import os

_DEFAULT_TIMEOUT_S = 120
_DEFAULT_MAX_EXAMPLES = 200
_DEFAULT_SUBSTRATE_TOL = 1e-9
_DEFAULT_SUBSTRATE_DPS = 50


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    """Same contract as _positive_int, for a real-valued knob.

    NaN and infinity are rejected alongside non-positive values: NaN compares
    false against everything, so a NaN tolerance silently turns every comparison
    into a violation, and an infinite tolerance makes the check vacuous. Both are
    worse than the default.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(value) or value <= 0:
        return default
    return value


# Wall-clock ceiling for every external tool invocation (pyright, semgrep, pytest).
TIMEOUT_S = _positive_int("CCA_TIMEOUT_S", _DEFAULT_TIMEOUT_S)

# How many examples Hypothesis generates per property.
MAX_EXAMPLES = _positive_int("CCA_MAX_EXAMPLES", _DEFAULT_MAX_EXAMPLES)

# Relative divergence between the float64 result and the high-precision reference
# above which a numeric finding is CONFIRMED. A well-conditioned float64 result
# lands within ~1e-15 of exact; 1e-9 is "worse than float32", i.e. real precision
# loss rather than ordinary representation noise.
SUBSTRATE_TOL = _positive_float("CCA_SUBSTRATE_TOL", _DEFAULT_SUBSTRATE_TOL)

# Decimal digits of precision for the reference substrate.
SUBSTRATE_DPS = _positive_int("CCA_SUBSTRATE_DPS", _DEFAULT_SUBSTRATE_DPS)
