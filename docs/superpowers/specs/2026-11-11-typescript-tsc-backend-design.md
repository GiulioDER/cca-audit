# TypeScript backend, stage 1: tsc settles definedness, type and nullability

Date: 2026-11-11. Status: design, awaiting owner approval.

## Intent

CCA settles Python `definedness`, `type` and `nullability` findings with pyright before a
model is asked to adjudicate them. A finding about a `.ts` file today reaches
`cca_checks/__main__.py::_resolve_backend`, finds no backend, and escalates to UNCERTAIN.
The owner wants TypeScript to behave as Python already does: a deterministic tool confirms
or refutes these findings.

The checker is the TypeScript compiler, playing pyright's role. CCA drives it through its
compiler API from a small Node script it ships (the type probe), so that it can ask the
type of every expression in scope as well as collect diagnostics. Every verdict keeps
the three-way rule of `pyright_check.verdict_for_claim`: a diagnostic at the claimed line
CONFIRMS; silence REFUTES only when the checker could actually see the code; anything else
is UNCERTAIN.

## Goals

1. A `TypeScriptBackend` registered in `cca_checks/languages/__init__.py::BACKENDS`,
   covering `.ts`, `.tsx`, `.mts` and `.cts`, declaring `claim_types` =
   `{"definedness", "type", "nullability"}` and nothing else.
2. A `cca_checks/tsc_check.py` that runs the type probe (`cca_checks/ts_probe.js`) under an
   auditor-controlled configuration and settles those three claim types, with the same
   fail-closed cascade as `pyright_check.py`.
3. Fixtures and tests that prove each guard can go red (see Testing).
4. CI on Ubuntu runs the TypeScript tests with a pinned `typescript`, and they may not
   skip there.
5. Stage 2, after fixtures pass: one hunt-mode run on a real TypeScript repository the owner
   picks, pre-registered before it runs.

## Non-goals (this piece)

- `taint` (semgrep with a TypeScript catalog) and `clock_leak` (tree-sitter). Owner's
  decision: later, as their own pieces. The backend must not declare them, so they keep
  escalating.
- `.js`, `.jsx`, `.mjs`, `.cjs`. JavaScript typing under `checkJs` is too weak to license a
  refutation.
- Any change to auditor prompts or agent files. The auditors already emit these claim
  types; the registry routes them by extension.
- Running tsc on VPS2. Node works on this workstation, and the VPS2 remote runner exists
  only because semgrep could not run here.

## Anti-goals (failure even if it technically works)

- **A FALSE_POSITIVE the code does not earn.** Any path where suppression, `any`, a
  weakened repo config, a missing dependency, or a file outside the program produces
  silence that is read as refutation. This is the failure the whole layer exists to
  prevent.
- **Running the audited repository's code.** In hunt mode the target is code nobody here
  wrote. CCA must never execute the target's `node_modules/.bin/tsc`, its `typescript`
  package, or anything else from its tree.
- **A CONFIRMED that is really an environment fact.** "Cannot find name 'process'" because
  `@types/node` is not installed is not a definedness bug. Pyright's
  `reportMissingImports` exclusion (`pyright_check.py`, comment above
  `DEFINEDNESS_RULES`) is the precedent.
- **Silently self-disabling.** A design that ends up UNCERTAIN on every real repository
  looks safe and settles nothing. Stage 2 measures this.

## Constraints

- The backend contract in `cca_checks/languages/base.py`: positive `claim_types`,
  `enclosing_span` raises rather than widens, `settle` only sees declared claim types.
- `cca_checks/toolpath.py::resolve_tool` for every binary.
- Owner's standing rules: a new behavior test is shown red against a plausible mutation
  before green is trusted; heavy test runs go to VPS2 through `vps2-pytest`; measurements
  are pre-registered and committed before they run.
