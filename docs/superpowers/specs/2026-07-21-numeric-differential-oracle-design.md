# Numeric differential oracle — design

**Date:** 2026-07-21
**Status:** approved, ready for implementation planning
**Scope:** one PR to `cca_checks` + two agent files + `audit-fix.md` tier rule + one worked example

## Problem

`cca_checks` can mechanically settle four claim types: `definedness` / `nullability` / `type`
(pyright), `taint` (semgrep), and `crash_impact` (pytest repro). Findings from the
numeric-auditor (`NUM-*`) have **no Phase-1 mechanical settler**. They fall through to Phase-2
semantic adjudication, where an LLM re-reads the arithmetic and renders a judgment.

That path is unsound for the bug class the numeric-auditor exists to catch. A sign error reads
fluently: the expression is well-formed, the variable names are right, and only the *meaning* is
inverted. An LLM re-reading such an expression will frequently declare it correct — and there is
currently no tool in the box that can refute it.

Motivating incident: a risk-neutral drift term written `+0.5·vol²` where the correct form is
`-0.5·vol²`. A review read that exact line and hand-verified it as correct. It was caught only by
comparing against a second, independently-derived implementation of the same formula.

## Goal

Give `NUM-*` findings a mechanical settler that produces a **counterexample artifact**, so a
numeric P1 can be confirmed by execution rather than by re-reading.

Non-goals: proving numeric code correct; supporting non-Python targets; replacing the
numeric-auditor's detection role. This settles claims; it does not find them.

## Approach

**Properties and metamorphic relations, not a twin implementation.**

A twin implementation authored by the same model that made the finding risks correlated errors,
and is expensive to write. Properties instead state the *intended relation* — which cannot be
derived from the implementation under test — and let a fuzzer search for inputs that break it. A
violated property yields a concrete falsifying input: real evidence. Properties merely holding
proves nothing, and the design says so explicitly rather than papering over it.

A sign error is caught by exactly this: `assert_limit` on a degenerate case (vol→0), or
`assert_monotonic_in` on the term whose sign flipped.

## Components

Four new files, two modified, mirroring the existing taint/repro shape.

### `cca_checks/properties.py` (new)

The assertion vocabulary. Pure functions, no I/O, no subprocess. Each raises `PropertyViolation`
carrying the property name, the falsifying input, and the observed vs required values.

| Helper | Catches |
|---|---|
| `assert_bounded(fn, lo, hi)` | probabilities outside [0,1], negative sizes, ratios escaping their base |
| `assert_monotonic_in(fn, arg, direction)` | a term entering with the wrong sign |
| `assert_limit(fn, arg, approaching, expected)` | degenerate-case errors — vol→0, t→0, n→1 |
| `assert_scale_invariant(fn, args, factor)` | unit mixing, missing or extra scale factors |
| `assert_sign_symmetric(fn, arg)` | swapped subtraction operands, inverted comparisons |
| `assert_round_trips(fwd, inv)` | conversions that do not invert |

Every helper takes the **intended** relation as an explicit argument. This is what makes a
tautological property (one that merely restates the implementation) impossible to write through
this vocabulary.

### `cca_checks/hypo.py` (new)

The determinism contract, isolated so `properties.py` stays import-free of Hypothesis. Exports
`cca_settings = settings(derandomize=True, max_examples=MAX_EXAMPLES, deadline=None)`, which every
generated property file applies. Determinism therefore lives in our code, not in a setting the
auditor must remember to write. Importing this module without `hypothesis` installed raises
`ModuleNotFoundError`, which the runner detects and maps to UNCERTAIN.

### `cca_checks/property_check.py` (new)

`run_properties(finding_id: str, test_path: str) -> Verdict`.

Subprocess-executes pytest over the auditor's property file, parses Hypothesis's
`Falsifying example:` block from the output, returns it as `evidence`. Carries the same
target-code-execution warning and the same 120s timeout as `repro_runner.py`.

Hypothesis runs with `derandomize=True` and `max_examples=200`: re-running an audit must
reproduce the same counterexample. An audit that reports a different failing input each run is
not an artifact.

### `cca_checks/__main__.py` (modified)

New `numeric` subcommand, sibling to `repro`: `--finding-id`, `--test`.

Deliberately **not** a `--claim-type` under `check`: `check` settles a static claim at a
`file:line` with an analyzer, while this executes a test file. Deliberately **not** a flag on
`repro`: `repro` confirms *a crash matching a predicted error*, while this confirms *a wrong
value with no exception at all*. Sharing the subcommand would require bypassing the
`--expect-error` logic and would blur the verdict asymmetry.

### `cca_checks/claim.py` (unchanged)

`Verdict.source` gains the value `"hypothesis"`, but the dataclass and `make_verdict` need no
change. The artifact-or-UNCERTAIN rule already applies as written.

