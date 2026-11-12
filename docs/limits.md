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
  | **TypeScript** | `definedness` · `type` · `nullability` | tree-sitter + `tsc` (major 6) |
  | **everything else** | nothing; every claim escalates to LLM adjudication | no tool |

  A claim about a file no backend covers, or a claim type the covering backend does not declare,
  returns `UNCERTAIN` before any checker runs, so a tool built for another language cannot refute it.
  `test_language_guard.py` asserts this for every claim type.

- **Rust has no `definedness`, `type` or `nullability` claims, by design.** Those defects do not
  survive to review in code that compiled: the name resolved, the types checked, and `Option` is not
  a nullable pointer. Porting them would have doubled the claim vocabulary while refuting almost
  everything by construction.
- **TypeScript settles only what the compiler could see.** TypeScript major 6 is the one supported
  major (install 6.0.3, the version CI pins); any other gives `UNCERTAIN`. A `FALSE_POSITIVE` is
  withheld, for every claim type, when a `@ts-ignore` or `@ts-expect-error` directive applies to a
  line in the span or the file has a `@ts-nocheck`, and when the claimed file has an environment
  diagnostic (a missing module or type package) or the compiler reports a diagnostic with no file,
  or with no line in the claimed file. For `type` and `nullability` claims it is also withheld when
  the span has `any`, a type assertion other than `as const`, or an implicit-any diagnostic; an
  assertion counts when it initializes the declaration of a name the span uses, wherever that
  declaration is (`const app = el!` at module level). For `definedness` claims it is also withheld
  when the claimed file imports a member its module does not export (TS2305, TS1192 and their
  family: uses of that name report nothing) and when a name in the span is declared with a
  definite assignment `!`, which silences TS2454. The import-member guard reads only the claimed
  file: a missing member re-exported through another file (`export { nope } from "./h"` in
  `index.ts`, imported from `index.ts` here) reports its error in that other file, so a
  `definedness` claim at the use site can still be refuted.
- **Some ordinary TypeScript loses refutation, by design of the guards.** Measured on TypeScript
  6.0.3 on 2026-11-12 (re-measure with `python -m pytest tests/test_ts_probe.py -q`):
  - a function or component that reads a `const`, `let` or `class` declared below it in the same
    file (a `styles` object at the end of a component file, say) gets no `FALSE_POSITIVE`, because
    the read may run in the temporal dead zone and tsc does not check that across functions;
  - a file with an unresolved side-effect import, such as `import "./app.css";` without a
    `declare module "*.css"` anywhere in the program, is a TS2882 under TypeScript 6, an
    environment diagnostic, so nothing in that file is refuted until the declaration exists;
  - a read of a field that tsc reports as never assigned (a TS2564 or TS2565 on the field's
    name, in whichever program file declares it) blocks refutation in the scope of the read, as
    a `declare` field and `x!: T` do; every other field stays refutable, so a repository not
    built with `strictPropertyInitialization` loses refutation only where it reads such a field;
  - not covered (known residual; finding STAKES-009, parked for the owner): `nullability`
    refutation trusts tsc's narrowing of a property reference across an await, across a call
    that mutates the object, and across a getter read twice, so a stale read such as
    `if (this.v) { await tick(); return this.v.n; }` can be refuted;
  - a file tree-sitter cannot parse cleanly never refutes, and that includes valid TypeScript the
    installed `tree-sitter-typescript` 0.23.2 grammar does not know, such as
    `export type * from "./x";`, `interface I<in out T>` and `import defer * as ns from "./x";`;
  - not covered (known residual for the owner, from finding BUG-003): a name whose declaration
    the compiler rejected, such as `const user: User = find()` where `find()` returns
    `User | null` (a TS2322 at the declaration), is trusted at its uses, in the same function, at
    module scope and in another file, so a real null dereference there can be refuted; and a
    `// @ts-ignore` above an import that names a missing member hides the TS2305, so a
    `definedness` claim on that name can be refuted;
  - not covered: a field assigned in the constructor, but read by a method the constructor calls
    before that assignment, is undefined there while tsc reports nothing, so a `nullability` claim
    about that read can still be refuted.
- **TypeScript `.d.ts` claims never refute.** `skipLibCheck` leaves declaration files unchecked, so
  the compiler's silence about one proves nothing.
- **TypeScript nullability claims never refute on index access.** Any element access or
  index-signature property read in the span blocks refutation, because `noUncheckedIndexedAccess`
  is not forced and the compiler is silent about exactly those reads. Destructuring is the same
  read (`const [u] = items`, `[u] = items`, `const { alice } = m` on a `Record`), except a position
  inside a tuple's fixed length (a `useState` pair, the pairs of `Object.entries`), and so is a
  name in the span whose declaration is one of these reads. A value that reaches the span through
  a later assignment, another name or a function's return is not followed.
- **TypeScript `definedness` is not confirmed when the environment is incomplete.** A TS2688 (a
  type package that cannot be found), an "install type definitions" diagnostic anywhere in the
  program (TS2580, TS2581, TS2582, TS2591, TS2592, TS2593), or a `package.json` that declares an
  `@types` package that is not installed, withholds `CONFIRMED`. The `package.json` files are read
  from the claimed file's directory up to the repository root, or up to the filesystem root when
  there is no `.git`, and one that does not parse or has a dependency section that is not an object
  counts as incomplete. With no tsconfig, or one that leaves `types` unset, the probe includes every
  `@types` package it can find, because TypeScript 6 stopped doing that. This widening is
  TypeScript 5 parity, not TypeScript 6 behaviour: it can make a name read as defined that the
  repository's own TypeScript 6 build would report, so a `definedness` claim about such a global
  can be refuted although `tsc` in the repository flags it.
- **A TypeScript repository without installed dependencies can confirm but rarely refute.**
  Unresolved modules are environment diagnostics in the claimed file, and those block refutation.
  When the tsconfig `extends` a package that is not installed (`@tsconfig/strictest`, say), the
  probe refuses the config, so nothing settles: every claim in that project is `UNCERTAIN`.
  Confirmation has one exception: an unresolved side-effect import (`import "x";`) withholds a
  `type` or `definedness` confirmation, in the claimed file for any specifier and anywhere in the
  program for a package specifier (`import "reflect-metadata";`), because such an import has no
  bindings and only augments globals.
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
