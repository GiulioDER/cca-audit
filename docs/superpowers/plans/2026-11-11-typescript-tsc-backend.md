# TypeScript tsc Backend Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `definedness`, `type` and `nullability` claims about `.ts`, `.tsx`, `.mts` and `.cts` files are settled by the TypeScript compiler with the same fail-closed rules pyright applies to Python.

**Architecture:** A shipped Node script, `cca_checks/ts_probe.js`, loads the globally installed `typescript` package by absolute path, builds the program under CCA's forced options, and prints one JSON object (diagnostics, program files, `any` lines, suppression directives, index-access lines). `cca_checks/tsc_check.py` launches it, validates the JSON once, and renders the verdict. `cca_checks/languages/typescript.py` registers the backend and computes function spans with tree-sitter.

**Tech Stack:** Python 3.10+, Node (22.11.0 measured locally 2026-11-11), the `typescript` npm package installed globally, `tree-sitter` plus `tree-sitter-typescript`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-11-typescript-tsc-backend-design.md` (commit `9a0faca`). Read it before any task; this plan does not repeat its reasoning.

## Global Constraints

- Claim types declared by the backend: exactly `{"definedness", "type", "nullability"}`. `taint` and `clock_leak` must keep escalating.
- Extensions: `.ts`, `.tsx`, `.mts`, `.cts`. Never `.js`, `.jsx`, `.mjs`, `.cjs`.
- Never execute anything from the audited tree: not its `node_modules/.bin/tsc`, not its `typescript` package, not a `node` binary in its root. `node` and `npm` go through `cca_checks/toolpath.py::resolve_tool`.
- Copied repo options (allow-list, verbatim from the spec): `baseUrl`, `paths`, `rootDirs`, `moduleResolution`, `module`, `moduleSuffixes`, `customConditions`, `jsx`, `jsxImportSource`, `lib`, `target`, `types`, `typeRoots`, `allowArbitraryExtensions`, `resolveJsonModule`, `esModuleInterop`, `allowSyntheticDefaultImports`, `experimentalDecorators`, `useDefineForClassFields`.
- Forced options: `strict: true` and every strict sub-flag explicitly `true`, `noImplicitAny: true`, `noCheck: false`, `noEmit: true`, `skipLibCheck: true`, `allowJs: false`, `checkJs: false`, `incremental: false`, `composite: false`.
- `noUncheckedIndexedAccess` is NOT forced.
- Timeout: `CCA_TS_TIMEOUT_S`, default 600, exposed as `cca_checks.config.TS_TIMEOUT_S`.
- One supported TypeScript major, pinned in CI; any other major is UNCERTAIN.
- Every new behavior test is shown red against the named mutation before green is trusted (owner rule, 2026-09-08). Record node ID, mutation, and failure reason in the PR body.
- Full suite runs on VPS2 only: `bash ~/.claude/bin/vps2-pytest`. Single test files may run locally.
- Python edits change only the lines the task needs (owner rule, 2026-11-01). Reuse `pyright_check` and `treesitter` helpers where the shapes match.
- No dash as punctuation in docs, comments, commit messages or the PR body.
- Ask the owner before any `npm install -g` here and before installing anything on VPS2.

## Review Focus

1. **Windows paths.** The probe reports files with forward slashes; the CLI gets `--file src\app.ts` relative to cwd. Expected: the membership check still finds the file. Pinned by `test_same_file_normalizes_case_and_separators` and `test_run_tsc_accepts_a_relative_path` in Task 3.
2. **A claim inside a `.d.ts` file.** `languages.resolve` maps `types.d.ts` to `.ts`, and `skipLibCheck: true` skips checking every declaration file, so silence there is blindness. Expected: never FALSE_POSITIVE. Pinned by `test_declaration_file_never_refutes` in Task 5.
3. **CRLF line endings.** tree-sitter spans and compiler line numbers must agree on a CRLF file. Expected: a CRLF copy of `null_access.ts` CONFIRMS at the same line. Pinned by `test_crlf_file_confirms_at_the_same_line` in Task 5.
4. **`.tsx` with the repo's `jsx` option.** Expected: the `tsx` grammar computes the span and the copied `jsx` option keeps TS17004 away, so a null defect in a component CONFIRMS. Pinned by `test_tsx_component_confirms` in Task 5.
5. **A suppression directive outside the span.** `@ts-ignore` at file top level while the claim is in a function further down. Expected: it does not block refutation there (only `@ts-nocheck` is file-wide). Pinned by `test_directive_outside_span_does_not_block` in Task 4.

---

### Task 1: TypeScript spans from tree-sitter

**Files:**
- Modify: `cca_checks/treesitter.py` (`_GRAMMAR_MODULES` and `_parser` only)
- Create: `cca_checks/languages/typescript.py` (span code only; the backend class arrives in Task 5)
- Modify: `pyproject.toml` (new `typescript` extra; add the two packages to `verify` and `dev`)
- Test: `tests/test_typescript_spans.py`

**Interfaces:**
- Produces: `cca_checks.languages.typescript.FUNCTION_TYPES: frozenset[str]`, `grammar_for(path: str) -> str` (returns `"tsx"` for `.tsx`, else `"typescript"`), `typescript_enclosing_span(path: str, line_1based: int) -> tuple[int, int]` (raises on missing grammar or unreadable file, like `ts.enclosing_span`).

- [ ] **Step 1: Write the failing tests**

```python
ts_grammar = pytest.importorskip("tree_sitter_typescript")

