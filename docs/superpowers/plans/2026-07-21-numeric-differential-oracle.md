# Numeric Differential Oracle Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `NUM-*` audit findings a mechanical settler that confirms them with an executable counterexample instead of an LLM re-reading the arithmetic.

**Architecture:** A fixed vocabulary of metamorphic property assertions (`properties.py`) is applied by an auditor-written property file, executed under Hypothesis in a subprocess (`property_check.py`), and surfaced as a `Verdict` through a new `numeric` CLI subcommand. Verdict semantics are asymmetric: a violated property yields `CONFIRMED` with a falsifying example; anything else yields `UNCERTAIN`. `FALSE_POSITIVE` is unreachable by design, because properties holding across a bounded search is not proof of correctness.

**Tech Stack:** Python ≥3.10, pytest, Hypothesis (optional extra), setuptools.

**Spec:** `docs/superpowers/specs/2026-07-21-numeric-differential-oracle-design.md`

## Global Constraints

- Python `>=3.10` (matches `pyproject.toml:9`).
- `hypothesis` is an **optional** dependency, declared as the extra `numeric`. The core install stays dependency-free. Its absence must yield `UNCERTAIN`, never a pass.
- `Verdict.source` for every verdict produced by this feature is the literal string `"hypothesis"`.
- Evidence strings for non-decisive outcomes must end in `"; escalated"`, matching `repro_runner.py`.
- `MAX_EXAMPLES = 200`, `TIMEOUT_S = 120`.
- Subprocess invocation must use `sys.executable` (never bare `"python"`), pass `--` before any path, set `encoding="utf-8"`, and set a timeout. This is locked in by test, mirroring `tests/test_repro_runner.py:47`.
- Agent markdown exists in **two copies** that must never drift on disk: `claude-code/agents/` and `.claude/agents/`. Every agent edit applies to both. **Only `claude-code/` is tracked in git** — `.claude/` is the local dogfooding mirror, generated from `claude-code/` by `install.sh`, and must stay untracked. Sync it, do not commit it.
- Do not modify `cca_checks/claim.py`.
- Commit after every task.

---

### Task 1: Property vocabulary

**Files:**
- Create: `cca_checks/properties.py`
- Create: `cca_checks/hypo.py`
- Test: `tests/test_properties.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `MAX_EXAMPLES: int = 200`; `class PropertyViolation(AssertionError)`; six assertion helpers `assert_bounded`, `assert_monotonic_in`, `assert_limit`, `assert_scale_invariant`, `assert_sign_symmetric`, `assert_round_trips` — all returning `None` and raising `PropertyViolation` on failure; `cca_checks.hypo.cca_settings` (a Hypothesis `settings` instance).

- [ ] **Step 1: Write the failing test**

Create `tests/test_properties.py`:

```python
import math

import pytest

from cca_checks.properties import (
    PropertyViolation,
    assert_bounded,
    assert_limit,
    assert_monotonic_in,
    assert_round_trips,
    assert_scale_invariant,
    assert_sign_symmetric,
)


# --- assert_bounded --------------------------------------------------------

def test_bounded_passes_inside_the_range():
    assert_bounded(lambda x: x / 2, (1.0,), lo=0.0, hi=1.0)


def test_bounded_violation_names_the_observed_value():
    with pytest.raises(PropertyViolation) as e:
        assert_bounded(lambda x: x * 5, (1.0,), lo=0.0, hi=1.0)
    assert "bounded" in str(e.value)
    assert "5" in str(e.value)


def test_bounded_rejects_nan():
    with pytest.raises(PropertyViolation):
        assert_bounded(lambda x: math.nan, (1.0,), lo=0.0, hi=1.0)


# --- assert_monotonic_in ---------------------------------------------------

def test_monotonic_increasing_passes():
    assert_monotonic_in(lambda a, b: a + b, (1.0, 2.0), index=1,
                        direction="increasing", delta=0.5)


def test_monotonic_decreasing_catches_a_flipped_sign():
    # The motivating bug shape: a term that must reduce the result increases it.
    def buggy(mu, vol):
        return mu + 0.5 * vol ** 2

    with pytest.raises(PropertyViolation) as e:
        assert_monotonic_in(buggy, (0.1, 0.3), index=1,
                            direction="decreasing", delta=0.5)
    assert "monotonic" in str(e.value)


def test_monotonic_rejects_an_unknown_direction():
    with pytest.raises(ValueError):
        assert_monotonic_in(lambda a: a, (1.0,), index=0,
                            direction="sideways", delta=0.1)


# --- assert_limit ----------------------------------------------------------

def test_limit_passes_at_the_degenerate_case():
    assert_limit(lambda mu, vol: mu - 0.5 * vol ** 2, (0.2, 1.0), index=1,
                 approaching=0.0, expected=0.2)


