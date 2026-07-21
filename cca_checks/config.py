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

import os

_DEFAULT_TIMEOUT_S = 120
_DEFAULT_MAX_EXAMPLES = 200


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Wall-clock ceiling for every external tool invocation (pyright, semgrep, pytest).
TIMEOUT_S = _positive_int("CCA_TIMEOUT_S", _DEFAULT_TIMEOUT_S)

# How many examples Hypothesis generates per property.
MAX_EXAMPLES = _positive_int("CCA_MAX_EXAMPLES", _DEFAULT_MAX_EXAMPLES)