- Python changes stay minimal (owner's Python rules, 2026-11-01): reuse
  `pyright_check` helpers where they fit rather than copy them; no abstraction with one
  caller.

## Decided with the owner (2026-11-11)

- The TypeScript compiler is the checker. Parity with Python for the three pyright claim
  types first; taint and clock_leak later.
- The compiler comes from a global `typescript` install, never the target's own copy.
  Absent means UNCERTAIN.
- It runs locally.
- The repo's tsconfig contributes only an allow-list of resolution options; everything
  that decides what gets checked is forced by CCA.
- The program's file list must contain the claimed file, the analogue of pyright's
  `summary.filesAnalyzed`.
- **Ship the type probe** (decided after the design review): `any` that reaches the scope
  from outside it (`JSON.parse(s).user.name`, a helper declared elsewhere as returning
  `any`, a shorthand ambient module) is invisible to plain `tsc` diagnostics, so a CCA
  script asks the compiler for the type of every expression in the span and blocks
  refutation when any is `any`. This replaces running the `tsc` CLI and parsing its text.
- Reading the target's `node_modules/@types` and other `.d.ts` files is data, not code
  execution, and is allowed. When dependencies are not installed, refutation is blocked;
  confirmation still works.
- Fixtures first, then a real repo.

## Design

### Running the compiler

`run_tsc(path, span) -> TscResult | None`. None means "could not tell, escalate", exactly
as `run_pyright` returns None.

1. `resolve_tool("node")`, then locate the global `typescript` package (for example
   through `npm root -g`, itself launched through `resolve_tool`). Either missing, or the
   package resolving inside the audited tree: None.
2. Find the project: the nearest `tsconfig.json` walking up from the claimed file, stopping
   at the repository root. None found: CCA's generated defaults alone.
   **Solution configs (my call, in scope):** when that file is a solution config
   (`"files": []` plus `references`, the Vite, Nx and Angular default), follow the
   references and use the one whose resolved file list contains the claimed file. None
   contains it, or more than one does: None. Without this, those repositories would
   always be UNCERTAIN, which is the self-disabling anti-goal.
3. Resolve the repo's tsconfig, `extends` chain included, with the compiler's own
   `ts.getParsedCommandLineOfConfigFile` inside the probe, so CCA does not reimplement
   `extends`. Reading a config is data; nothing in it is executed. A config that does not
   parse: None.
4. The probe builds the effective options. It copies **only** these keys from the repo's
   resolved `compilerOptions`: `baseUrl`, `paths`, `rootDirs`, `moduleResolution`,
   `module`, `moduleSuffixes`, `customConditions`, `jsx`, `jsxImportSource`, `lib`,
   `target`, `types`, `typeRoots`, `allowArbitraryExtensions`, `resolveJsonModule`,
   `esModuleInterop`, `allowSyntheticDefaultImports`, `experimentalDecorators`,
   `useDefineForClassFields`. Every key not on the list is dropped. The allow-list lives
   in Python and is passed in the request, so the Python tests see it.
5. It forces: `strict: true` (and each strict sub-flag explicitly true, so no inherited
   `false` can survive), `noImplicitAny: true`, `noCheck: false`, `noEmit: true`,
   `skipLibCheck: true`, `allowJs: false`, `checkJs: false`, `incremental: false`,
   `composite: false`. `skipLibCheck` only skips checking `.d.ts` bodies, not using them,
   and keeps a broken dependency typing from flooding the output.
6. Program membership: the repo's resolved file list (from `files`, `include`, `exclude`),
   so ambient declarations the repo ships in its own `.d.ts` files are in scope, plus the
   claimed file added explicitly. No repo tsconfig: the claimed file alone.
7. Launch: `node <cca_checks>/ts_probe.js` with the request as JSON on stdin (absolute
   path of the global `typescript` module, the repo tsconfig if any, the allow-list, the
   forced options, the claimed file, the span lines). `cwd` is outside the audited tree.
   The script `require`s `typescript` by that absolute path and nothing else outside the
   Node standard library.
   **Timeout (my call):** a TypeScript-specific ceiling, `CCA_TS_TIMEOUT_S`, default 600 s,
   alongside `RUST_TIMEOUT_S` in `cca_checks/config.py`, because every claim type-checks
   the whole program. No cache across claims in this piece.
8. The probe prints one JSON object:
   - `version`: `ts.version`;
   - `files`: every source file in the program;
   - `diagnostics`: `ts.getPreEmitDiagnostics(program)`, each with code, message
     (flattened), and file plus start and end line when it has a location. Unlocated ones
     (TS2688, TS5xxx, TS18003) are kept as program-wide facts, never a reason to abort
     (my call);
   - `any_lines`: lines in the span where an expression node (identifier, property access,
     element access, call) has a type with `TypeFlags.Any` per `checker.getTypeAtLocation`.
     The compiler's error type for an unresolved name is an `any` too, which is the right
     reading: blind;
   - `directives`: lines of `@ts-ignore` / `@ts-expect-error` comments in the file and
     whether `@ts-nocheck` is present, read from the compiler's own comment ranges, so a
     directive inside a string does not count;
   - `subscript_lines`: lines in the span that contain an element access.

   Timeout, nonzero exit, output that is not one JSON object, or a missing or mistyped
   field: None. Python validates the shape once, at this boundary.
9. The claimed file (normalized, case-insensitive on Windows) must be in `files`. Absent:
   None.

`TscResult` holds those fields. Diagnostics carry start and end lines, so CONFIRM can use
the same range-covers-line rule as `pyright_check._diags_at`.

**Version policy (my call).** The code sets below are verified against one TypeScript
major, pinned in CI. `run_tsc` returns None for any other `version` major, because a
renumbered blindness or environment code would switch a guard off silently and buy
refutations. Supporting another major means re-verifying the sets and adding it to an
explicit supported list. The builder confirms which major is current, whether TypeScript
6.0 deprecations affect the allow-list (`baseUrl`, `moduleResolution: node10`,
`target: es5`), and that the major still ships the JavaScript compiler API the probe uses
(TypeScript 7 is a native rewrite) before pinning.

### Diagnostic code sets

Pairwise disjoint, enforced by a test, as for pyright's rule sets. The exact codes are
confirmed against the pinned tsc version in the first task; the intent of each set is
the decision.

- **definedness:** TS2304 (cannot find name), TS2552 (cannot find name, did you mean),
  TS2448 and TS2454 (used before declaration / before assignment).
  **Excluded** and therefore escalate: TS2580, TS2582, TS2591, TS2592, TS2593 ("do you
  need to install type definitions for ..."), which are environment facts.
- **nullability:** TS2531, TS2532, TS2533 (object possibly null / undefined / both),
  TS18047, TS18048, TS18049 (named value possibly null / undefined / both), TS2722
  (invoking possibly undefined), TS18046 is **not** here (`unknown`, see blindness).
- **type:** TS2322, TS2345, TS2339, TS2741, TS2769, TS2353, TS2551, TS2362, TS2363,
  TS2365, TS2367, TS2349, TS2554, TS2555. Drift may cost a confirmation, never buy a
  refutation: an unrecognized code at the line escalates (below).
- **environment:** TS2307 (cannot find module), TS7016 (module has no declaration file:
  its import is `any`, and it sits at the import line, outside any function span, so it
  belongs here rather than in blindness), TS2688 (cannot find type definition file),
  TS2580 to TS2593 as above, TS5xxx (config errors).
- **blindness:** TS7005, TS7006, TS7008, TS7010, TS7011, TS7015, TS7019, TS7031,
  TS7034, TS7053 (implicit `any` family) and TS18046 (`unknown`).

### Verdict

`verdict_for_tsc(claim, result, codes, span)`, following `pyright_check.verdict_for_claim`
in order:

1. `result is None`: UNCERTAIN, "tsc unavailable or could not analyze".
2. **Suppression in the file blocks everything.** If the claimed file contains
   `@ts-nocheck`, or a `@ts-ignore` / `@ts-expect-error` directive within the enclosing
   span: no FALSE_POSITIVE for any claim type, definedness included (unlike pyright, where
   `enableTypeIgnoreComments: false` removes the hole; tsc has no such switch). A
   diagnostic that still appears at the line may still CONFIRM. Read from the probe's
   `directives`.
3. An in-set diagnostic whose range covers the claim line: CONFIRMED, source `tsc`.
   Exception (my call, narrowed after review): a definedness CONFIRMED is UNCERTAIN when
   the program has a TS2688, because a missing type package can remove globals it
   declares. TS2307 does not block it: an unresolved import makes its bindings `any`, not
   undefined. This keeps the owner's decision that confirmation works without installed
   dependencies, except in that one case.
4. Any other diagnostic at the line: UNCERTAIN, naming the codes seen.
5. Refutation requires the span. `span is None` (grammar missing, parse error): UNCERTAIN.
   This is stricter than pyright, which skips the nearby check when the span fails; for a
   new backend there is no reason to inherit that.
6. In-set diagnostic elsewhere in the span: UNCERTAIN, "off by a line rather than false".
7. Any environment diagnostic located in the claimed file, or any unlocated one: UNCERTAIN
   for every claim type. An unresolved import makes its bindings `any`, so silence there
   is blindness.
7a. **Index access (my call).** `noUncheckedIndexedAccess` is not forced, because forcing it
   would CONFIRM claims at guarded index sites (`if (i < a.length) a[i].x`) and feed a
   wrong auto-fix. Instead, a nullability claim whose line contains an element access
   (the probe's `subscript_lines`) can never be FALSE_POSITIVE.
8. For `type` and `nullability`: any blindness diagnostic in the span, or any line in the
   probe's `any_lines`, UNCERTAIN. This covers explicit `any`, `as any`, and `any` that
   arrives from outside the span (`JSON.parse`, a helper returning `any`, a shorthand
   ambient module), which was the reason for the probe.
9. Otherwise FALSE_POSITIVE, source `tsc`, evidence naming the code set and span.

### Spans

`enclosing_span` uses `cca_checks/treesitter.py` with the `tree-sitter-typescript` wheel.
That wheel ships two grammars, `typescript` and `tsx`; `.tsx` uses `tsx`, the rest use
`typescript`. Function node types: `function_declaration`, `function_expression`,
`arrow_function`, `method_definition`, `generator_function_declaration`,
`generator_function`. A grammar that is not installed raises, as for Rust.

New `typescript` extra in `pyproject.toml`: `tree-sitter>=0.23`,
`tree-sitter-typescript>=0.23` (builder confirms the current version line). Added to
`verify` as well, matching how `tree-sitter-rust` sits in both. `typescript` itself is an
npm tool on PATH, like cargo; it is documented, not pip-installed.

## Testing

### Fixtures

`tests/fixtures/typescript/`: a small project with its own `package.json` (no
dependencies installed by default) and `tsconfig.json`, one file per trap. Fixtures that
need `@types` vendor a minimal `.d.ts` inside the fixture tree rather than running
`npm install` in tests.

Each claim type gets a defect that must be CONFIRMED and a guarded twin that must be
FALSE_POSITIVE. Each guard gets a fixture that must be UNCERTAIN, never FALSE_POSITIVE:

| Guard | Fixture |
|---|---|
| suppression | `@ts-ignore`, `@ts-expect-error`, `@ts-nocheck` over a real defect |
| explicit `any` | a null access through a value typed `any`, and through `as any` |
| `any` from outside the span | `JSON.parse(s).user.name`; a call to a helper in another file declared as returning `any`; an import from `declare module "x";` |
| index access | `arr[i].x` with no guard: nullability must not be FALSE_POSITIVE |
| untyped module | an import of a module without declarations (TS7016) used inside a function |
| solution config | `tsconfig.json` with `files: []` and `references` to `tsconfig.app.json` holding `strict` and `paths` |
| unlocated diagnostic | repo `types: ["node"]` with no `@types/node` (TS2688) |
| version | a tsc reporting an unsupported major (monkeypatched): UNCERTAIN |
| implicit `any` | an unannotated parameter dereferenced |
| environment | an import of a module that is not present |
| environment, confirm side | `process.env.X` with no `@types/node`: definedness must not CONFIRM |
| repo weakens the config | repo tsconfig with `strict: false`, and one with `noCheck: true`, over a real nullability defect: must still CONFIRM |
| not in program | claimed file excluded by the repo's `exclude`, and the generated config fails to force it in (mutation) |
| tool failure | node or the global `typescript` missing, nonzero exit, timeout, output that is not one JSON object, a missing field (monkeypatched) |
| off by a line | a multi-line call where the defect is one line from the claim |
| grammar missing | span resolution raises: no FALSE_POSITIVE |
| hijack | a `node` / `node.exe` in the audited root is refused by `resolve_tool`; a `typescript` package path inside the audited tree is refused |
| directive in a string | `const s = "// @ts-ignore"` over a guarded line: must still be FALSE_POSITIVE |

### Red proof

Per the owner's 2026-09-08 rule. Each guard test is run against a deliberate mutation of
the line that implements it (for example: delete the suppression check in step 2; drop
`strictNullChecks` from the forced options; skip the program membership check; copy the
whole repo `compilerOptions` instead of the allow-list; make the probe's `any` test
compare against the wrong flag so `any_lines` comes back empty). It must fail in its
assertion, not in setup. The node ID, mutation and failure reason go in the PR.

### Where tests run

Unit tests with monkeypatched probe output run anywhere. Tests that run the real probe
need Node and a global `typescript`; locally they skip only when either is absent. The
full suite runs on VPS2 through `vps2-pytest` per standing instruction, which means VPS2
needs Node and the pinned `typescript` too (builder checks, and asks before installing
anything there). In CI the Ubuntu job sets up Node and installs the pinned `typescript`
globally with npm, and the TypeScript tests may not skip, the same rule the Rust
tests have (`.github/workflows/ci.yml`, the comment on clippy not being allowed to skip).
A test asserts the CI job installs it, following `tests/test_ci_contract.py`.

### Stage 2: a real repository

The owner picks the repository after fixtures pass. Before running, a pre-registration in
`docs/preregistrations/` records the predicted share of tsc-routed claims that come out
CONFIRMED, FALSE_POSITIVE and UNCERTAIN, and what result would mean the design
self-disables (for example, UNCERTAIN on nearly every claim because of environment
diagnostics). It is committed before the run. The run happens in hunt mode against a
checkout with dependencies installed and, separately, without, since both are realistic.

## Left to the builder

- How `run_tsc` and the verdict reuse `pyright_check` helpers versus a small parallel
  module; prefer reuse where the shapes match.
- The final code sets, confirmed against the pinned version and recorded in the module
  docstring with the version.
- The exact request and response field names, and how `ts_probe.js` is packaged so the
  installed wheel ships it.
- Which syntax kinds count as an expression node for `any_lines`, as long as identifiers,
  property accesses, element accesses and calls are included.
- Docs: `docs/v3-design.md` (the "TypeScript and Go are not shipped" line),
  `docs/limits.md`, `docs/configuration.md` install notes, `CHANGELOG.md`.

## Amendments made during the build (2026-11-11)

Recorded here because the sections above were written before the compiler was run. Each was a
controller ruling during subagent-driven execution, made after a reviewer measured the case on
TypeScript 6.0.3.

- **Pinned major 6, installed 6.0.3.** TypeScript 7.0.2 (npm `latest`) ships no JavaScript compiler
  API. The probe forces `ignoreDeprecations: "6.0"`.
- **`types` defaults to `["*"]`** when the repo's config leaves it unset or there is no config, with
  `typeRoots` from every `node_modules/@types` above the file in the no-config case. TypeScript 6
  stopped auto-including `@types`; this restores TypeScript 5 behaviour, and it can make a name read
  as defined that the repo's own TypeScript 6 build would flag.
- **Allow-list additions:** `moduleDetection`, `allowImportingTsExtensions`,
  `rewriteRelativeImportExtensions`, besides the 19 options listed in "Running the compiler".
- **New probe fields:** `assertion_lines` (non-null and casts other than `as const`, in the span or
  reached through a declaration), `definite_assignment_lines`, and `directives.targets` (the line
  each directive actually suppresses). `subscript_lines` also covers destructuring and property
  reads through an index signature, anywhere in the span.
- **Definedness CONFIRMED is also withheld** when a declared `@types` package is not installed,
  when the program reports an "install type definitions" code, or when the claimed file has an
  import-member error. The probe refuses a declaration-file target.
- **Known residual, not fixed:** a missing member re-exported through another file still refutes
  definedness at the use site.
- **Correction (2026-11-12, L4 audit, DOC-001) to the "Definedness CONFIRMED is also withheld"
  item above:** an import-member error in the claimed file withholds a definedness
  `FALSE_POSITIVE`, not a `CONFIRMED`. The code returns `CONFIRMED` before it reads
  `IMPORT_MEMBER_CODES`; the other two conditions in that item (an uninstalled `@types` package,
  an "install type definitions" code) do withhold the `CONFIRMED`.
- **Amendment (2026-11-12, L4 audit, FIX-015) narrowing the owner's decision recorded in step 3 of
  "Verdict":** an unresolved side-effect import (`import "x";`) withholds a `type` or
  `definedness` confirmation: in the claimed file for any specifier, and program-wide for a package
  (non-relative) specifier, because such an import has no bindings and only augments globals. An
  unresolved ordinary import still lets confirmation work, as decided.