def test_limit_violation_reports_expected_and_observed():
    with pytest.raises(PropertyViolation) as e:
        assert_limit(lambda mu, vol: mu + vol + 1.0, (0.2, 1.0), index=1,
                     approaching=0.0, expected=0.2)
    assert "limit" in str(e.value)


# --- assert_scale_invariant ------------------------------------------------

def test_scale_invariant_passes_for_a_ratio():
    assert_scale_invariant(lambda a, b: a / b, (4.0, 2.0), factor=10.0,
                           indices=(0, 1))


def test_scale_invariant_catches_a_stray_absolute_term():
    with pytest.raises(PropertyViolation) as e:
        assert_scale_invariant(lambda a, b: a / b + a, (4.0, 2.0), factor=10.0,
                               indices=(0, 1))
    assert "scale" in str(e.value)


# --- assert_sign_symmetric -------------------------------------------------

def test_sign_symmetric_odd_passes():
    assert_sign_symmetric(lambda x: x ** 3, (2.0,), index=0, kind="odd")


def test_sign_symmetric_odd_catches_a_swapped_subtraction():
    with pytest.raises(PropertyViolation):
        assert_sign_symmetric(lambda x: x + 1.0, (2.0,), index=0, kind="odd")


def test_sign_symmetric_even_passes():
    assert_sign_symmetric(lambda x: x ** 2, (2.0,), index=0, kind="even")


def test_sign_symmetric_rejects_an_unknown_kind():
    with pytest.raises(ValueError):
        assert_sign_symmetric(lambda x: x, (1.0,), index=0, kind="weird")


# --- assert_round_trips ----------------------------------------------------

def test_round_trip_passes():
    assert_round_trips(lambda x: x * 100.0, lambda y: y / 100.0, 1.23)


def test_round_trip_catches_a_lost_factor():
    with pytest.raises(PropertyViolation) as e:
        assert_round_trips(lambda x: x * 100.0, lambda y: y / 10.0, 1.23)
    assert "round" in str(e.value)


# --- message shape ---------------------------------------------------------

def test_violation_message_carries_inputs_observed_and_required():
    with pytest.raises(PropertyViolation) as e:
        assert_bounded(lambda x: 9.0, (1.0,), lo=0.0, hi=1.0)
    msg = str(e.value)
    assert msg.startswith("PROPERTY ")
    assert "inputs=" in msg and "observed=" in msg and "required=" in msg
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_properties.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cca_checks.properties'`

- [ ] **Step 3: Write minimal implementation**

Create `cca_checks/properties.py`:

```python
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
```

Create `cca_checks/hypo.py`:

```python
"""The determinism contract for generated property files.

Isolated from `properties.py` so that module stays importable without the
optional `hypothesis` dependency. Importing this module when hypothesis is
absent raises ModuleNotFoundError, which `property_check` maps to UNCERTAIN.

Determinism lives here rather than in the generated test: an audit that reports
a different falsifying input on each run is not an artifact, and that guarantee
must not depend on the auditor remembering to write a setting.
"""

from hypothesis import settings

from .properties import MAX_EXAMPLES

cca_settings = settings(
    derandomize=True,
    max_examples=MAX_EXAMPLES,
    deadline=None,  # audit targets may be slow; a deadline would add flaky failures
)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_properties.py -q`
Expected: PASS, 17 passed

- [ ] **Step 5: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add cca_checks/properties.py cca_checks/hypo.py tests/test_properties.py
git commit -m "feat(properties): metamorphic assertion vocabulary for numeric claims"
```

---

### Task 2: Property runner and verdict mapping

**Files:**
- Create: `cca_checks/property_check.py`
- Test: `tests/test_property_check.py`

**Interfaces:**
- Consumes: `cca_checks.properties.MAX_EXAMPLES`; `cca_checks.claim.Verdict`, `cca_checks.claim.make_verdict`.
- Produces: `run_properties(finding_id: str, test_path: str) -> Verdict`; module constants `TIMEOUT_S: int = 120`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_property_check.py`:

