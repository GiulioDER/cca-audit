# Substrate-differential check — design

**Date:** 2026-07-21
**Status:** approved, ready for implementation planning
**Slice:** v3.5
**Depends on:** the DEEP self-audit hardening, merged to `master` as `f91b1f4` (PRs #18 → #19 → #20,
a stacked set; 260 tests green on py3.10–3.13). Implement against `master` at or after that commit —
several integration details assume conventions that did not exist in v3.4.

## Baseline — read this first

This spec is written against the **hardened** `cca_checks` (`f91b1f4`), not the v3.4 release. That
pass established four conventions this design adopts rather than duplicating:

1. **`ValueError` means "this check could not meaningfully run."** A `ValueError` is not a
   `PropertyViolation`, so it emits no `PROPERTY … violated` line, so `property_check` maps it to
   UNCERTAIN. Used for harness overflow (`_require_harness_finite`) and for degenerate declared
   relations (`lo > hi`, `factor == 1`, empty `indices`).
2. **Vacuity guards belong in the helper.** A declared relation that no input can violate — or that
   every input violates — is rejected at the top of the helper, before any input is tested.
3. **Ambiguity escalates rather than guessing.** More than one *distinct* Hypothesis banner in a
   run (`_distinct_falsifying()` in `property_check.py`) means the violation cannot be bound to a
   specific counterexample, so the verdict is UNCERTAIN.
4. **Tunables live in `cca_checks/config.py`, not in the module that uses them**, and are
   environment-overridable under a `CCA_` prefix, with a malformed value falling back to the
   default rather than crashing the checker. `TIMEOUT_S` and `MAX_EXAMPLES` moved there;
   `properties.py` re-exports `MAX_EXAMPLES` for back-compat.

Consequence: **`property_check.py` needs no changes for this slice.** An earlier draft of this
design added a pattern match there to surface substrate reasons; convention 1 already delivers
that, with the reason visible in the escalated output tail.

## Problem

The v3.4 `numeric` claim type settles arithmetic findings by executing auditor-declared metamorphic
properties. It works, and it has a structural limit the spec named honestly: **the property is
authored by the same LLM auditor that raised the finding**, so property and finding stay
correlated. A wrong declared relation yields a real counterexample to a wrong claim.

Raised by the author of the article that prompted the v3.4 work:

> The strongest decorrelation I got wasn't two models — it was two runtimes (a JVM build vs a
> browser/TeaVM build); the substrate disagreed where the author couldn't.

We have no substrate disagreement anywhere. Hypothesis's generator is the only uncorrelated
element, and it explores only inside a domain the auditor declares.

There is also a defect class the property vocabulary covers weakly. Consider two algebraically
identical formulations of `(1 − cos x)/x²`:

| x | `(1-cos(x))/x²` | `2·sin²(x/2)/x²` | relative difference |
|---|---|---|---|
| 1e-3 | 0.4999999583255033 | 0.4999999583333347 | 1.6e-11 |
| 1e-7 | 0.4996003610813205 | 0.4999999999999996 | 8.0e-04 |
| 1e-8 | **0** | 0.5 | **1.0** |

At `x = 1e-8` the first returns zero instead of a half. No property anyone would think to write
catches that. A reference substrate catches it in one call.

## Viability finding — why the obvious implementation is a trap

A naive substrate swap **does not work in Python**, and fails in the worst possible direction.

| Attempt | Result |
|---|---|
| `Fraction` inputs | A float literal in the source (`0.5 * vol**2`) collapses the result back to `float` — **silently** |
| Any `math.*` call | `math.cos(Fraction)` returns `float`. Substrate lost — **silently** |
| `Decimal` inputs | `TypeError` on mixed float literals — loud, but cannot run most real code |

Measured directly: a pure-arithmetic function called with `Fraction` arguments returned a `float`,
and a transcendental function called with `Fraction` arguments returned a value bit-identical to
the plain `float` run. A naive implementation therefore reports "no divergence" on essentially all
real numeric code, because both runs are secretly float64. **A check that always passes.**

An attempted substrate-free alternative — measuring 1-ULP perturbation amplification — was also
tested and rejected: it scored 1.68 for the unstable function against 2.05 for a benign one. At
that scale the error is a bias, not a sensitivity; the result is already on a quantized plateau, so
nudging the input barely moves it.

**Both dead ends are recorded here so they are not re-attempted.**

