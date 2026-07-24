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
_DEFAULT_RUST_TIMEOUT_S = 600
_DEFAULT_MAX_EXAMPLES = 200
_DEFAULT_SUBSTRATE_TOL = 1e-9
_DEFAULT_SUBSTRATE_DPS = 50

# Bounds on CCA_SUBSTRATE_TOL. Unlike CCA_TIMEOUT_S/CCA_MAX_EXAMPLES, this knob is
# not just "must be a sane positive number" -- a value that is technically positive
# and finite can still make the checker WRONG rather than merely slow.
#
# Floor: float64 itself carries ~15-17 significant decimal digits, and an
# expression chain of a handful of operations accumulates a handful of ULPs of
# rounding on top of that -- exactly the "~1e-15" noise floor this module already
# documents below for SUBSTRATE_TOL's default. A tolerance tighter than that floor
# does not measure precision loss in the code under test; it measures float64's
# own unavoidable rounding, and flags correct code as a defect. `targets.stable`
# (the fixture's deliberately-correct counterpart to `unstable`) measures ~8.3e-18
# relative error against the mpmath reference -- ordinary noise, comfortably above
# this floor, and must never CONFIRM.
_MIN_SUBSTRATE_TOL = 1e-15
# Ceiling: a relative tolerance at or above 1.0 accepts an `observed` differing
# from `reference` by 100% of the reference's own magnitude -- wide enough to
# swallow a sign flip (relative error ~2 for equal-magnitude values) or a dropped
# additive term, the exact defect classes `assert_substrate_agrees` exists to
# catch. Past that point the check cannot fire on the failures it was built for,
# so CONFIRMED becomes permanently unreachable; a finite-but-huge override (e.g.
# 1e6) must degrade to the default rather than be honoured.
_MAX_SUBSTRATE_TOL = 1.0


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float,
                    lo: float | None = None, hi: float | None = None) -> float:
    """Same contract as _positive_int, for a real-valued knob.

    NaN and infinity are rejected alongside non-positive values: NaN compares
    false against everything, so a NaN tolerance silently turns every comparison
    into a violation, and an infinite tolerance makes the check vacuous. Both are
    worse than the default.

    `lo`/`hi`, when given, bound the value the same way: a value outside them is
    just as "bad" as a non-positive or non-finite one, and falls back to the
    default rather than being obeyed -- a malformed knob must degrade safely, not
    silently change what the checker proves.
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
    if lo is not None and value < lo:
        return default
    if hi is not None and value > hi:
        return default
    return value


# Wall-clock ceiling for every external tool invocation (pyright, semgrep, pytest).
TIMEOUT_S = _positive_int("CCA_TIMEOUT_S", _DEFAULT_TIMEOUT_S)

# The same ceiling for cargo, which needs its own because it is not the same kind of
# operation. pyright and semgrep analyse a file; `cargo clippy` COMPILES a crate and
# every dependency it has, from a cold target directory (see clippy_check on why the
# directory must be cold). Two minutes is generous for the former and routinely too
# short for the latter, and a timeout maps to UNCERTAIN -- so an under-set ceiling
# does not fail loudly, it silently deletes deterministic coverage for exactly the
# large crates where it is worth most.
RUST_TIMEOUT_S = _positive_int("CCA_RUST_TIMEOUT_S", _DEFAULT_RUST_TIMEOUT_S)

# How many examples Hypothesis generates per property.
MAX_EXAMPLES = _positive_int("CCA_MAX_EXAMPLES", _DEFAULT_MAX_EXAMPLES)

# Relative divergence between the float64 result and the high-precision reference
# above which a numeric finding is CONFIRMED. A well-conditioned float64 result
# lands within ~1e-15 of exact; 1e-9 is "worse than float32", i.e. real precision
# loss rather than ordinary representation noise. Bounded to
# [_MIN_SUBSTRATE_TOL, _MAX_SUBSTRATE_TOL] -- see those constants for why.
SUBSTRATE_TOL = _positive_float(
    "CCA_SUBSTRATE_TOL", _DEFAULT_SUBSTRATE_TOL,
    lo=_MIN_SUBSTRATE_TOL, hi=_MAX_SUBSTRATE_TOL,
)

# Decimal digits of precision for the reference substrate.
SUBSTRATE_DPS = _positive_int("CCA_SUBSTRATE_DPS", _DEFAULT_SUBSTRATE_DPS)


def _name_set(name: str, default: frozenset[str]) -> frozenset[str]:
    """A comma-separated identifier set, overridable. Empty/blank -> default.

    Same degrade-safely contract as the numeric knobs: a malformed override must
    not take the checker down. An override that parsed to the EMPTY set would be
    worse than malformed for CLOCK_STRONG_PARAMS -- it silently makes CONFIRMED
    unreachable, so the check keeps running and simply stops finding anything.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    parts = frozenset(p.strip() for p in raw.split(",") if p.strip())
    return parts or default


# Parameter names that mean "the caller injects the clock here". STRONG names are
# specific enough that a DEAD one sitting next to a wall-clock read is a proven
# defect (see clock_check). WEAK names are as often plain data as an injected
# clock -- `timestamp` is usually a value, not a clock -- so they only ever raise
# the question. Keeping the two tiers separate is what stops a name-based
# heuristic from licensing an automated edit.
CLOCK_STRONG_PARAMS = _name_set("CCA_CLOCK_STRONG_PARAMS", frozenset({
    "now", "as_of", "asof", "as_of_time", "as_of_ts", "as_of_date",
    "clock", "current_time", "sim_time", "simulated_time",
    "reference_time", "ref_time", "time_provider", "time_func", "timefunc",
}))
CLOCK_WEAK_PARAMS = _name_set("CCA_CLOCK_WEAK_PARAMS", frozenset({
    "ts", "timestamp", "at", "when", "today", "date", "start_time", "end_time",
}))