```python
import subprocess
import sys

import pytest

from cca_checks import property_check as pcheck
from cca_checks.property_check import run_properties

FALSIFYING = """
E   cca_checks.properties.PropertyViolation: PROPERTY monotonic violated | inputs=(0.1, 0.3) | observed=(0.145, 0.26) | required=result non-increasing in arg 1

Falsifying example: test_growth(
    mu=0.1,
    vol=0.3,
)
"""


class _Proc:
    def __init__(self, rc, out="", err=""):
        self.returncode = rc
        self.stdout = out
        self.stderr = err


def fake(rc, out="", err=""):
    return lambda *a, **k: _Proc(rc, out, err)


def test_property_violation_confirms(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake(1, FALSIFYING))
    v = run_properties("NUM-1", "t_NUM-1_props.py")
    assert v.verdict == "CONFIRMED"
    assert v.source == "hypothesis"
    assert "Falsifying example" in v.evidence


def test_clean_run_is_uncertain_never_refuted(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake(0, "2 passed"))
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
    assert "200 examples" in v.evidence
    assert v.evidence.endswith("; escalated")


def test_missing_hypothesis_is_uncertain_not_a_pass(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        fake(2, "ModuleNotFoundError: No module named 'hypothesis'"))
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert "hypothesis not installed" in v.evidence


def test_missing_hypothesis_is_detected_before_the_returncode(monkeypatch):
    # A missing module can surface as rc=1 or rc=2 depending on where it is
    # imported. The dependency check must not depend on which.
    monkeypatch.setattr(subprocess, "run",
                        fake(1, "ModuleNotFoundError: No module named 'hypothesis'"))
    v = run_properties("NUM-1", "t.py")
    assert "hypothesis not installed" in v.evidence


def test_collection_error_is_uncertain_never_confirmed(monkeypatch):
    monkeypatch.setattr(subprocess, "run", fake(4, "ERROR: file or directory not found"))
    v = run_properties("NUM-1", "nope.py")
    assert v.verdict == "UNCERTAIN"
    assert "rc=4" in v.evidence


def test_failure_without_a_falsifying_example_is_uncertain(monkeypatch):
    # A plain assertion failure is not a property violation. Reading it as one
    # would let any red test CONFIRM a numeric finding.
    monkeypatch.setattr(subprocess, "run", fake(1, "assert 1 == 2"))
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert "without a falsifying example" in v.evidence


def test_timeout_escalates_to_uncertain(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="pytest", timeout=120)
    monkeypatch.setattr(subprocess, "run", boom)
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"
    assert v.source == "hypothesis"
    assert "timed out" in v.evidence


def test_launch_failure_escalates_to_uncertain(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError("interpreter not found")
    monkeypatch.setattr(subprocess, "run", boom)
    v = run_properties("NUM-1", "t.py")
    assert v.verdict == "UNCERTAIN"


def test_false_positive_is_unreachable(monkeypatch):
    # The asymmetry is the contract: this checker may never refute a finding.
    for rc, out in [(0, "passed"), (1, FALSIFYING), (1, "assert 1 == 2"), (5, "no tests")]:
        monkeypatch.setattr(subprocess, "run", fake(rc, out))
        assert run_properties("NUM-1", "t.py").verdict != "FALSE_POSITIVE"


def test_invocation_is_hardened(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["kwargs"] = kwargs
        return _Proc(0, "")

    monkeypatch.setattr(subprocess, "run", fake_run)
    run_properties("NUM-1", "t.py")
    argv, kw = captured["argv"], captured["kwargs"]
    assert argv[0] == sys.executable
    assert "--" in argv
    assert argv[-1] == "t.py"
    assert kw.get("encoding") == "utf-8"
    assert kw.get("timeout") == pcheck.TIMEOUT_S
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_property_check.py -q`
Expected: FAIL — `ModuleNotFoundError: No module named 'cca_checks.property_check'`

- [ ] **Step 3: Write minimal implementation**

Create `cca_checks/property_check.py`:

```python
"""Settle a numeric claim by executing declared properties under Hypothesis.

WARNING: run_properties executes the target's code. pytest imports conftest.py
during collection, so running this against a repo you do not trust executes that
repo's code with your privileges and environment. Do not point it at untrusted
code without a sandbox (container / seccomp / a scrubbed, offline env).

Verdict asymmetry, and why: a violated property yields a concrete falsifying
input, which is evidence. Properties *holding* across a bounded search is not
evidence of correctness — it is only the absence of a counterexample. So this
checker can CONFIRM but can never return FALSE_POSITIVE. That mirrors
semgrep_check, where the reachable verdicts are the other way round.
"""

import re
import subprocess
import sys

from .claim import Verdict, make_verdict
from .properties import MAX_EXAMPLES

TIMEOUT_S = 120
SOURCE = "hypothesis"

# Hypothesis prints the shrunk input under this banner, up to a blank line.
_FALSIFYING = re.compile(r"Falsifying example:.*?(?=\n\s*\n|\Z)", re.S)
# Our own violation message, which names the property and the required relation.
_PROPERTY_LINE = re.compile(r"^.*PROPERTY .+ violated \|.*$", re.M)
_NO_HYPOTHESIS = "No module named 'hypothesis'"


def _uncertain(finding_id: str, why: str) -> Verdict:
    return make_verdict(finding_id, "UNCERTAIN", why, SOURCE)


def run_properties(finding_id: str, test_path: str) -> Verdict:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-xq", "-p", "no:cacheprovider",
             "--", test_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return _uncertain(finding_id,
                          f"property check timed out after {TIMEOUT_S}s; escalated")
    except OSError:
        return _uncertain(finding_id,
                          "property check could not run (pytest unavailable); escalated")

    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-800:]

    # Checked before the returncode: a missing optional dependency surfaces as
    # rc=1 or rc=2 depending on where the import sits, and neither is a result.
    if _NO_HYPOTHESIS in out:
        return _uncertain(finding_id,
                          "property check unavailable (hypothesis not installed); escalated")

    rc = proc.returncode
    if rc == 0:
        return _uncertain(finding_id,
                          f"no counterexample in {MAX_EXAMPLES} examples; escalated")
    if rc != 1:
        return _uncertain(finding_id,
                          f"property check could not run/collect (pytest rc={rc}); "
                          f"escalated:\n{tail}")

    example = _FALSIFYING.search(out)
    if not example:
        # rc==1 means a test failed, but without Hypothesis's banner it was a
        # plain assertion, not a property violation. Confirming on that would
        # let any red test settle a numeric finding.
        return _uncertain(finding_id,
                          f"property test failed without a falsifying example "
                          f"(not a property violation); escalated:\n{tail}")

    prop = _PROPERTY_LINE.search(out)
    evidence = "property violated:\n" + example.group(0).strip()
    if prop:
        evidence += "\n" + prop.group(0).strip()
    return make_verdict(finding_id, "CONFIRMED", evidence, SOURCE)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_property_check.py -q`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add cca_checks/property_check.py tests/test_property_check.py
