# Substrate-Differential Check Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give numeric findings a reference substrate nobody authored, so precision and cancellation defects are caught without an auditor having to suspect them first.

**Architecture:** Run the target twice — once in float64, once under `mpmath` at 50 decimal digits with the target module's `math` bindings swapped for mpmath equivalents. Divergence beyond `1e-9` relative is the finding. The result type is integrity-gated: if the alternate run did not return an `mpf`, the substrate was lost and the verdict is UNCERTAIN, never "they agree." Exposed as a seventh property helper so it reuses the existing property-file flow, CLI, and verdict mapping unchanged.

**Tech Stack:** Python ≥3.10, pytest, Hypothesis, mpmath (both optional extras), setuptools.

**Spec:** `docs/superpowers/specs/2026-07-21-substrate-differential-design.md`

**Baseline:** `master` at or after `f91b1f4` (the DEEP self-audit hardening, PRs #18→#19→#20). 260 tests currently pass.

## Global Constraints

- Python `>=3.10`.
- `mpmath>=1.3` is an **optional** dependency, added to BOTH the `numeric` and `verify` extras. The core install stays dependency-free. Absence must yield UNCERTAIN, never a pass.
- **Tunables live in `cca_checks/config.py`**, environment-overridable under the `CCA_` prefix, with a malformed value falling back to the default rather than crashing. Never define a tunable in the module that consumes it.
- **`ValueError` means "this check could not meaningfully run."** It is not a `PropertyViolation`, so it emits no `PROPERTY … violated` line, so `property_check` maps it to UNCERTAIN. Use it for every substrate failure.
- `SUBSTRATE_TOL = 1e-9` default, `SUBSTRATE_DPS = 50` default.
- **Do NOT modify `cca_checks/property_check.py`.** The `ValueError` convention already routes substrate failures correctly.
- `properties.py` must stay importable with `mpmath` absent — it may re-export the helper but must not import mpmath at module level.
- Agent markdown exists in two copies that must not drift on disk: `claude-code/` (tracked) and `.claude/` (**untracked — never `git add`**).
- Commit after every task.

---

### Task 1: Substrate tunables in config.py

**Files:**
- Modify: `cca_checks/config.py`
- Test: `tests/test_config.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `cca_checks.config.SUBSTRATE_TOL: float` (default `1e-9`, env `CCA_SUBSTRATE_TOL`); `cca_checks.config.SUBSTRATE_DPS: int` (default `50`, env `CCA_SUBSTRATE_DPS`); `cca_checks.config._positive_float(name, default) -> float`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_config.py`:

```python
import importlib

from cca_checks import config


def _reload(monkeypatch, **env):
    for k, v in env.items():
        if v is None:
            monkeypatch.delenv(k, raising=False)
        else:
            monkeypatch.setenv(k, v)
    return importlib.reload(config)


def test_substrate_defaults(monkeypatch):
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL=None, CCA_SUBSTRATE_DPS=None)
    assert c.SUBSTRATE_TOL == 1e-9
    assert c.SUBSTRATE_DPS == 50


def test_substrate_tol_env_override(monkeypatch):
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL="1e-6")
    assert c.SUBSTRATE_TOL == 1e-6


def test_malformed_tol_falls_back_not_crashes(monkeypatch):
    # A bad env value must never take the deterministic layer down.
    c = _reload(monkeypatch, CCA_SUBSTRATE_TOL="loose")
    assert c.SUBSTRATE_TOL == 1e-9


def test_non_positive_tol_falls_back(monkeypatch):
    # A zero or negative tolerance would make every comparison a violation.
    for bad in ("0", "-1e-9"):
        c = _reload(monkeypatch, CCA_SUBSTRATE_TOL=bad)
        assert c.SUBSTRATE_TOL == 1e-9


def test_malformed_dps_falls_back(monkeypatch):
    c = _reload(monkeypatch, CCA_SUBSTRATE_DPS="lots")
    assert c.SUBSTRATE_DPS == 50


def test_positive_float_rejects_nan_and_inf(monkeypatch):
    # NaN compares false against everything; inf makes the check vacuous.
    for bad in ("nan", "inf", "-inf"):
        c = _reload(monkeypatch, CCA_SUBSTRATE_TOL=bad)
        assert c.SUBSTRATE_TOL == 1e-9
```

Restore the real module for the rest of the suite by reloading once at the end of the file:

```python
def test_module_restored_for_other_tests(monkeypatch):
    monkeypatch.delenv("CCA_SUBSTRATE_TOL", raising=False)
    monkeypatch.delenv("CCA_SUBSTRATE_DPS", raising=False)
    c = importlib.reload(config)
    assert c.SUBSTRATE_TOL == 1e-9
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_config.py -q -k substrate`
Expected: FAIL — `AttributeError: module 'cca_checks.config' has no attribute 'SUBSTRATE_TOL'`

- [ ] **Step 3: Write minimal implementation**

In `cca_checks/config.py`, add after `_DEFAULT_MAX_EXAMPLES`:

```python
_DEFAULT_SUBSTRATE_TOL = 1e-9
_DEFAULT_SUBSTRATE_DPS = 50
```

Add after `_positive_int`:

```python
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
```

Add `import math` to the module's imports (it currently imports only `os`).

Add at the end of the module:

```python
# Relative divergence between the float64 result and the high-precision reference
# above which a numeric finding is CONFIRMED. A well-conditioned float64 result
# lands within ~1e-15 of exact; 1e-9 is "worse than float32", i.e. real precision
# loss rather than ordinary representation noise.
SUBSTRATE_TOL = _positive_float("CCA_SUBSTRATE_TOL", _DEFAULT_SUBSTRATE_TOL)

# Decimal digits of precision for the reference substrate.
SUBSTRATE_DPS = _positive_int("CCA_SUBSTRATE_DPS", _DEFAULT_SUBSTRATE_DPS)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_config.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add cca_checks/config.py tests/test_config.py
git commit -m "feat(config): substrate tunables, with a float knob that rejects NaN and inf"
```

---

### Task 2: The substrate runner

**Files:**
- Create: `cca_checks/substrate.py`
- Test: `tests/test_substrate.py`
- Test fixture: `tests/fixtures/substrate/targets.py`

**Interfaces:**
- Consumes: `cca_checks.config.SUBSTRATE_DPS`.
- Produces: `SubstrateResult` (frozen dataclass, fields `value: object | None`, `reason: str | None`); `mpmath_bindings(fn)` context manager yielding `bool`; `run_under_substrate(fn, args, dps=SUBSTRATE_DPS) -> SubstrateResult`; module constant `MIN_DPS = 30`. Reasons are exactly: `"unavailable"`, `"not_patchable"`, `"substrate_lost"`, `"raised"`, `"bad_dps"`.

This task builds the risky half and tests it **directly**, without going through Hypothesis. Every mechanism below was prototyped against real code before this plan was written.

- [ ] **Step 1: Write the fixture targets**

Create `tests/fixtures/substrate/targets.py`:

```python
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
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_substrate.py`:

```python
import math
import sys

import pytest

from cca_checks.substrate import (
    MIN_DPS,
    SubstrateResult,
    mpmath_bindings,
    run_under_substrate,
)

pytest.importorskip("mpmath", reason="substrate extra not installed")

sys.path.insert(0, "tests/fixtures/substrate")
import targets  # noqa: E402


def test_arithmetic_only_survives():
    r = run_under_substrate(targets.arithmetic_only, (1.0, 2.0))
    assert r.reason is None
    assert float(r.value) == pytest.approx(1.5)


def test_from_math_import_binding_is_patched():
    # `unstable` uses a bare `cos` bound at import time. If the runner only
    # patched the `math` module, this would silently stay float64 and the
    # reference would be as wrong as the code under test.
    r = run_under_substrate(targets.unstable, (1e-8,))
    assert r.reason is None
    assert float(r.value) == pytest.approx(0.5, rel=1e-6)


def test_import_math_binding_is_patched():
    r = run_under_substrate(targets.stable, (1e-8,))
    assert r.reason is None
    assert float(r.value) == pytest.approx(0.5, rel=1e-6)


def test_substrate_lost_yields_no_value():
    # The spine of the design: a lost substrate must never produce a value that
    # could be compared and read as agreement.
    r = run_under_substrate(targets.loses_substrate, (0.5,))
    assert r.reason == "substrate_lost"
    assert r.value is None


def test_target_raising_is_reported_not_swallowed():
    r = run_under_substrate(targets.raises_always, (1.0,))
    assert r.reason == "raised"
    assert r.value is None


def test_dps_below_floor_is_rejected():
    # float64 carries ~15-17 significant digits; a reference below that is less
    # precise than the thing it references.
    r = run_under_substrate(targets.stable, (1e-8,), dps=MIN_DPS - 1)
    assert r.reason == "bad_dps"
    assert r.value is None


def test_bindings_are_restored_after_success():
    with mpmath_bindings(targets.stable):
        pass
    assert targets.math is math
    assert targets.cos is math.cos


def test_bindings_are_restored_after_exception():
    with pytest.raises(RuntimeError):
        with mpmath_bindings(targets.stable):
            raise RuntimeError("boom")
    assert targets.math is math
    assert targets.cos is math.cos


def test_unpatchable_target_is_reported():
    fn = lambda x: x  # noqa: E731
    fn.__module__ = "no.such.module.anywhere"
    r = run_under_substrate(fn, (1.0,))
    assert r.reason == "not_patchable"


def test_mpmath_absent_is_unavailable(monkeypatch):
    import cca_checks.substrate as sub
    monkeypatch.setattr(sub, "mpmath", None)
    r = run_under_substrate(targets.stable, (1e-8,))
    assert r.reason == "unavailable"
    assert r.value is None


def test_result_is_frozen():
    r = SubstrateResult(value=None, reason="unavailable")
    with pytest.raises(Exception):
        r.value = 1
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_substrate.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cca_checks.substrate'`

- [ ] **Step 4: Write minimal implementation**

Create `cca_checks/substrate.py`:

```python
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
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_substrate.py -q`
Expected: PASS, 11 passed

- [ ] **Step 6: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add cca_checks/substrate.py tests/test_substrate.py tests/fixtures/substrate/targets.py
git commit -m "feat(substrate): high-precision reference runner with an integrity gate"
```

---

### Task 3: The seventh helper, plus packaging

**Files:**
- Modify: `cca_checks/substrate.py`
- Modify: `cca_checks/properties.py`
- Modify: `pyproject.toml`
- Test: `tests/test_substrate.py` (append)

**Interfaces:**
- Consumes: `run_under_substrate`, `SubstrateResult` (Task 2); `cca_checks.config.SUBSTRATE_TOL` (Task 1); `cca_checks.properties.PropertyViolation`.
- Produces: `assert_substrate_agrees(fn, args) -> None`, importable from BOTH `cca_checks.substrate` and `cca_checks.properties`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_substrate.py`:

```python
from cca_checks.properties import PropertyViolation
from cca_checks.substrate import assert_substrate_agrees


def test_cancellation_is_a_violation():
    with pytest.raises(PropertyViolation) as e:
        assert_substrate_agrees(targets.unstable, (1e-8,))
    msg = str(e.value)
    assert msg.startswith("PROPERTY ")
    assert "substrate_agrees" in msg
    assert "inputs=" in msg


def test_stable_variant_does_not_violate():
    # Same maths, no cancellation. Proves the check discriminates rather than
    # flagging every float function.
    assert_substrate_agrees(targets.stable, (1e-8,))


def test_arithmetic_only_does_not_violate():
    assert_substrate_agrees(targets.arithmetic_only, (1.0, 2.0))


def test_sign_trap_does_not_violate():
    # THE BLINDNESS PROBE. The GBM sign defect is real and present, and both
    # substrates compute the same wrong formula, so they agree perfectly. This
    # layer cannot see formula errors; properties cover that class. Asserting it
    # keeps the documented division of labour honest.
    assert_substrate_agrees(targets.sign_trap, (0.1, 0.3, 1.0))


def test_substrate_failure_raises_value_error_not_violation():
    # ValueError emits no "PROPERTY ... violated" line, so property_check maps it
    # to UNCERTAIN. A PropertyViolation here would let a lost substrate CONFIRM.
    with pytest.raises(ValueError) as e:
        assert_substrate_agrees(targets.loses_substrate, (0.5,))
    assert "substrate_lost" in str(e.value)
    assert not isinstance(e.value, PropertyViolation)


def test_non_callable_target_is_rejected():
    with pytest.raises(ValueError):
        assert_substrate_agrees("not a function", (1.0,))


def test_helper_is_reexported_from_properties():
    from cca_checks import properties
    assert properties.assert_substrate_agrees is assert_substrate_agrees


def test_properties_imports_without_mpmath(monkeypatch):
    # properties.py must stay importable when the optional extra is absent.
    import importlib
    monkeypatch.setitem(sys.modules, "mpmath", None)
    importlib.reload(importlib.import_module("cca_checks.properties"))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_substrate.py -q -k "violat or reexport or callable"`
Expected: FAIL — `ImportError: cannot import name 'assert_substrate_agrees'`

- [ ] **Step 3: Write minimal implementation**

Append to `cca_checks/substrate.py`:

```python
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

    observed = fn(*args)
    reference = result.value

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
```

In `cca_checks/properties.py`, add at the END of the module (after all six helpers), so the vocabulary is a single import:

```python
# Re-exported so the vocabulary is one import. A module-level import is safe here
# even though `substrate` deals with an optional extra: substrate.py sets
# `mpmath = None` on ImportError rather than failing, so this succeeds whether or
# not the extra is installed, and `properties.assert_substrate_agrees` is the same
# object as `substrate.assert_substrate_agrees`.
from .substrate import assert_substrate_agrees  # noqa: E402,F401  (re-exported)
```

Place this import at the bottom of the module, not with the other imports at the top: `substrate.py` imports `PropertyViolation` from this module inside its function body, and keeping the re-export last makes the one-directional module-level dependency obvious to a reader.

In `pyproject.toml`, add `mpmath>=1.3` to both extras:

```toml
numeric = ["hypothesis>=6.0", "pytest>=7", "mpmath>=1.3"]
verify = ["hypothesis>=6.0", "pytest>=7", "pyright>=1.1.350", "semgrep>=1.50", "mpmath>=1.3"]
```

Leave the existing explanatory comments above `numeric` and `verify` intact.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_substrate.py -q`
Expected: PASS, 19 passed

Then confirm nothing regressed:
```bash
python -m pytest -q
```
Expected: PASS, 260 + new tests, no failures

- [ ] **Step 5: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add cca_checks/substrate.py cca_checks/properties.py pyproject.toml tests/test_substrate.py
git commit -m "feat(substrate): assert_substrate_agrees as the seventh helper"
```

---

### Task 4: End-to-end acceptance through the real CLI

**Files:**
- Create: `tests/fixtures/substrate/props_unstable.py`
- Create: `tests/fixtures/substrate/props_stable.py`
- Create: `tests/fixtures/substrate/props_sign_trap.py`
- Create: `tests/acceptance/test_substrate_suite.py`

**Interfaces:**
- Consumes: `assert_substrate_agrees` (Task 3); `cca_checks.hypo.cca_settings`; `cca_checks.property_check.run_properties`.
- Produces: nothing downstream. This proves Tasks 1-3 work through the real subprocess and the real verdict mapping, not through mocks.

- [ ] **Step 1: Write the property files**

Create `tests/fixtures/substrate/props_unstable.py`:

```python
import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings              # noqa: E402
from cca_checks.substrate import assert_substrate_agrees  # noqa: E402
from targets import unstable                          # noqa: E402


@cca_settings
@given(x=st.floats(1e-9, 1e-6))
def test_float64_matches_the_reference(x):
    assert_substrate_agrees(unstable, (x,))
```

Create `tests/fixtures/substrate/props_stable.py` — identical but importing and targeting `stable`:

```python
import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings              # noqa: E402
from cca_checks.substrate import assert_substrate_agrees  # noqa: E402
from targets import stable                            # noqa: E402


@cca_settings
@given(x=st.floats(1e-9, 1e-6))
def test_float64_matches_the_reference(x):
    assert_substrate_agrees(stable, (x,))
```

Create `tests/fixtures/substrate/props_sign_trap.py`:

```python
import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings              # noqa: E402
from cca_checks.substrate import assert_substrate_agrees  # noqa: E402
from targets import sign_trap                         # noqa: E402


@cca_settings
@given(
    mu=st.floats(-0.5, 0.5),
    vol=st.floats(0.01, 1.0),
    t=st.floats(0.01, 5.0),
)
def test_float64_matches_the_reference(mu, vol, t):
    # The sign defect is real and present. Both substrates compute the same wrong
    # formula, so this passes — and must.
    assert_substrate_agrees(sign_trap, (mu, vol, t))
```

- [ ] **Step 2: Write the acceptance suite**

Create `tests/acceptance/test_substrate_suite.py`:

```python
import pytest

from cca_checks.property_check import run_properties

pytest.importorskip("hypothesis", reason="numeric extra not installed")
pytest.importorskip("mpmath", reason="numeric extra not installed")

UNSTABLE = "tests/fixtures/substrate/props_unstable.py"
STABLE = "tests/fixtures/substrate/props_stable.py"
SIGN_TRAP = "tests/fixtures/substrate/props_sign_trap.py"


def test_cancellation_is_confirmed_with_a_falsifying_example():
    v = run_properties("SUB-ACC-1", UNSTABLE)
    assert v.verdict == "CONFIRMED"
    assert v.source == "hypothesis"
    assert "Falsifying example" in v.evidence
    assert "substrate_agrees" in v.evidence


def test_confirmation_is_reproducible():
    a = run_properties("SUB-ACC-1", UNSTABLE)
    b = run_properties("SUB-ACC-1", UNSTABLE)
    assert a.evidence == b.evidence


def test_the_stable_variant_is_not_confirmed():
    v = run_properties("SUB-ACC-2", STABLE)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"
    assert "no counterexample" in v.evidence


def test_sign_error_is_structurally_invisible_to_this_layer():
    # The blindness probe, end to end. A CONFIRMED here would mean the check is
    # reporting divergence where the two substrates genuinely agree.
    v = run_properties("SUB-ACC-3", SIGN_TRAP)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"
    assert "no counterexample" in v.evidence
```

- [ ] **Step 3: Run the suite**

This task is entirely test code; it exercises Tasks 1-3 through the real subprocess. There is no new implementation to drive out.

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/acceptance/test_substrate_suite.py -q`
Expected: PASS, 4 passed

If any test fails, the defect is in Task 1, 2, or 3 — fix it there and re-run. **Do not weaken a fixture or widen an input domain to make a test go green.**

- [ ] **Step 4: Run the full suite**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest -q`
Expected: PASS, no failures

- [ ] **Step 5: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add tests/fixtures/substrate tests/acceptance/test_substrate_suite.py
git commit -m "test(substrate): end-to-end acceptance, including the blindness probe"
```

---

### Task 5: Agent contracts and design record

**Files:**
- Modify: `claude-code/agents/cca-numeric-auditor.md`
- Modify: `claude-code/agents/cca-fp-check.md`
- Modify: `docs/v3-design.md`
- Copy: both agent files into `.claude/agents/` (do NOT `git add` them)

**Interfaces:**
- Consumes: the CLI behaviour from Tasks 1-4.
- Produces: the `properties:` key shape agents use to request a substrate check.

- [ ] **Step 1: Add the helper to the numeric auditor's key reference**

In `claude-code/agents/cca-numeric-auditor.md`, find the bulleted helper key reference (the list beginning `- **\`assert_bounded\`** — \`lo\`, \`hi\``) and add:

```markdown
- **`assert_substrate_agrees`** — `target`, `args`, `domains` only. **No tolerance key
  exists, deliberately.** This is the one helper with no authored relation: it compares
  float64 against a 50-digit reference, so nothing about it comes from whoever raised the
  finding. Letting a finding carry its own threshold would reintroduce exactly the
  correlation this helper exists to escape. Use it for precision loss, catastrophic
  cancellation, accumulation error, and rounding direction. It is **blind to sign and
  formula errors** — both substrates compute the same wrong formula — so pair it with
  `assert_monotonic_in` or `assert_limit` when the finding is about direction.
```

- [ ] **Step 2: Add the third template to fp-check**

In `claude-code/agents/cca-fp-check.md`, after the second template (the `assert_round_trips` one), add:

```markdown
  Third template, for `assert_substrate_agrees` (no tolerance argument — the threshold is
  fixed in `cca_checks.config`):
  ```python
  from hypothesis import given, strategies as st
  from cca_checks.hypo import cca_settings
  from cca_checks.substrate import assert_substrate_agrees
  from <module> import <target>

  @cca_settings
  @given(x=st.floats(1e-9, 1e-6))
  def test_property(x):
      assert_substrate_agrees(<target>, (x,))
  ```

  An `UNCERTAIN` whose evidence names `substrate_lost`, `not_patchable`, `raised`, or
  `unavailable` means the reference substrate never ran — the two values compared would
  have been float64 against float64. That is NOT agreement and NOT a refutation:
  investigate or escalate, never drop the finding.
```