def test_arrow_function_is_its_own_span(tmp_path):
    p = tmp_path / "a.ts"
    p.write_text("const x = 1;\nconst f = (u: {n: number} | null) => {\n  return u.n;\n};\nconst y = 2;\n")
    assert typescript_enclosing_span(str(p), 3) == (2, 4)

def test_method_is_its_own_span(tmp_path):
    p = tmp_path / "a.ts"
    p.write_text("class C {\n  m(u: string | null) {\n    return u.length;\n  }\n}\n")
    assert typescript_enclosing_span(str(p), 3) == (2, 4)

def test_tsx_uses_the_tsx_grammar(tmp_path):
    p = tmp_path / "a.tsx"
    p.write_text("export function C(p: {n: string | null}) {\n  return <div>{p.n.length}</div>;\n}\n")
    assert grammar_for(str(p)) == "tsx"
    assert typescript_enclosing_span(str(p), 2) == (1, 3)

def test_top_level_line_falls_back_to_whole_file(tmp_path):
    p = tmp_path / "a.ts"
    p.write_text("const a = 1;\nconst b = a;\n")
    assert typescript_enclosing_span(str(p), 2) == (1, 2)

def test_missing_grammar_raises(monkeypatch, tmp_path):
    # Patch _GRAMMAR_MODULES to point at a module that does not exist, clear _parser's cache.
    ...
    with pytest.raises(GrammarUnavailable):
        typescript_enclosing_span(str(p), 1)
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_typescript_spans.py -q`
Expected: ImportError on `cca_checks.languages.typescript` (setup failure, not yet red proof; the red proof is Step 6).

- [ ] **Step 3: Install the grammar locally**

Run: `pip install "tree-sitter-typescript>=0.23"` and confirm `python -c "import tree_sitter_typescript as t; print(t.language_typescript, t.language_tsx)"` prints two functions. If the attribute names differ in the installed version, use the real names in Step 4 and note them in the module docstring.

- [ ] **Step 4: Implement**

`_GRAMMAR_MODULES` becomes `{language: (module_name, attribute)}` with `"rust": ("tree_sitter_rust", "language")`, `"typescript": ("tree_sitter_typescript", "language_typescript")`, `"tsx": ("tree_sitter_typescript", "language_tsx")`; `_parser` calls `getattr(grammar, attribute)()`. The install hint in `GrammarUnavailable` names the extra `typescript` for both TypeScript grammars. `FUNCTION_TYPES = frozenset({"function_declaration", "function_expression", "arrow_function", "method_definition", "generator_function_declaration", "generator_function"})`. `typescript_enclosing_span` calls `ts.enclosing_span(path, line, grammar_for(path), FUNCTION_TYPES)`. pyproject: `typescript = ["tree-sitter>=0.23", "tree-sitter-typescript>=0.23"]`, and `"tree-sitter-typescript>=0.23"` appended to `verify` and `dev`.

- [ ] **Step 5: Run, expect pass**

Run: `python -m pytest tests/test_typescript_spans.py tests/test_treesitter.py tests/test_dev_extra_completeness.py -q`
Expected: all pass, none skipped.

- [ ] **Step 6: Red proof**

Mutation: remove `"arrow_function"` from `FUNCTION_TYPES`. Run `test_arrow_function_is_its_own_span`; expected assertion failure `(1, 5) != (2, 4)`. Restore, rerun green. Record it.

- [ ] **Step 7: Commit**

```bash
git add cca_checks/treesitter.py cca_checks/languages/typescript.py pyproject.toml tests/test_typescript_spans.py
git commit -m "Compute TypeScript function spans with tree-sitter"
```

---

### Task 2: The type probe script and its fixtures

**Files:**
- Create: `cca_checks/ts_probe.js`
- Create: `tests/fixtures/typescript/` (layout below)
- Modify: `pyproject.toml` (`[tool.setuptools.package-data]`: add `"ts_probe.js"`)
- Test: `tests/test_ts_probe.py`

**Interfaces:**
- Produces, request (JSON on stdin):
  `{"protocol": 1, "typescript": <abs dir of the typescript package>, "tsconfig": <abs path or null>, "file": <abs path>, "span": [lo, hi], "copy_options": [<names>], "forced_options": {<tsconfig-style JSON>}}`
- Produces, response (one JSON object on stdout, exit 0):
  `{"protocol": 1, "version": "<ts.version>", "files": [<abs paths>], "diagnostics": [{"code": int, "message": str, "file": str|null, "start_line": int|null, "end_line": int|null}], "any_lines": [int], "directives": {"lines": [int], "nocheck": bool}, "subscript_lines": [int]}`
  Lines are 1-indexed. Any refusal (config does not parse, solution config with zero or several owning references, `require` fails) is exit code 2 with one line on stderr and nothing on stdout.

Fixture layout (each directory has its own `tsconfig.json`; line numbers asserted in tests are the line of the defect):

| Path | Content and purpose |
|---|---|
| `basic/tsconfig.json` | `strict: true`, `module: "esnext"`, `moduleResolution: "bundler"`, `include: ["*.ts", "*.d.ts"]` |
| `basic/undefined_name.ts` / `defined_name.ts` | `RISK_CAP` used undeclared / declared |
| `basic/null_access.ts` / `null_guarded.ts` | `u.name` on `{name: string} \| null`, unguarded / guarded |
| `basic/bad_arg.ts` / `good_arg.ts` | `takesNumber("x")` / `takesNumber(1)` |
| `basic/suppressed_ignore.ts`, `suppressed_expect.ts`, `suppressed_nocheck.ts` | the null defect under each directive |
| `basic/directive_in_string.ts` | `const s = "// @ts-ignore";` above a guarded access |
| `basic/directive_outside_span.ts` | `// @ts-ignore` on a top-level line, guarded access in a function below |
| `basic/explicit_any.ts`, `as_any.ts` | null access through `u: any`, and through `(u as any)` |
| `basic/json_parse.ts` | `return JSON.parse(s).user.name;` |
| `basic/helper.ts` + `any_helper.ts` | `export function load(): any`; `load().user.name` |
| `basic/ambient.d.ts` + `ambient_import.ts` | `declare module "untyped-x";`; a value from it dereferenced |
| `basic/implicit_any.ts` | `function f(u) { return u.x; }` |
| `basic/missing_module.ts` | imports `./nope` |
| `basic/index_access.ts` | `return a[i].x;` with no guard |
| `basic/multiline.ts` | a null defect inside a call spread over three lines, claim cites the last line |
| `tsx/tsconfig.json`, `tsx/jsx.d.ts`, `tsx/component.tsx` | `strict: true`, `jsx: "preserve"`; `jsx.d.ts` declares `namespace JSX { interface IntrinsicElements { div: {} } }` so no `@types/react` is needed; the component has a null defect |
| `weak/tsconfig.json` + `null_access.ts` | `strict: false`; the defect must still CONFIRM |
| `nocheck_config/tsconfig.json` + `null_access.ts` | `noCheck: true`; must still CONFIRM |
| `excluded/tsconfig.json` + `skipped.ts` | `exclude: ["skipped.ts"]`; the probe must still put it in the program |
| `solution/tsconfig.json`, `tsconfig.app.json`, `src/main.ts`, `src/lib/util.ts` | `files: []` plus `references`; the app config holds `strict` and `paths: {"@lib/*": ["./src/lib/*"]}`; `main.ts` imports `@lib/util` and has a null defect |
| `types_node/tsconfig.json` + `uses_process.ts` | `types: ["node"]`, no `@types/node`; `process.env.X` |