git commit -m "feat(property_check): run declared properties, map outcomes to verdicts"
```

---

### Task 3: CLI subcommand and optional extra

**Files:**
- Modify: `cca_checks/__main__.py:1-66`
- Modify: `pyproject.toml:5-16`
- Test: `tests/test_cli.py` (append)

**Interfaces:**
- Consumes: `cca_checks.property_check.run_properties`.
- Produces: CLI `python -m cca_checks numeric --finding-id <ID> --test <PATH>` emitting the `Verdict` as JSON on stdout, exit code 0. `run_properties` is imported into `__main__`'s namespace so tests may monkeypatch `cli.run_properties`, matching how `cli.run_repro` is patched at `tests/test_cli.py:97`.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_cli.py`:

```python
def test_numeric_subcommand_emits_a_verdict(capsys, monkeypatch):
    monkeypatch.setattr(cli, "run_properties",
                        lambda fid, test: make_verdict(fid, "CONFIRMED",
                                                       "property violated:\nFalsifying example: f(x=1.0)",
                                                       "hypothesis"))
    out = run(capsys, ["numeric", "--finding-id", "NUM-1", "--test", "t_NUM-1_props.py"])
    assert out["finding_id"] == "NUM-1"
    assert out["verdict"] == "CONFIRMED"
    assert out["source"] == "hypothesis"


def test_numeric_subcommand_passes_the_test_path_through(capsys, monkeypatch):
    captured = {}

    def spy(fid, test):
        captured["fid"] = fid
        captured["test"] = test
        return make_verdict(fid, "UNCERTAIN", "stub; escalated", "hypothesis")

    monkeypatch.setattr(cli, "run_properties", spy)
    run(capsys, ["numeric", "--finding-id", "NUM-2", "--test", "props.py"])
    assert captured == {"fid": "NUM-2", "test": "props.py"}


def test_numeric_requires_a_test_path():
    with pytest.raises(SystemExit):
        cli.main(["numeric", "--finding-id", "NUM-3"])


def test_numeric_is_not_a_check_claim_type():
    # `check` settles a static claim at a file:line; `numeric` executes a test
    # file. Conflating them would put an unusable --file/--line on the numeric path.
    assert "numeric" not in cli.CLAIM_TYPES
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_cli.py -q -k numeric`
Expected: FAIL — `AttributeError: module 'cca_checks.__main__' has no attribute 'run_properties'`

- [ ] **Step 3: Write minimal implementation**

In `cca_checks/__main__.py`, add the import after line 8 (`from .repro_runner import run_repro`):

```python
from .property_check import run_properties
```

Add the subparser immediately after the `repro` block (currently lines 49-52):

```python
    n = sub.add_parser("numeric", help="settle a numeric claim by running declared properties")
    n.add_argument("--finding-id", required=True)
    n.add_argument("--test", required=True)
```

Replace the dispatch block (currently lines 55-60) with:

```python
    if a.cmd == "check":
        v = _check(a.claim_type, a)
    elif a.cmd == "definedness":
        v = _check("definedness", a)
    elif a.cmd == "numeric":
        v = run_properties(a.finding_id, a.test)
    else:
        v = run_repro(a.finding_id, a.test, a.expect_error)
```

In `pyproject.toml`, bump the version and add the extra. Replace lines 5-10:

```toml
[project]
name = "cca_checks"
version = "0.4.0"
description = "Deterministic verification helpers for CCA-Audit's fp-check gate"
requires-python = ">=3.10"

[project.optional-dependencies]
numeric = ["hypothesis>=6.0"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /c/Users/gde00/Documents/cca-audit && python -m pytest tests/test_cli.py -q`
Expected: PASS, all tests including the 4 new ones

- [ ] **Step 5: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add cca_checks/__main__.py pyproject.toml tests/test_cli.py
git commit -m "feat(cli): numeric subcommand + optional hypothesis extra"
```

---

### Task 4: End-to-end acceptance and the blindness probe

**Files:**
- Create: `tests/fixtures/numeric/drift.py`
- Create: `tests/fixtures/numeric/props_violated.py`
- Create: `tests/fixtures/numeric/props_fixed.py`
- Create: `tests/fixtures/numeric/props_hold.py`
- Create: `tests/acceptance/test_numeric_suite.py`

**Interfaces:**
- Consumes: `run_properties` (Task 2); `cca_checks.properties` helpers and `cca_checks.hypo.cca_settings` (Task 1).
- Produces: nothing consumed downstream. This task proves the wiring against a real subprocess rather than a mock.

- [ ] **Step 1: Write the failing test**

Create `tests/fixtures/numeric/drift.py` — the trap and its corrected twin:

```python
"""A sign trap and its corrected twin.

`expected_log_growth` carries the defect this feature exists to catch: the
variance term enters with the wrong sign. The expression is well formed and the
names are right; only the meaning is inverted, which is exactly what reads as
correct on review.
"""


def expected_log_growth(mu: float, vol: float, t: float) -> float:
    """BUGGY: variance should drag growth down, not push it up."""
    return (mu + 0.5 * vol ** 2) * t


def expected_log_growth_fixed(mu: float, vol: float, t: float) -> float:
    """CORRECT: variance drag reduces expected log growth."""
    return (mu - 0.5 * vol ** 2) * t
```

Create `tests/fixtures/numeric/props_violated.py` — a property that catches it:

```python
import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings          # noqa: E402
from cca_checks.properties import assert_monotonic_in  # noqa: E402
from drift import expected_log_growth            # noqa: E402


@cca_settings
@given(
    mu=st.floats(-0.5, 0.5),
    vol=st.floats(0.01, 1.0),
    t=st.floats(0.01, 5.0),
)
def test_growth_decreases_with_volatility(mu, vol, t):
    # Intended relation, stated independently of the implementation:
    # more volatility must not raise expected log growth.
    assert_monotonic_in(expected_log_growth, (mu, vol, t), index=1,
                        direction="decreasing", delta=0.1)
```

Create `tests/fixtures/numeric/props_fixed.py` — the same property against the corrected twin:

```python
import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings              # noqa: E402
from cca_checks.properties import assert_monotonic_in  # noqa: E402
from drift import expected_log_growth_fixed          # noqa: E402


@cca_settings
@given(
    mu=st.floats(-0.5, 0.5),
    vol=st.floats(0.01, 1.0),
    t=st.floats(0.01, 5.0),
)
def test_growth_decreases_with_volatility(mu, vol, t):
    # Identical property, correct implementation. Proves the property discriminates
    # between the two rather than failing on everything it is pointed at.
    assert_monotonic_in(expected_log_growth_fixed, (mu, vol, t), index=1,
                        direction="decreasing", delta=0.1)
```

Create `tests/fixtures/numeric/props_hold.py` — the blindness probe:

```python
import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings      # noqa: E402
from cca_checks.properties import assert_limit  # noqa: E402
from drift import expected_log_growth        # noqa: E402


@cca_settings
@given(mu=st.floats(-0.5, 0.5), t=st.floats(0.01, 5.0))
def test_limit_at_zero_volatility(mu, t):
    # This property HOLDS on the buggy function: at vol == 0 the flipped term
    # vanishes, so the defect is invisible here. Keeping it proves the checker
    # returns UNCERTAIN rather than pretending a clean run refutes the finding.
    assert_limit(expected_log_growth, (mu, 0.5, t), index=1,
                 approaching=0.0, expected=mu * t)
```

Create `tests/acceptance/test_numeric_suite.py`:

```python
import pytest

from cca_checks.property_check import run_properties

pytest.importorskip("hypothesis", reason="numeric extra not installed")

VIOLATED = "tests/fixtures/numeric/props_violated.py"
FIXED = "tests/fixtures/numeric/props_fixed.py"
HOLD = "tests/fixtures/numeric/props_hold.py"


def test_sign_trap_is_confirmed_with_a_falsifying_example():
    v = run_properties("NUM-ACC-1", VIOLATED)
    assert v.verdict == "CONFIRMED"
    assert v.source == "hypothesis"
    assert "Falsifying example" in v.evidence
    assert "monotonic" in v.evidence