## Approach

`mpmath` as the reference substrate, because `mpmath.cos` returns an `mpf` and therefore survives
where `math.cos` collapses. Two requirements follow, and the second is the spine of the design:

1. The patch must reach the target's arithmetic, including targets that did `from math import log`
   and hold their own binding.
2. **The result type must be integrity-gated.** If the returned value is not an `mpf`, the
   substrate was lost and the verdict is UNCERTAIN — never "they agree." This is the project's own
   "a check that couldn't run never passes" rule applied to itself, and it is what separates this
   design from the trap above.

## Components

### `cca_checks/substrate.py` (new — the only new module)

```python
@dataclass(frozen=True)
class SubstrateResult:
    value: object | None          # mpf, or None
    reason: str | None            # None | "substrate_lost" | "not_patchable" | "raised" | "unavailable"
```

- `mpmath_bindings(fn)` — context manager. Introspects `fn.__module__` and swaps any global bound
  to a `math` function for its mpmath equivalent, restoring on exit **including on exception**.
- `run_under_substrate(fn, args, dps=SUBSTRATE_DPS) -> SubstrateResult` — converts args to `mpf`,
  runs under the patch, then integrity-gates the returned type. A non-`mpf` return yields
  `reason="substrate_lost"` and **never** a value.
- `assert_substrate_agrees(fn, args)` — the seventh helper. Raises `PropertyViolation` when
  relative divergence exceeds `SUBSTRATE_TOL`; raises `ValueError` carrying the reason when the
  substrate could not be applied.

Splitting the runner from the assertion is deliberate: the patching machinery is the risky half and
gets tested directly, without going through Hypothesis.

### `cca_checks/config.py` (modified)

Per convention 4, both tunables live here, not in `substrate.py`:

```python
SUBSTRATE_TOL = _positive_float("CCA_SUBSTRATE_TOL", 1e-9)   # a well-conditioned float64 result
                     # lands within ~1e-15 of exact; 1e-9 is "worse than float32" = real precision loss
SUBSTRATE_DPS = _positive_int("CCA_SUBSTRATE_DPS", 50)       # reference precision
```

`config.py` currently has only `_positive_int`; add a `_positive_float` following the same
malformed-value-falls-back-to-default discipline.

**An environment override does not reintroduce author-correlation.** The decorrelation this slice
depends on is that the *auditor raising the finding* cannot tune the threshold per finding. An
operator setting `CCA_SUBSTRATE_TOL` once for a whole repo is a different actor making a different
kind of decision, and it stays out of the per-finding loop. The agent contract must therefore
continue to forbid a tolerance key in the `properties:` block.

### `cca_checks/properties.py` (modified)

Re-export `assert_substrate_agrees` and `SUBSTRATE_TOL` so the vocabulary remains one import. **No
logic moves in** — `properties.py` keeps its pure-functions, no-I/O, no-subprocess contract, and
must stay importable without `mpmath`.

### `cca_checks/property_check.py` (NOT modified)

See *Baseline*. Convention 1 handles substrate failures already.

### `pyproject.toml` (modified)

`mpmath>=1.3` added to **both** the `numeric` extra (which now reads
`["hypothesis>=6.0", "pytest>=7"]`) and the `verify` extra, since `verify` is documented as "the
whole deterministic layer in one install". Adding it to `numeric` alone would leave `verify`
half-enabling the feature — the exact failure the hardening pass called out when it added `pytest`
to `numeric`: an extra that half-enables a feature is worse than one that does not exist, because
the failure is silent. The core install stays dependency-free.

### Agent files (modified)

- `cca-numeric-auditor.md` — `assert_substrate_agrees` added to the helper key reference:
  `target`, `args`, `domains`, and **explicitly no tolerance key**, since the threshold is fixed by
  design. An authored tolerance would reintroduce the correlation this whole slice exists to escape.
- `cca-fp-check.md` — a third template.

Both mirrored to `.claude/` on disk; **neither committed there** — `.claude/` is untracked.

## Verdict semantics

No new verdict machinery. `assert_substrate_agrees` raises `PropertyViolation`, emitting the same
`PROPERTY substrate_agrees violated | …` line the other six helpers do, so the existing CONFIRMED
gate (Hypothesis banner **and** our own violation line) covers it unchanged.