- [ ] **Step 1: Pin the TypeScript version (owner decision point)**

Run: `npm view typescript dist-tags --json` and `npm view typescript versions --json`. Pick the newest major whose package still ships the JavaScript compiler API (`lib/typescript.js` exporting `createProgram`; TypeScript 7 is the native rewrite and may not). Show the owner the chosen exact version (`TS_PIN`) and ask before running `npm install -g typescript@<TS_PIN>`. After install, record `node --version`, `tsc --version`, `npm root -g`.

- [ ] **Step 2: Write the fixtures** listed above.

- [ ] **Step 3: Write the failing tests**

Tests drive the script directly with `subprocess` and the request schema; all are gated with `@pytest.mark.skipif(shutil.which("node") is None or shutil.which("tsc") is None, reason=...)` (that exact `shutil.which` form, so `tests/test_ci_contract.py` sees both gates). A helper `probe(fixture_rel, line, span)` builds the request with the global package dir from `npm root -g`, the copy list and forced options from Task 3's constants (until Task 3 exists, define them in the test module and move them in Task 3).

```python
def test_reports_version_and_program_files(): r = probe("basic/null_access.ts", 3); assert r["version"].split(".")[0] == TS_MAJOR; assert any(f.endswith("null_access.ts") for f in r["files"])
def test_undefined_name_has_2304(): assert 2304 in codes_at(probe("basic/undefined_name.ts", L), L)
def test_null_access_has_a_nullability_code(): assert codes_at(r, L) & {18047, 2531}
def test_bad_arg_has_2345(): ...
def test_weak_config_is_overridden(): assert codes_at(probe("weak/null_access.ts", L), L) & {18047, 2531}
def test_nocheck_config_is_overridden(): same for "nocheck_config/null_access.ts"
def test_excluded_file_is_forced_into_the_program(): assert any(f.endswith("skipped.ts") for f in probe("excluded/skipped.ts", 1)["files"])
def test_solution_config_follows_the_owning_reference(): r = probe("solution/src/main.ts", L); assert 2307 not in all_codes(r); assert codes_at(r, L) & {18047, 2531}
def test_json_parse_line_is_an_any_line(): assert L in probe("basic/json_parse.ts", L)["any_lines"]
def test_any_helper_line_is_an_any_line(): ...
def test_ambient_module_line_is_an_any_line(): ...
def test_as_any_line_is_an_any_line(): ...
def test_typed_access_has_no_any_line(): assert probe("basic/null_guarded.ts", L)["any_lines"] == []
def test_directives_are_reported(): assert probe("basic/suppressed_ignore.ts", L)["directives"]["lines"] == [L - 1]; nocheck fixture reports "nocheck": True
def test_directive_inside_a_string_is_not_reported(): assert probe("basic/directive_in_string.ts", L)["directives"]["lines"] == []
def test_index_access_line_is_reported(): assert L in probe("basic/index_access.ts", L)["subscript_lines"]
def test_unlocated_diagnostic_has_null_file(): r = probe("types_node/uses_process.ts", L); assert any(d["code"] == 2688 and d["file"] is None for d in r["diagnostics"])
def test_dropped_option_is_not_applied(): # a repo option off the allow-list (for example "noImplicitAny": false in weak/) has no effect: implicit any still reports 7006
def test_bad_request_exits_2(): run with {"protocol": 1} only; assert returncode == 2 and stdout == ""
```