def test_the_corrected_twin_is_not_confirmed():
    # The same property against the fixed implementation. A checker that
    # confirmed both would be discriminating nothing.
    v = run_properties("NUM-ACC-3", FIXED)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "CONFIRMED"


def test_confirmation_is_reproducible():
    # derandomize=True: the same audit must yield the same artifact.
    a = run_properties("NUM-ACC-1", VIOLATED)
    b = run_properties("NUM-ACC-1", VIOLATED)
    assert a.evidence == b.evidence


def test_a_property_that_holds_on_buggy_code_never_refutes():
    # The honest blindness case. The defect is real and still present; this
    # property simply cannot see it. UNCERTAIN, never FALSE_POSITIVE.
    v = run_properties("NUM-ACC-2", HOLD)
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"
    assert "no counterexample" in v.evidence
```

- [ ] **Step 2: Confirm the suite is skipped without the extra**

This task inverts the usual TDD order: it is entirely test code, and it exercises Tasks 1-3 against
a real subprocess rather than a mock. There is no new implementation to drive out. What must be
verified first is the *skip* path — that a machine without the extra degrades cleanly.

Run, in an environment where `hypothesis` is NOT installed:
```bash
cd /c/Users/gde00/Documents/cca-audit
python -m pytest tests/acceptance/test_numeric_suite.py -q
```
Expected: `1 skipped` — a module-level `pytest.importorskip` skips the whole module as one unit, so the count is 1, not one-per-test. Never an error, never a failure.

- [ ] **Step 3: Install the extra**

```bash
cd /c/Users/gde00/Documents/cca-audit
python -m pip install -e ".[numeric]"
```
Expected: `hypothesis` installed, `cca_checks` re-installed in editable mode.

If any acceptance test fails after this, the defect is in Task 1 or Task 2 — fix it there and
re-run. Do not patch the fixtures to make a failing checker look green.

- [ ] **Step 4: Run test to verify it passes**

Run:
```bash
cd /c/Users/gde00/Documents/cca-audit
python -m pytest tests/acceptance/test_numeric_suite.py -q
```
Expected: PASS, 4 passed

Then the full suite, to confirm nothing regressed:
```bash
python -m pytest -q
```
Expected: PASS, no failures

- [ ] **Step 5: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add tests/fixtures/numeric tests/acceptance/test_numeric_suite.py
git commit -m "test(numeric): end-to-end sign-trap acceptance + blindness probe"
```

---

### Task 5: Agent contracts and tier rule

**Files:**
- Modify: `claude-code/agents/cca-numeric-auditor.md:69-74` (Output Format section)
- Modify: `claude-code/agents/cca-fp-check.md:65-101` (Phase 1 claim-type list)
- Modify: `claude-code/commands/audit-fix.md:159` and `:368-370`
- Copy: the same three edits into `.claude/agents/` and `.claude/commands/`

**Interfaces:**
- Consumes: the CLI from Task 3.
- Produces: the `properties:` block contract that fp-check reads to generate a property file.

- [ ] **Step 1: Add the properties block to the numeric auditor**

In `claude-code/agents/cca-numeric-auditor.md`, replace the Output Format section (lines 69-74):

```markdown
## Output Format

Report findings grouped by severity: Critical > High > Medium > Low.
Each finding: `### NUM-NNN: Title` with Severity, File:line, the dimensional/sign mismatch,
the concrete consequence, and the Fix.

Every finding MUST also carry a `properties:` block naming the metamorphic properties that
would expose the defect, and the input domain over which they hold. This is what lets L2.5
settle the finding by execution instead of by re-reading the arithmetic — a sign error reads
fluently, so a second reading is not evidence.

```yaml
properties:
  - helper: assert_monotonic_in     # one of: assert_bounded | assert_monotonic_in |
                                    # assert_limit | assert_scale_invariant |
                                    # assert_sign_symmetric | assert_round_trips
    target: expected_log_growth     # the function under test
    args: [mu, vol, t]              # positional argument names, in order
    index: 1                        # which argument the property is about
    direction: decreasing           # helper-specific parameters
    delta: 0.1
    domains:                        # REQUIRED — unbounded floats are not allowed
      mu: [-0.5, 0.5]
      vol: [0.01, 1.0]
      t: [0.01, 5.0]
    rationale: variance drag must not raise expected log growth
```

State the property as the **intended relation**, derived from what the function is supposed to
mean — never from what the code does. A property read off the implementation is a tautology: it
passes on buggy code and proves nothing.