### Agent files (modified)

- `claude-code/agents/cca-numeric-auditor.md` — every `NUM-*` finding gains a required
  `properties:` block naming which helpers encode its claim and over which input domains.
- `claude-code/agents/cca-fp-check.md` — `numeric` added to the Phase-1 claim-type list, bound by
  the same "you may not overturn a tool artifact" rule as pyright/semgrep/pytest.

Both must be updated in `.claude/agents/` as well as `claude-code/agents/`; the repo ships two
copies and they must not drift.

## Verdict semantics

**The asymmetry is the mirror image of taint.** Semgrep can never return `CONFIRMED` for a taint
claim; the numeric checker can never return `FALSE_POSITIVE` for a numeric one. Properties holding
across a bounded search is not proof of correctness — it is only the absence of a counterexample.

| Outcome | Verdict | `source` | `evidence` |
|---|---|---|---|
| Property violated | `CONFIRMED` | `hypothesis` | falsifying example + observed vs required |
| No counterexample found | `UNCERTAIN` | `hypothesis` | "no counterexample in 200 examples; escalated" |
| `hypothesis` not installed | `UNCERTAIN` | `hypothesis` | "property check unavailable; escalated" |
| pytest rc ∉ {0,1} | `UNCERTAIN` | `hypothesis` | collection/usage error + output tail |
| Timeout | `UNCERTAIN` | `hypothesis` | "timed out after 120s; escalated" |

This matches `repro_runner.py:31-34`, where a *passing* repro yields UNCERTAIN rather than
FALSE_POSITIVE. Same principle, so the rule an agent must remember stays one rule, not two.

`hypothesis` is an optional dependency, declared in `pyproject.toml` as a
`[project.optional-dependencies]` extra named `numeric` (`pip install cca_checks[numeric]`). The
core install stays dependency-free. Absent ⇒ UNCERTAIN, never a silent pass.

## Data flow

1. numeric-auditor emits `NUM-007` with a `properties:` block.
2. fp-check writes `t_NUM-007_props.py` from that block.
3. `python -m cca_checks numeric --finding-id NUM-007 --test t_NUM-007_props.py`
4. JSON verdict (`verdict`, `evidence`, `source`) consumed verbatim.

**Divergence from the repro flow:** fp-check deletes the temp repro test after use
(`cca-fp-check.md:86`). Numeric property files are **kept** and moved into the target's test suite
as part of the fix. The property that caught the bug becomes the regression test proving the fix
satisfies it — closing the L5.5 anti-regression loop with the same artifact that confirmed the
finding.

## Tier integration

In `claude-code/commands/audit-fix.md`:

- The L2.5 row (`audit-fix.md:159`) gains, for DEEP: numeric P1s route through the `numeric`
  claim type.
- New binding rule: **in DEEP tier, a `NUM-*` P1 may not enter the fix plan on an `llm`-sourced
  verdict.** It carries a `hypothesis` artifact or it is escalated as UNCERTAIN.
- FAST and STANDARD are unchanged. The claim type is available; nothing blocks.

## Testing

Mirrors the existing per-module layout.

- `tests/test_properties.py` — each of the six helpers, violating and non-violating cases.
- `tests/test_property_check.py` — the full verdict mapping. Subprocess mocked, following
  `test_repro_runner.py`.
- `tests/test_cli.py` — extended for the `numeric` subcommand, including an unparseable-output case.
- `tests/fixtures/numeric/` — a sign-flipped function and its correct twin.
- `tests/acceptance/test_numeric_suite.py` — end to end: the sign-trap fixture returns CONFIRMED
  with a falsifying example; the corrected version does not.
- Blindness probe — an honest case where the defect is real and still present but the chosen
  property cannot see it (`assert_limit` at vol=0, where the flipped term vanishes). Must return
  UNCERTAIN, never a refutation. This lives in `tests/acceptance/test_numeric_suite.py` rather than
  in `tests/test_blindness_probe.py`: the latter is specific to `pyright_is_blind_at` and mixing an
  execution-based probe into it would blur that module's subject.

## Failure modes

| Mode | Handling |
|---|---|
| Property restates the implementation (tautology) | Structurally prevented: every helper takes the intended relation as an argument |
| Non-Python target | No settler; falls to Phase 2 exactly as today |
| Function needs heavy construction to call | Property file cannot call it ⇒ UNCERTAIN, escalate |
| Counterexample in a domain production never sees | Property file must declare input domains explicitly; unbounded-float defaults are disallowed |
| `hypothesis` missing in the target env | UNCERTAIN with a distinct evidence string; never a pass |

## Deliverable

One PR to `GiulioDER/cca-audit` containing the above, plus an `examples/` entry in the style of
the existing `bps-sizing/definedness_trap`: a worked sign trap showing the finding, the property
that catches it, and the resulting artifact.