- [ ] **Step 3: Record the slice in the design of record**

In `docs/v3-design.md`:

1. Add `substrate` to the `claim_type ∈ { … }` set (around line 50), after `numeric`.
2. Add a row to the §3.2 claim_type→checker table, matching the existing row format:

```markdown
| `substrate` | "float64 loses precision here: cancellation, accumulation, rounding" | the target run twice — float64 vs a 50-digit `mpmath` reference with the module's `math` bindings swapped — **shipped in v3.5**. Confirms only on divergence beyond `CCA_SUBSTRATE_TOL`; a lost substrate is `UNCERTAIN`, never agreement. Blind to sign and formula errors by construction. |
```

3. Add a roadmap entry under §7 after the v3.4 bullet, without renumbering anything:

```markdown
- **v3.5 (shipped)** — substrate-differential checks via `assert_substrate_agrees`, the
  seventh property helper. Adds the decorrelation v3.4 lacked: the property vocabulary is
  authored by the same agent that raised the finding, whereas nobody authors a substrate
  disagreement. Requires the `numeric` or `verify` extra (`mpmath>=1.3`); absent ⇒
  `UNCERTAIN`. See `docs/superpowers/specs/2026-07-21-substrate-differential-design.md`.
```

- [ ] **Step 4: Mirror to `.claude/` and verify no drift**