Declare `domains` from the values the function actually receives in production. A counterexample
found outside that range is an artifact, not a defect.
```

- [ ] **Step 2: Add the numeric claim type to fp-check**

In `claude-code/agents/cca-fp-check.md`, insert after the `taint` bullet (which ends at line 79, before the `crash_impact` bullet):

```markdown
- **`numeric`** — the finding asserts an arithmetic defect: wrong sign, mixed units, bad scaling,
  wrong rounding direction, a conversion that does not invert. FIRST write a property file
  `t_<ID>_props.py` from the finding's `properties:` block, applying `@cca_settings` and
  `@given(...)` with the declared domains, THEN:
  `python -m cca_checks numeric --finding-id <ID> --test t_<ID>_props.py`

  Template:
  ```python
  from hypothesis import given, strategies as st
  from cca_checks.hypo import cca_settings
  from cca_checks.properties import assert_monotonic_in
  from <module> import <target>

  @cca_settings
  @given(mu=st.floats(-0.5, 0.5), vol=st.floats(0.01, 1.0), t=st.floats(0.01, 5.0))
  def test_property(mu, vol, t):
      assert_monotonic_in(<target>, (mu, vol, t), index=1, direction="decreasing", delta=0.1)
  ```

  **Numeric verdicts are asymmetric, the mirror of taint.** The checker never returns
  `FALSE_POSITIVE` for a `numeric` claim: properties holding across a bounded search is not proof
  of correctness, only the absence of a counterexample. A `CONFIRMED` carries a falsifying example
  and is binding. An `UNCERTAIN` reading "no counterexample" means your property could not see the
  defect — try a different property or escalate; it is NOT a refutation.

  **Unlike a repro test, do NOT delete the property file.** A `CONFIRMED` property file moves into
  the target's test suite as part of the fix: the property that caught the bug is the regression
  test proving the fix satisfies it.
```

- [ ] **Step 3: Add the tier rule to audit-fix.md**

In `claude-code/commands/audit-fix.md`, replace line 159:

```markdown
| L2.5 findings verification | — | single fp-check | fp-check + **adversarial 2-of-3 on high-stakes P1** + **`numeric` artifact required on NUM-\* P1** |
```

Then append after line 370 (the "Apply verdicts" paragraph):

```markdown
**DEEP tier — NUM-\* P1 artifact rule.** A `NUM-*` P1 may NOT enter the fix plan on an
`llm`-sourced verdict. It carries a `hypothesis` artifact (a falsifying example from
`python -m cca_checks numeric`) or it is escalated as UNCERTAIN. A numeric defect that an LLM
merely re-read and approved is unverified — a sign error reads fluently, which is precisely why
this class needs execution rather than a second opinion. FAST and STANDARD are unaffected: the
claim type is available there, but nothing blocks on it.
```

- [ ] **Step 4: Mirror all three edits into the `.claude/` copies**

The repo ships two copies of every agent and command. They must not drift.

```bash
cd /c/Users/gde00/Documents/cca-audit
cp claude-code/agents/cca-numeric-auditor.md .claude/agents/cca-numeric-auditor.md
cp claude-code/agents/cca-fp-check.md        .claude/agents/cca-fp-check.md
cp claude-code/commands/audit-fix.md         .claude/commands/audit-fix.md
```

Verify no drift remains:
```bash
diff -r claude-code/agents .claude/agents && diff claude-code/commands/audit-fix.md .claude/commands/audit-fix.md && echo "NO DRIFT"
```
Expected: `NO DRIFT`

- [ ] **Step 5: Commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
git add claude-code/agents claude-code/commands   # NOT .claude/ — it is an untracked local mirror
git commit -m "feat(agents): numeric claim type, properties block, DEEP artifact rule"
```

---

### Task 6: Worked example and README

**Files:**
- Create: `examples/sign-trap/README.md`
- Create: `examples/sign-trap/growth.py`
- Create: `examples/sign-trap/t_NUM-001_props.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the public-facing demonstration, in the style of the existing `examples/bps-sizing/definedness_trap`.

- [ ] **Step 1: Write the example target**

Create `examples/sign-trap/growth.py`:

```python
"""A sign trap: the expression is well formed, the names are right, the meaning is inverted."""


def expected_log_growth(mu: float, vol: float, t: float) -> float:
    """Expected log growth over horizon t.

    Variance drag should REDUCE expected log growth. This returns the opposite.
    """
    return (mu + 0.5 * vol ** 2) * t
```

- [ ] **Step 2: Write the property file**

Create `examples/sign-trap/t_NUM-001_props.py`:

```python
import os
import sys

from hypothesis import given, strategies as st

sys.path.insert(0, os.path.dirname(__file__))

from cca_checks.hypo import cca_settings              # noqa: E402
from cca_checks.properties import assert_monotonic_in  # noqa: E402
from growth import expected_log_growth               # noqa: E402


