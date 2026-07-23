# Changelog

All notable changes to `cca_checks` — the deterministic verification layer behind CCA-Audit's
`fp-check` gate — are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the `cca_checks` package
version from `pyproject.toml`, not the surrounding agent-prompt tooling (which is unversioned).
Dates and content are sourced from `git log` and `docs/v3-design.md` §7 — nothing here is invented.

## [Unreleased]

- **v3.6 — `clock_leak` claims** (2026-07-22, PR #29). A new claim type for "code that should run
  on injected time reads the wall clock anyway" — the defect that passes review because both
  halves look correct in isolation, and only misbehaves once simulated and real time diverge.
  Settled from the syntax tree by `cca_checks/clock_check.py`, through import aliases, with no
  external tool and no new dependency. `CONFIRMED` needs a *dead parameter*: a STRONG
  injected-time parameter (`CCA_CLOCK_STRONG_PARAMS`) never referenced in the scope **and** a
  real wall-clock read — mere co-occurrence of a clock parameter and a `datetime.now()` is
  `UNCERTAIN`, since stamping an audit log with real time while the logic runs on `as_of` is
  correct code. `FALSE_POSITIVE` only on an absence the file cannot have hidden, so a
  `from x import *` or a clock function named but never called blocks refutation.

## [0.5.0] - 2026-07-22

- **v3.5 — substrate-differential checks** (2026-07-22, PR #21). Adds `assert_substrate_agrees`,
  a seventh property helper that runs a numeric target twice — once at float64, once against a
  50-digit `mpmath` reference via `substrate.py` — and confirms only when the two disagree
  beyond a tolerance. Unlike the `v3.4` property helpers, no relation is authored by the finding's
  own agent, so it is decorrelated on *evaluation* (catches cancellation/accumulation/rounding
  defects the property vocabulary can't) but not on *transcription* (a flipped sign or wrong term
  survives into both substrates identically — that class stays with the property helpers).
  Requires the `numeric` or `verify` extra (`mpmath>=1.3`); absent → `UNCERTAIN`, never a silent
  pass. See `docs/superpowers/specs/2026-07-21-substrate-differential-design.md`.
- **Self-audit of v3.5** (2026-07-22, PR #22 — `fix/cca-self-audit-substrate`). CCA's own DEEP
  pipeline run against the substrate feature found two P1s, both reproduced by execution:
  - The `dev` extra — the only extra CI's install step uses — was missing `mpmath`, so both
    `tests/test_substrate.py` and `tests/acceptance/test_substrate_suite.py` silently skipped
    via `pytest.importorskip("mpmath")` on every CI run, on every Python version: ~25 tests,
    including the integrity gate that is this feature's central safety guarantee. Fixed by
    adding `mpmath>=1.3` to `dev`; a follow-up test now asserts the invariant (every
    module-scope `importorskip` name under `tests/` must appear in `dev`) so the class can't
    silently recur with a future optional dependency.
  - `SUBSTRATE_TOL` had no floor or ceiling, unlike `SUBSTRATE_DPS`. An override as small as
    `1e-20` turned ordinary float64 noise (~8.3e-18) into a `CONFIRMED` violation against
    *correct* code; an override large enough made the check permanently vacuous. Bounded to
    `[1e-15, 1.0]` in `config.py`.
  - Also fixed: asymmetric non-finite handling in `assert_substrate_agrees` (only `observed`
    was checked, so a non-finite `reference` against a finite `observed` produced `NaN >
    SUBSTRATE_TOL == False` — silently passing a genuine unbounded divergence), and a P2 where
    `properties.py` re-exported `assert_substrate_agrees` but not `SUBSTRATE_TOL`, contradicting
    the design spec.

## [0.4.0] - 2026-07-21

- **v3.4 — `numeric` claims via metamorphic properties** (PR #16). Settles arithmetic defects —
  wrong sign, mixed units, bad scaling — by running auditor-declared properties
  (`assert_bounded`, `assert_monotonic_in`, `assert_limit`, `assert_scale_invariant`,
  `assert_sign_symmetric`, `assert_round_trips`) under Hypothesis. A violated property yields a
  concrete falsifying example (`CONFIRMED`); properties holding across a bounded search is never
  treated as proof of correctness, so this checker can never emit `FALSE_POSITIVE` — the mirror
  image of the taint checker's asymmetry. `hypothesis` is an optional `[numeric]` extra;
  without it, every numeric claim escalates to `UNCERTAIN` rather than silently passing. On the
  DEEP tier, a `NUM-*` P1 cannot enter the fix plan without a Hypothesis artifact — a hard stop,
  not a graceful degradation, because a sign error reads fluently and a second LLM opinion isn't
  evidence. See `docs/superpowers/blog-fluency-isnt-evidence.md` and
  `docs/superpowers/specs/2026-07-21-numeric-differential-oracle-design.md`.
- **Self-audit of the deterministic layer (DEEP tier), 21 fixes** (PRs #18 → #19 → #20, all
  2026-07-22). CCA-Audit run against its own verification layer in hunt mode. Highlights:
  - `SEC-001`: `pyright`/`semgrep` were launched by bare name while `cwd` is the audited repo;
    on Windows, `CreateProcess` resolves `argv[0]` against the current directory *before* `PATH`,
    so a repo shipping a same-named binary in its root got code execution on the auditor's
    machine. Fixed with an explicit `PATH`-only resolver (`cca_checks/toolpath.py`) that refuses
    a binary planted in the audit root.
  - `SEC-003`/`STAKES-002`: `pyright` inherited the audited repo's own config, so
    `typeCheckingMode: off` or a stray `# type: ignore` silenced findings and the silence read
    as `FALSE_POSITIVE`. Both passes now run under a generated config that ignores the target's
    own type-ignore comments.
  - `SEC-004`: `semgrep` honored the audited repo's `# nosemgrep` and `.gitignore`; added
    `--disable-nosem`/`--no-git-ignore`.
  - Several `NUM-*`/`BUG-*` fixes where the harness's own perturbation, not the target code,
    produced the "violation" (e.g. `assert_round_trips` falsifying a correct quantizing
    converter; a scale-invariance probe pushing outside float64 range and blaming the target).
  - `ENV-018`/`DOC-003`: neither installer passed the `[numeric]` extra by default, so a fresh
    install silently ran with every numeric claim escalating to `UNCERTAIN`. Fixed at install
    time, plus a same-PR fix (`cbf1f18`) resolving contradictions between `audit-fix.md`'s
    high-stakes escalation rule and `cca-fp-check.md`'s "any tool artifact cannot be overturned"
    rule.
  - `NUM-003`: two independent regexes could pair one violation's shrunk input with a *different*
    violation's property-name line; ambiguous output now escalates instead of guessing.
  - CI updated (`cb934e4`) to run the suite across Python 3.10–3.13 plus a job that builds the
    wheel and installs it into a clean venv, so a module missing from the packaged wheel is
    caught in CI rather than surfacing only for a user who installs it.

## [0.3.0] - 2026-07-10

- **v3.2 — `taint` claims via semgrep.** A bundled two-tier sink catalog backs the `taint` claim
  type. Semgrep can refute a false premise (flags a genuinely parameterized query as safe) and
  inform adjudication, but never confirms on its own — a hit is evidence, not proof. See
  `docs/superpowers/specs/2026-07-10-v3.2-taint-semgrep-design.md`.

## [0.2.0] - 2026-07-10

- **v3.1 — `type` and `nullability` claims via `pyright`,** routed through the same deterministic
  mechanism as v3.0-min's `definedness` claim, plus a blindness probe so a refutation is only
  issued when `pyright` actually had type information in the claim's enclosing scope — a silent
  gap here would otherwise read as "no type error" rather than "couldn't check." See
  `docs/superpowers/specs/2026-07-10-v3.1-type-nullability-design.md`.

## [0.1.0] - 2026-07-09

- **v3.0-min — the deterministic verification layer, initial slice.** Claim/Verdict schema
  (`cca_checks.claim`, artifact-or-`UNCERTAIN` rule), the `fp-check` router, a `pyright` adapter
  for `definedness` claims, and a `pytest`-based repro runner (confirm-on-fail, escalate-on-pass).
  Falls back to LLM-only verification when `pyright`/`pytest` are absent — no crash, no silent
  regression. See `docs/v3-design.md` and
  `docs/superpowers/plans/2026-07-09-v3-deterministic-verification.md`.

## Unclaimed

- **v3.3 — other languages** (TypeScript via `tsc`, Go, Rust) is reserved in the roadmap
  (`docs/v3-design.md` §7) but not implemented. Also the design's stated point at which the
  mechanical checks should move behind their own layer.