```bash
cd /c/Users/gde00/Documents/cca-audit
cp claude-code/agents/cca-numeric-auditor.md .claude/agents/cca-numeric-auditor.md
cp claude-code/agents/cca-fp-check.md        .claude/agents/cca-fp-check.md
diff -r claude-code/agents .claude/agents && echo "NO DRIFT"
```
Expected: `NO DRIFT`

- [ ] **Step 5: Verify the documented template actually runs**

The template in Step 2 is what an agent will copy. Prove it works before shipping it: write a scratch file OUTSIDE the repo following the template exactly, targeting `tests/fixtures/substrate/targets.py::unstable`, and run it through the real CLI.

```bash
python -m cca_checks numeric --finding-id SUB-DOC-1 --test <your scratch file>
```
Expected: `"verdict": "CONFIRMED"`, `"source": "hypothesis"`, evidence containing `substrate_agrees`.

Delete the scratch file afterwards. If the documented template does not produce that, fix the template — do not adjust the expectation.

- [ ] **Step 6: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add claude-code/agents docs/v3-design.md   # NOT .claude/, and no command file changed
git commit -m "feat(agents): substrate claim shape, template, and design record"
```

---

## Final verification

- [ ] `python -m pytest -q` — full suite green
- [ ] `diff -r claude-code/agents .claude/agents` — `NO DRIFT`
- [ ] `git status --porcelain` shows only `?? .claude/`
- [ ] In a clean venv with **no** extras: `pip install -e .` then `python -m cca_checks numeric --finding-id X --test tests/fixtures/substrate/props_unstable.py` must return `UNCERTAIN` naming the missing dependency — never a crash, never a pass
- [ ] Open a PR against `master`, referencing the spec and this plan