@cca_settings
@given(
    mu=st.floats(-0.5, 0.5),
    vol=st.floats(0.01, 1.0),
    t=st.floats(0.01, 5.0),
)
def test_growth_decreases_with_volatility(mu, vol, t):
    assert_monotonic_in(expected_log_growth, (mu, vol, t), index=1,
                        direction="decreasing", delta=0.1)
```

- [ ] **Step 3: Write the example README**

Create `examples/sign-trap/README.md`:

```markdown
# Sign trap — settling a numeric finding by execution

A sign error is the bug class that survives review. The expression parses, the variable names are
right, and only the meaning is inverted — so a careful second reading tends to approve it.

`growth.py` computes expected log growth as `(mu + 0.5*vol**2) * t`. The variance term should
reduce growth, not raise it.

## The finding

The numeric-auditor reports it with the property that would expose it:

```yaml
properties:
  - helper: assert_monotonic_in
    target: expected_log_growth
    args: [mu, vol, t]
    index: 1
    direction: decreasing
    delta: 0.1
    domains:
      mu: [-0.5, 0.5]
      vol: [0.01, 1.0]
      t: [0.01, 5.0]
    rationale: variance drag must not raise expected log growth
```

Note what the property states: the *intended* relation, derived from what the function is supposed
to mean. It is not readable off the implementation — which is what stops it being a tautology.

## Settling it

```bash
pip install -e ".[numeric]"
python -m cca_checks numeric --finding-id NUM-001 --test examples/sign-trap/t_NUM-001_props.py
```

```json
{
  "finding_id": "NUM-001",
  "verdict": "CONFIRMED",
  "evidence": "property violated:\nFalsifying example: test_growth_decreases_with_volatility(\n    mu=0.0,\n    vol=0.01,\n    t=0.01,\n)\nPROPERTY monotonic violated | inputs=(0.0, 0.01, 0.01) | ...",
  "source": "hypothesis"
}
```

That is an artifact, not an opinion. It reproduces: `derandomize=True` means the same audit
returns the same falsifying input every run.

## What a clean run does NOT mean

If your property holds, the verdict is `UNCERTAIN` — never `FALSE_POSITIVE`. Properties holding
across a bounded search is the absence of a counterexample, not proof of correctness. Pick
`assert_limit` at `vol=0` for this same function and it passes, because the flipped term vanishes
there. The defect is still real; that property just cannot see it.

## After the fix

Keep the property file. Move it into the target's test suite — the property that caught the bug is
the regression test proving the fix satisfies it.
```

- [ ] **Step 4: Verify the example actually produces the documented output**

Run:
```bash
cd /c/Users/gde00/Documents/cca-audit
python -m cca_checks numeric --finding-id NUM-001 --test examples/sign-trap/t_NUM-001_props.py
```
Expected: JSON with `"verdict": "CONFIRMED"`, `"source": "hypothesis"`, and a `Falsifying example` in `evidence`.

If the falsifying values differ from what the README shows, **update the README to match the real
output**. A worked example whose transcript does not reproduce is the exact failure this feature
exists to prevent.

- [ ] **Step 5: Add the claim type to the main README**

In `README.md`, replace line 113 (the paragraph beginning "**For the deterministic verification
layer**") with:

```markdown
**For the deterministic verification layer**, also have `pyright`, `pytest`, and `semgrep` on your `PATH` (`pip install pyright pytest semgrep`). Without `cca_checks` or those tools, `/audit-fix` gracefully **falls back to LLM-only verification** — no crash, no regression. See the [Claude Code README](claude-code/README.md) for local-clone install and details.

**For numeric findings**, install the `numeric` extra (`pip install "cca_checks[numeric]"`). It adds
the `numeric` claim type, which settles arithmetic defects — wrong sign, mixed units, bad scaling —
by running declared metamorphic properties under Hypothesis. It confirms with a falsifying example
and never refutes, because properties holding is not proof of correctness. Worked example:
[`examples/sign-trap`](examples/sign-trap/).
```

- [ ] **Step 6: Run the full suite and commit**

```bash
cd /c/Users/gde00/Documents/cca-audit
python -m pytest -q
```
Expected: PASS, no failures

```bash
git add examples/sign-trap README.md
git commit -m "docs(examples): worked sign trap settled by a property artifact"
```

---

## Final verification

- [ ] `python -m pytest -q` — full suite green
- [ ] `python -m pytest -q -p no:randomly` twice — same result, no flakiness
- [ ] `diff -r claude-code/agents .claude/agents` — no drift
- [ ] `pip install -e .` in a clean venv (no extra) then `python -m cca_checks numeric --finding-id X --test examples/sign-trap/t_NUM-001_props.py` — must return `UNCERTAIN` with "hypothesis not installed", NOT a crash and NOT a pass
- [ ] Open PR against `master` with the spec and this plan referenced