| Situation | Verdict | Evidence |
|---|---|---|
| Relative divergence > `1e-9` | `CONFIRMED` | falsifying input, float value, mpf value, measured relative error |
| Within tolerance across 200 examples | `UNCERTAIN` | "no counterexample" — unchanged |
| Result returned as `float`, not `mpf` | `UNCERTAIN` | `substrate_lost` in the escalated tail |
| Target module not introspectable | `UNCERTAIN` | `not_patchable` |
| Target raised under the substrate but not under float | `UNCERTAIN` | `raised` — an mpmath-specific failure is not evidence about the code |
| `mpmath` not installed | `UNCERTAIN` | `unavailable` |

**A lost substrate must never read as agreement.** Because `ValueError` is not a
`PropertyViolation`, it emits no `PROPERTY` line and cannot reach CONFIRMED through the existing
gate.

### Numeric edge cases

Follow `properties.py`'s established discipline. A non-finite float result where the mpf result is
finite (or the reverse) is a violation, not a tolerance question. A zero reference falls back to
absolute comparison. Vacuity guard, per convention 2: reject a non-callable target, and reject
`dps < 30`. float64 carries ~15–17 significant decimal digits, so a reference below that is less
precise than the thing it references and proves nothing; 30 leaves a clear margin above the
boundary rather than sitting on it. This guard is not theoretical now that `SUBSTRATE_DPS` is
environment-overridable — `CCA_SUBSTRATE_DPS=5` would otherwise silently turn every comparison into
noise, and per convention 4 a bad env value must degrade safely rather than be honoured.

## Data flow

1. numeric-auditor emits a finding with `helper: assert_substrate_agrees` and input domains.
2. fp-check writes the property file.
3. The **first call doubles as the capability pre-flight** — an unusable target fails in one call,
   not 200.
4. Hypothesis searches for maximum divergence.
5. Verdict consumed verbatim.

**Determinism:** `dps = 50` fixed, combined with the existing `derandomize=True`, so the same audit
returns the same divergent input and the same measured error every run.

## Testing

- `tests/test_substrate.py` — `run_under_substrate` in isolation: a math-free target, an
  `import math` target, a `from math import log` target (the binding gotcha), a non-introspectable
  target, and mpmath absent. Each asserts the exact `reason`, not merely that it failed.
- `tests/fixtures/substrate/` — the `(1−cos x)/x²` pair above.
- `tests/acceptance/test_substrate_suite.py`:
  - unstable → `CONFIRMED` with the divergent input
  - stable → not confirmed (proves it discriminates rather than flagging all float code)
  - integrity gate fires → a substrate-losing target returns `substrate_lost`, never agreement
  - restoration → `math.log` is the original builtin after the call, **including after an exception**

### The blindness probe for this layer

Point the substrate check at the v3.4 GBM sign trap and assert it does **not** confirm. Both
substrates compute the same wrong formula, so they agree perfectly — the defect is real, present,
and structurally invisible here.

This is not a caveat in prose; it is a test. It converts the division-of-labor claim in
`docs/blog-fluency-isnt-evidence.md` from an assertion into something the suite enforces.

## Failure modes

| Mode | Handling |
|---|---|
| Patch misses a binding | Integrity gate → `substrate_lost`, never agreement |
| Target is not pure | Runs twice ⇒ side effects fire twice. **Out of scope, documented** — the auditor must not point this at impure code |
| mpmath at dps=50 × 200 examples is slow | Existing 120s timeout → UNCERTAIN, never a pass |
| Patching module globals is not thread-safe | Single-threaded under `pytest -x`; documented as a constraint, not defended against |
| Sign or formula errors | **Structurally invisible.** Properties cover this class; this layer never will |

## What this slice deliberately excludes

Dimensional invariants for unit mixing — the other half of the article author's suggestion — are a
separate mechanism sharing no machinery with this one. They get their own spec.

## Division of labor (why both layers exist)

| Defect class | Properties (v3.4) | Substrate (v3.5) |
|---|---|---|
| Sign / direction inverted | ✅ | ❌ both substrates compute the same wrong formula |
| Wrong formula, right units | ✅ | ❌ |
| Catastrophic cancellation, accumulation | ❌ needs you to suspect it | ✅ falls out with no authored relation |
| Precision loss, wrong rounding direction | weak — you must know to look | ✅ |

Neither catches the other's class. The substrate check is genuinely independent because **nobody
authors the disagreement** — which is the property v3.4 was missing.
