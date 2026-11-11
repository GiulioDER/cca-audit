# Honest limits

Where CCA-Audit is blind. Most of these limits are asserted by tests.

- **The numeric substrate check cannot see sign or formula errors.** Its two substrates differ in how
  they evaluate and agree in what they evaluate: both compute whatever structure was written down,
  so a flipped sign survives into both and they agree. That class belongs to the property helpers.
  `test_sign_trap_does_not_violate` asserts this.
- **Its provenance gate observes Python conversion hooks, not every native intermediate.** An `mpf`
  converted through `__float__`, `__int__` or `__complex__` in a helper module returns `UNCERTAIN`,
  even when outer arithmetic promotes the float back to `mpf`. Native code that extracts a value
  without those hooks, and work delegated to another thread, remain unsupported.
- **Its replay screens for state but does not prove purity.** The float64 result is evaluated before
  and after the reference run, and a mismatch returns `UNCERTAIN`. That closes the false confirmation
  demonstrated with a counter-based target, but a finite replay cannot certify an arbitrary function
  as free of side effects.
- **The agent that raised a finding also writes its property.** A wrongly declared relation therefore
  produces a real counterexample to a wrong claim. A confirmation obliges you to re-read the relation
  as well as the verdict.
- **Deterministic coverage is per language, and a language without a backend gets none.**
  `python -m cca_checks capabilities --file <F>` reports what the tool can settle on your machine,
  including which tools are missing, so ask it rather than trusting this table.

  | | settled deterministically | by |
  |---|---|---|
  | **Python** | `definedness` · `nullability` · `type` · `taint` · `clock_leak` | `ast` + `pyright` + `semgrep` |
  | **Rust** | `clock_leak` · `panic_path` · `overflow` · `error_swallow` · `unsafe_op` · `taint` | tree-sitter + `clippy` + `semgrep` |
  | **TypeScript** | `definedness` · `type` · `nullability` | tree-sitter + `tsc` (pinned 6.0.3) |
  | **everything else** | nothing; every claim escalates to LLM adjudication | no tool |

  A claim about a file no backend covers, or a claim type the covering backend does not declare,
  returns `UNCERTAIN` before any checker runs, so a tool built for another language cannot refute it.
  `test_language_guard.py` asserts this for every claim type.

- **Rust has no `definedness`, `type` or `nullability` claims, by design.** Those defects do not
  survive to review in code that compiled: the name resolved, the types checked, and `Option` is not
  a nullable pointer. Porting them would have doubled the claim vocabulary while refuting almost
  everything by construction.
- **TypeScript settles only what the compiler could see.** TypeScript 6.0.3 is the one supported
  major; any other gives `UNCERTAIN`. A `FALSE_POSITIVE` is withheld when the span has a
  suppression directive (`@ts-ignore`, `@ts-expect-error`, `@ts-nocheck`), `any`, or a type
  assertion other than `as const`, and when the compiler reports an implicit-any, a missing module
  or type package, or a diagnostic it cannot place in a file.
- **TypeScript `.d.ts` claims never refute.** `skipLibCheck` leaves declaration files unchecked, so
  the compiler's silence about one proves nothing.
- **TypeScript nullability claims never refute on index access.** Any element access or
  index-signature property read in the span blocks refutation, because `noUncheckedIndexedAccess`
  is not forced and the compiler is silent about exactly those reads.
- **TypeScript `definedness` is not confirmed when the environment is incomplete.** A TS2688 (a
  type package that cannot be found), or a `package.json` that declares an `@types` package that is
  not installed, withholds `CONFIRMED`. With no tsconfig, or one that leaves `types` unset, the
  probe includes every `@types` package it can find, because TypeScript 6 stopped doing that.
- **A TypeScript repository without installed dependencies can confirm but rarely refute.**
  Unresolved modules are environment diagnostics, and those block refutation.
- **The Rust clock check reads syntax, not semantics.** tree-sitter does not expand macros, so it
  cannot see a clock read inside a `macro_rules!` body or one reaching the file through a glob `use`.
  Both block refutation instead of being ignored: a file must not earn a `FALSE_POSITIVE` with an
  authoritative `source` just by being hard to read. `test_clock_check_rust.py` asserts both.
- **clippy needs the crate to build.** A crate that does not compile produces no lint diagnostics,
  which looks exactly like a clean crate. An unbuildable crate is therefore `UNCERTAIN` everywhere and
  never refuted. `test_clippy_check.py::test_a_stream_that_proves_nothing_returns_none` asserts the
  whole cascade.
- **A debug test cannot prove release-mode wrapping overflow.** Rust panics on overflow in debug
  builds and wraps silently in release builds, and a `cargo test` artifact can only demonstrate the
  first. The second is the more dangerous half, and it is disclosed rather than claimed.
- **clippy can never confirm a `panic_path` or `unsafe_op` finding.** A lint sees the construct, not
  whether a value the caller controls can reach it: `.unwrap()` on a value built two lines above is
  correct code. Like `taint`, these two refute a false premise and inform adjudication, and a
  confirmation has to come from a repro that actually panics.
- **The commit guard does not inspect command substitution or `$'...'` quoting.** A command such as
  `git commit -am "$(<edit>)msg"` runs the edit before git and commits it unjudged. Both gaps were
  parked after three repair cycles each, and the
  [changelog](../CHANGELOG.md) records where a fix
  should start.