- [ ] **Step 4: Run, expect failure**

Run: `python -m pytest tests/test_ts_probe.py -q`
Expected: every test fails because `cca_checks/ts_probe.js` does not exist (node exits nonzero). Setup failure, not red proof.

- [ ] **Step 5: Implement `cca_checks/ts_probe.js`**

CommonJS, Node standard library plus `require(request.typescript)` only. Config: `ts.getParsedCommandLineOfConfigFile`; for a solution config (`fileNames` empty and `projectReferences` present) parse each reference and keep the ones whose `fileNames` contain the target (normalized with `path.resolve` and lower-cased on win32); exactly one or exit 2. Options: start from `ts.convertCompilerOptionsFromJson(forced_options)` and copy only `copy_options` keys from the parsed repo options. Root names: the repo's `fileNames` plus the target. Diagnostics: `ts.getPreEmitDiagnostics(program)`, message via `ts.flattenDiagnosticMessageText(d.messageText, "\n")`, lines via `getLineAndCharacterOfPosition(d.start)` and `(d.start + d.length)`. `any_lines`: walk the target `SourceFile`; for nodes whose kind is `Identifier`, `PropertyAccessExpression`, `ElementAccessExpression` or `CallExpression` and whose start line is in the span, record the line when `checker.getTypeAtLocation(node).flags & ts.TypeFlags.Any`. `directives`: scan the target's full text with `ts.createScanner(languageVersion, /*skipTrivia*/ false, languageVariant, text)` and test only `SingleLineCommentTrivia` and `MultiLineCommentTrivia` tokens: `/^\/\/\s*@ts-(ignore|expect-error)\b/` records the comment's line, `/@ts-nocheck\b/` sets `nocheck`. String and template tokens are never tested, which is what keeps `directive_in_string.ts` clean. `subscript_lines`: `ElementAccessExpression` start lines in the span.

- [ ] **Step 6: Run, expect pass**

Run: `python -m pytest tests/test_ts_probe.py -q`
Expected: all pass, none skipped. If a code in an assertion differs from what the compiler emits, print the probe output and fix the fixture or the expected code, then write every observed code into a table in the module docstring of `ts_probe.js` with `TS_PIN`.

- [ ] **Step 7: Red proof**

Mutations, each run against its test and restored after: (a) compare against `ts.TypeFlags.Unknown` instead of `Any`; `test_json_parse_line_is_an_any_line` must fail on its assertion. (b) Stop forcing `strictNullChecks`; `test_weak_config_is_overridden` must fail. (c) Copy every repo option instead of `copy_options`; `test_dropped_option_is_not_applied` must fail. (d) Drop the explicit target from root names; `test_excluded_file_is_forced_into_the_program` must fail. (e) Match directives by regex over the raw text; `test_directive_inside_a_string_is_not_reported` must fail.

- [ ] **Step 8: Commit**

```bash
git add cca_checks/ts_probe.js tests/fixtures/typescript tests/test_ts_probe.py pyproject.toml
git commit -m "Ship a TypeScript type probe that reports diagnostics and any-typed lines"
```

---

### Task 3: Launch the probe and validate its answer

**Files:**
- Create: `cca_checks/tsc_check.py` (launch and result types only; the verdict is Task 4)
- Modify: `cca_checks/config.py` (add `_DEFAULT_TS_TIMEOUT_S = 600` and `TS_TIMEOUT_S = _positive_int("CCA_TS_TIMEOUT_S", _DEFAULT_TS_TIMEOUT_S)` beside `RUST_TIMEOUT_S`)
- Modify: `tests/test_ts_probe.py` (import the constants from `tsc_check` instead of defining them)
- Test: `tests/test_tsc_run.py`

**Interfaces:**
- Consumes: the probe request and response schema from Task 2.
- Produces:
  - `COPY_OPTIONS: tuple[str, ...]` (the allow-list, in the spec's order) and `FORCED_OPTIONS: dict[str, object]`.
  - `SUPPORTED_TS_MAJORS: frozenset[int]` = `{<major of TS_PIN>}`.
  - `@dataclass(frozen=True) class TscDiagnostic: code: int; message: str; file: str | None; start_line: int | None; end_line: int | None`
  - `@dataclass(frozen=True) class TscResult: version: str; files: frozenset[str]; diagnostics: tuple[TscDiagnostic, ...]; any_lines: frozenset[int]; directive_lines: frozenset[int]; nocheck: bool; subscript_lines: frozenset[int]` where `files` and each diagnostic `file` are already passed through `same_file_key`.
  - `same_file_key(path: str) -> str`: `os.path.normcase(os.path.realpath(path))`.
  - `find_tsconfig(path: str) -> str | None`: nearest `tsconfig.json` walking up from the file's directory; stops after the first directory that contains `.git`, or at the filesystem root.
  - `typescript_package() -> str | None`: `resolve_tool("npm")`, run `npm root -g` with `PROBE_TIMEOUT_S`, return `<root>/typescript` if it contains `package.json`, else None.
  - `run_tsc(path: str, span: tuple[int, int]) -> TscResult | None`.

- [ ] **Step 1: Write the failing tests** (no real Node; `subprocess.run` and the two resolvers are monkeypatched on `cca_checks.tsc_check`)

```python
def test_valid_response_parses(): fake stdout = good JSON for the claimed file; r = run_tsc(str(f), (1, 3)); assert isinstance(r, TscResult) and 3 in r.any_lines
def test_node_missing_is_none(): resolve_tool -> None; assert run_tsc(...) is None
def test_typescript_package_missing_is_none(): ...
def test_typescript_package_inside_audited_tree_is_none(): package path under tmp repo root (cwd); assert None
def test_timeout_is_none(): subprocess raises TimeoutExpired; assert None
def test_nonzero_exit_is_none(): returncode 2; assert None
def test_non_json_stdout_is_none(): stdout "Found 3 errors"; assert None
@pytest.mark.parametrize("field", ["version", "files", "diagnostics", "any_lines", "directives", "subscript_lines"])
def test_missing_field_is_none(field): assert None
def test_mistyped_field_is_none(): "any_lines": "3"; assert None
def test_unsupported_major_is_none(): "version": f"{max(SUPPORTED_TS_MAJORS) + 1}.0.0"; assert None
def test_claimed_file_not_in_program_is_none(): files lists another file; assert None
def test_same_file_normalizes_case_and_separators(): on win32, same_file_key("C:\\X\\A.ts") == same_file_key("c:/x/a.ts"); skip elsewhere
def test_run_tsc_accepts_a_relative_path(tmp_path, monkeypatch): chdir; run_tsc("src/app.ts", ...) sends an absolute "file" in the request and matches the absolute path in "files"
def test_request_carries_the_allow_list_and_forced_options(): capture stdin; assert json "copy_options" == list(COPY_OPTIONS) and "forced_options" == FORCED_OPTIONS
def test_cwd_is_outside_the_audited_tree(): capture cwd kwarg; assert not _is_inside(cwd, repo_root)
def test_timeout_uses_ts_timeout(): capture timeout kwarg; assert == config.TS_TIMEOUT_S
def test_find_tsconfig_stops_at_git_boundary(tmp_path): tsconfig above the .git dir is not returned
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_tsc_run.py -q`
Expected: ImportError on `cca_checks.tsc_check` (setup).

- [ ] **Step 3: Implement** the Interfaces above in `cca_checks/tsc_check.py`. Validation happens once in a private `_parse_response(raw: str, claimed: str) -> TscResult | None`; every failure returns None. The probe path is `os.path.join(os.path.dirname(__file__), "ts_probe.js")`. Launch with `[node, probe]`, `input=json.dumps(request)`, `capture_output=True`, `text=True`, `encoding="utf-8"`, `cwd=tempfile.gettempdir()`, `timeout=TS_TIMEOUT_S`. The audited tree for the inside check is the `.git` root `find_tsconfig` walks to, or cwd when there is none.

- [ ] **Step 4: Run, expect pass**

Run: `python -m pytest tests/test_tsc_run.py tests/test_ts_probe.py tests/test_config.py -q`
Expected: all pass.

- [ ] **Step 5: Red proof**

Mutations: (a) skip the major check; `test_unsupported_major_is_none` fails. (b) skip the membership check; `test_claimed_file_not_in_program_is_none` fails. (c) compare raw paths instead of `same_file_key`; `test_run_tsc_accepts_a_relative_path` fails. (d) drop the inside check; `test_typescript_package_inside_audited_tree_is_none` fails.

- [ ] **Step 6: Commit**

```bash
git add cca_checks/tsc_check.py cca_checks/config.py tests/test_tsc_run.py tests/test_ts_probe.py
git commit -m "Launch the TypeScript probe and refuse any answer it cannot vouch for"
```

---

### Task 4: The verdict

**Files:**
- Modify: `cca_checks/tsc_check.py`
- Test: `tests/test_tsc_verdict.py` (pure unit tests on hand-built `TscResult` values; no Node)

**Interfaces:**
- Consumes: `TscResult`, `TscDiagnostic`, `same_file_key` from Task 3; `Claim`, `Verdict`, `make_verdict` from `cca_checks/claim.py`.
- Produces:
  - `DEFINEDNESS_CODES = frozenset({2304, 2552, 2448, 2454})`
  - `NULLABILITY_CODES = frozenset({2531, 2532, 2533, 18047, 18048, 18049, 2722})`
  - `TYPE_CODES = frozenset({2322, 2345, 2339, 2741, 2769, 2353, 2551, 2362, 2363, 2365, 2367, 2349, 2554, 2555})`
  - `ENVIRONMENT_CODES = frozenset({2307, 7016, 2688, 2580, 2582, 2591, 2592, 2593})`, plus every code in 5000 to 5999 via `is_environment(code: int) -> bool`
  - `BLINDNESS_CODES = frozenset({7005, 7006, 7008, 7010, 7011, 7015, 7019, 7031, 7034, 7053, 18046})`
  - `CODES_BY_CLAIM = {"definedness": DEFINEDNESS_CODES, "nullability": NULLABILITY_CODES, "type": TYPE_CODES}`
  - `verdict_for_tsc(claim: Claim, result: TscResult | None, codes: frozenset[int], span: tuple[int, int] | None) -> Verdict`, source `"tsc"` on every verdict that rests on a result, `"llm"` when `result is None`.
  - Code sets replaced by the observed values from Task 2 Step 6 where the compiler disagrees; the sets stay pairwise disjoint.

The order is the spec's "Verdict" section, steps 1 to 9 including 7a. Do not reorder.

- [ ] **Step 1: Write the failing tests** (helper `res(**overrides)` builds a clean `TscResult` that contains the claimed file and nothing else; `diag(code, line, file=CLAIMED)`)

```python
def test_code_sets_are_pairwise_disjoint(): all five sets and the 5xxx range
def test_none_result_is_uncertain_llm(): v = verdict_for_tsc(c, None, NULLABILITY_CODES, (1, 9)); assert (v.verdict, v.source) == ("UNCERTAIN", "llm")
def test_in_set_diag_covering_line_confirms(): diag(18047, start 4, end 5); claim line 5 -> CONFIRMED, source "tsc"
def test_clean_span_refutes(): res() with span (2, 8) -> FALSE_POSITIVE, source "tsc"
@pytest.mark.parametrize("claim_type", ["definedness", "type", "nullability"])
def test_nocheck_blocks_refutation(claim_type): res(nocheck=True) -> UNCERTAIN
def test_directive_in_span_blocks_refutation(): res(directive_lines={4}) span (2, 8) -> UNCERTAIN
def test_directive_does_not_block_confirmation(): directive and an in-set diag at the line -> CONFIRMED
def test_directive_outside_span_does_not_block(): directive_lines={1}, span (5, 9) -> FALSE_POSITIVE
def test_definedness_confirm_blocked_by_2688(): diag(2304 at line) plus unlocated 2688 -> UNCERTAIN
def test_definedness_confirm_survives_2307(): diag(2304 at line) plus diag(2307, 1) -> CONFIRMED
def test_other_code_at_line_is_uncertain(): diag(9999 at line) -> UNCERTAIN naming 9999
def test_no_span_never_refutes(): span None -> UNCERTAIN
def test_in_set_diag_elsewhere_in_span_is_uncertain(): diag(18047, line + 1) -> UNCERTAIN, evidence mentions "off by a line"
def test_environment_diag_in_file_blocks_refutation(): diag(2307, 1) -> UNCERTAIN for each claim type
def test_unlocated_diag_blocks_refutation(): unlocated 2688 -> UNCERTAIN
def test_environment_diag_in_another_file_does_not_block(): diag(2307, 1, file=OTHER) -> FALSE_POSITIVE
def test_subscript_line_blocks_nullability_refutation(): subscript_lines={line} -> UNCERTAIN for nullability, FALSE_POSITIVE for type
@pytest.mark.parametrize("claim_type", ["type", "nullability"])
def test_any_line_in_span_blocks_refutation(claim_type): any_lines={span[0] + 1} -> UNCERTAIN
def test_any_line_does_not_block_definedness(): any_lines in span -> FALSE_POSITIVE for definedness
@pytest.mark.parametrize("claim_type", ["type", "nullability"])
def test_blindness_diag_in_span_blocks_refutation(claim_type): diag(7006, span[0]) -> UNCERTAIN
def test_diag_in_another_file_at_same_line_is_ignored(): diag(18047, line, file=OTHER) -> FALSE_POSITIVE
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_tsc_verdict.py -q`
Expected: ImportError on `verdict_for_tsc` (setup).

- [ ] **Step 3: Implement** `verdict_for_tsc` and the sets. Reuse the shape of `pyright_check.verdict_for_claim` (evidence wording, range covers line) rather than calling it: its diagnostics are pyright dicts keyed by rule name, ours are `TscDiagnostic` keyed by code.

- [ ] **Step 4: Run, expect pass**

Run: `python -m pytest tests/test_tsc_verdict.py -q`
Expected: all pass.

- [ ] **Step 5: Red proof**, one mutation per guard, each restored after:

| Mutation | Test that must fail |
|---|---|
| delete the `nocheck` check | `test_nocheck_blocks_refutation[definedness]` |
| delete the directive-in-span check | `test_directive_in_span_blocks_refutation` |
| treat directive lines file-wide | `test_directive_outside_span_does_not_block` |
| delete the 2688 exception | `test_definedness_confirm_blocked_by_2688` |
| refute when `span is None` | `test_no_span_never_refutes` |
| delete the environment check | `test_environment_diag_in_file_blocks_refutation` |
| delete the subscript check | `test_subscript_line_blocks_nullability_refutation` |
| delete the `any_lines` check | `test_any_line_in_span_blocks_refutation[nullability]` |
| move 7006 out of `BLINDNESS_CODES` | `test_blindness_diag_in_span_blocks_refutation[type]` |

- [ ] **Step 6: Commit**

```bash
git add cca_checks/tsc_check.py tests/test_tsc_verdict.py
git commit -m "Settle TypeScript claims only where the compiler could see the code"
```

---

### Task 5: Register the backend and settle end to end

**Files:**
- Modify: `cca_checks/languages/typescript.py` (add `TypeScriptBackend`)
- Modify: `cca_checks/languages/__init__.py` (`BACKENDS`, `__all__`)
- Modify: `tests/test_language_guard.py:49`, `tests/test_language_registry.py:32`, `tests/test_capabilities.py:32,55` (replace the `app.ts` "uncovered" example with `app.kt`; keep the rest of each test unchanged)
- Modify: `tests/test_language_guard.py` (add a TypeScript case beside `COVERED_RUST`)
- Test: `tests/acceptance/test_typescript_suite.py`, `tests/test_typescript_backend.py`

`tests/test_rust_taint.py:46` and `tests/test_semgrep_check.py:294` use `handler.ts` for taint and stay valid, because the TypeScript backend has no `semgrep_catalog`. Leave them.

**Interfaces:**
- Consumes: `typescript_enclosing_span` (Task 1), `run_tsc`, `CODES_BY_CLAIM`, `verdict_for_tsc` (Tasks 3 and 4), `tool_unavailable_reason`, `typescript_package`.
- Produces: `class TypeScriptBackend` with `name = "typescript"`, `extensions = frozenset({".ts", ".tsx", ".mts", ".cts"})`, `claim_types = frozenset(CODES_BY_CLAIM)`, `enclosing_span(path, line)`, `settle(claim) -> Verdict`, `unavailable_claim_types() -> dict[str, str]`.

`settle`: a file ending `.d.ts`, `.d.mts` or `.d.cts` returns UNCERTAIN, source `"llm"`, evidence naming `skipLibCheck`. Otherwise span via `typescript_enclosing_span` (exception means `None`), `run_tsc(claim.file, span or (claim.line, claim.line))`, then `verdict_for_tsc`. `unavailable_claim_types`: grammar missing is NOT a reason to mark claim types unavailable (confirmation still works without it); `node` unavailable or `typescript_package()` None marks all three with the reason.

- [ ] **Step 1: Write the failing tests**

`tests/test_typescript_backend.py` (no Node; monkeypatched):

```python
def test_registry_resolves_each_extension(): for ext in (".ts", ".tsx", ".mts", ".cts"): assert languages.resolve("a" + ext).name == "typescript"
def test_js_stays_uncovered(): for ext in (".js", ".jsx", ".mjs", ".cjs"): assert languages.resolve("a" + ext) is None
def test_declares_exactly_three_claim_types(): assert TypeScriptBackend().claim_types == {"definedness", "type", "nullability"}
def test_declaration_file_never_refutes(): run_tsc patched to a clean result; settle(claim on "types.d.ts") -> UNCERTAIN
def test_unavailable_when_node_missing(): all three claim types listed with a reason
def test_grammar_missing_does_not_mark_unavailable(): ...
def test_span_failure_never_refutes(): typescript_enclosing_span patched to raise GrammarUnavailable, run_tsc patched to a clean result -> UNCERTAIN
```

`tests/test_language_guard.py`: a `COVERED_TS` case asserting `taint` and `clock_leak` on `app.ts` are never decisive, through the existing parametrized test that covers `COVERED_RUST`.

`tests/acceptance/test_typescript_suite.py`, gated on `shutil.which("node")` and `shutil.which("tsc")`, drives `python -m cca_checks check --claim-type <t> --finding-id X --file <fixture> --line <L>` and parses the JSON verdict:

| Test | Fixture | Claim type | Expected |
|---|---|---|---|
| `test_undefined_name_confirms` | `basic/undefined_name.ts` | definedness | CONFIRMED |
| `test_defined_name_refutes` | `basic/defined_name.ts` | definedness | FALSE_POSITIVE |
| `test_null_access_confirms` / `test_null_guarded_refutes` | `basic/null_access.ts` / `null_guarded.ts` | nullability | CONFIRMED / FALSE_POSITIVE |
| `test_bad_arg_confirms` / `test_good_arg_refutes` | `basic/bad_arg.ts` / `good_arg.ts` | type | CONFIRMED / FALSE_POSITIVE |
| `test_guard_never_refutes[...]` | each of: the three suppressed fixtures, `explicit_any`, `as_any`, `json_parse`, `any_helper`, `ambient_import`, `implicit_any`, `missing_module`, `index_access`, `multiline`, `types_node/uses_process` | the fixture's claim type | not FALSE_POSITIVE |
| `test_directive_in_string_still_refutes` | `basic/directive_in_string.ts` | nullability | FALSE_POSITIVE |
| `test_weak_configs_still_confirm[weak, nocheck_config]` | `*/null_access.ts` | nullability | CONFIRMED |
| `test_solution_config_confirms` | `solution/src/main.ts` | nullability | CONFIRMED |
| `test_process_env_does_not_confirm_definedness` | `types_node/uses_process.ts` | definedness | not CONFIRMED |
| `test_crlf_file_confirms_at_the_same_line` | a CRLF copy of `basic/null_access.ts` written to `tmp_path` with the `basic` tsconfig copied beside it | nullability | CONFIRMED |
| `test_tsx_component_confirms` | `tsx/component.tsx` | nullability | CONFIRMED |
| `test_node_in_audited_root_is_refused` | `tmp_path` containing `node.exe` / `node` and a `.ts` file, cwd there | nullability | UNCERTAIN |

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_typescript_backend.py tests/acceptance/test_typescript_suite.py tests/test_language_guard.py tests/test_language_registry.py tests/test_capabilities.py -q`
Expected: the new tests fail on `resolve(...) is None` or UNCERTAIN verdicts (no backend yet). That is the red run for the acceptance table.

- [ ] **Step 3: Implement** the backend and register it after `RustBackend()` in `BACKENDS`.

- [ ] **Step 4: Run, expect pass**, same command. Expected: all pass, none skipped.

- [ ] **Step 5: Red proof**

Mutations: (a) remove the `.d.ts` check; `test_declaration_file_never_refutes` fails. (b) add `"taint"` to `claim_types`; the `COVERED_TS` guard test fails. (c) in `settle`, replace a failed span with the whole file `(1, 10**9)` instead of `None`; `test_span_failure_never_refutes` fails.

- [ ] **Step 6: Commit**

```bash
git add cca_checks/languages tests/test_typescript_backend.py tests/acceptance/test_typescript_suite.py tests/test_language_guard.py tests/test_language_registry.py tests/test_capabilities.py
git commit -m "Register the TypeScript backend for definedness, type and nullability"
```

---

### Task 6: CI installs Node and the pinned TypeScript, and the wheel ships the probe

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `tests/test_ci_contract.py`

- [ ] **Step 1: Write the failing tests** in `tests/test_ci_contract.py`

```python
# _INSTALLED_BY gains: "node": "actions/setup-node", "tsc": "npm install -g typescript@"
def test_ci_pins_typescript_to_a_supported_major():
    m = re.search(r"npm install -g typescript@(\d+)\.\d+\.\d+", _ci_text())
    assert m and int(m.group(1)) in tsc_check.SUPPORTED_TS_MAJORS
def test_the_packaging_job_checks_the_probe_ships():
    assert "ts_probe.js" in _ci_text()
```

- [ ] **Step 2: Run, expect failure**

Run: `python -m pytest tests/test_ci_contract.py -q`
Expected: `test_every_externally_gated_tool_is_installed_by_ci` fails naming `node` (now gated by Task 2 and 5 tests), plus the two new tests fail. This is the red proof for the contract.

- [ ] **Step 3: Edit `ci.yml`**

In the `test` job, before "Install the deterministic layer": `actions/setup-node` pinned to a commit SHA with a `# vX.Y.Z` comment (get the SHA with `gh api repos/actions/setup-node/git/ref/tags/<tag> --jq .object.sha`, and dereference if it is an annotated tag), `node-version: "22"`, then a step running `npm install -g typescript@<TS_PIN>`, with a comment that the TypeScript tests may not skip here, mirroring the Rust step's comment. In the `package` job's smoke test, assert `importlib.resources.files("cca_checks").joinpath("ts_probe.js").is_file()`.

- [ ] **Step 4: Run, expect pass**

Run: `python -m pytest tests/test_ci_contract.py -q`
Expected: all pass, including `test_every_remote_action_is_pinned_to_a_commit_sha`.

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/ci.yml tests/test_ci_contract.py
git commit -m "Install Node and the pinned TypeScript in CI so the TypeScript tests cannot skip"
```

---

### Task 7: Docs, full suite on VPS2, PR

**Files:**
- Modify: `docs/v3-design.md:176` (TypeScript shipped for three claim types; Go still not), `docs/limits.md`, `docs/configuration.md` (install `typescript` globally, the `typescript` extra, `CCA_TS_TIMEOUT_S`), `CHANGELOG.md` (Unreleased entry)

- [ ] **Step 1: Check VPS2 has Node and the pinned TypeScript**

Run over the existing VPS2 route: `node --version` and `tsc --version`. If either is missing or the major differs, stop and ask the owner before installing anything there.

- [ ] **Step 2: Write the docs.** State the known blind spots plainly: `.d.ts` claims never refute; nullability claims on index-access lines never refute; repositories without installed dependencies can confirm but rarely refute.

- [ ] **Step 3: Full suite on VPS2**

Run: `bash ~/.claude/bin/vps2-pytest`
Expected: the summary line reports 0 failed, and the TypeScript test files report passes, not skips. Read the summary line; exit 0 alone is not a pass after an SSH reset.

- [ ] **Step 4: Lint and type check locally**

Run: `ruff check cca_checks tests` and `python -m pyright`
Expected: no findings.

- [ ] **Step 5: Commit, push, open the PR** (ask the owner before pushing). The PR body lists every red-proof record from Tasks 1 to 6 (node ID, mutation, failure reason) and `TS_PIN`.

```bash
git add docs CHANGELOG.md
git commit -m "Document the TypeScript backend and its blind spots"
```

---

### Task 8 (after merge, owner picks the repository): pre-registered real-repo run

- [ ] **Step 1:** Owner names the repository.
- [ ] **Step 2:** Run `/preregister`: predicted shares of tsc-routed claims that come out CONFIRMED, FALSE_POSITIVE and UNCERTAIN, with and without installed dependencies, and the result that would mean the backend self-disables. Commit it before Step 3.
- [ ] **Step 3:** Hunt-mode run on a checkout with dependencies installed, then on one without. Append the measured result under the prediction; never edit the prediction.
