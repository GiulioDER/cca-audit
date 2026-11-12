"""The TypeScript type probe (`cca_checks/ts_probe.js`), driven directly.

Each test sends the probe one request, the way `tsc_check.run_tsc` will, and asserts
on the raw response. The soundness claims live here: a repo config cannot weaken the
check, an excluded file is still analysed, `any` is reported wherever it reaches the
span, and a suppression comment is found by the compiler's own tokens, never by a
text search that a string literal can fool.

Red proof for every behaviour test, per the owner's 2026-09-08 rule, is recorded in
`.superpowers/sdd/2026-11-11-typescript-tsc-backend/task-2-report.md` (node ID,
mutation of `ts_probe.js`, failure reason), and for the tests added by the final
review's fixes in `final-fix-report.md` beside it.
"""

import functools
import json
import os
import pathlib
import re
import shutil
import subprocess
import tempfile

import pytest

from cca_checks.tsc_check import COPY_OPTIONS, FORCED_OPTIONS

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures" / "typescript"
PROBE = pathlib.Path(__file__).resolve().parent.parent / "cca_checks" / "ts_probe.js"
TS_MAJOR = "6"

NULLABILITY = {18047, 2531}

pytestmark = pytest.mark.skipif(
    shutil.which("node") is None or shutil.which("tsc") is None,
    reason="node or a global typescript is not installed; the CI job installs both",
)


@functools.cache
def _typescript_dir() -> str:
    npm = shutil.which("npm")
    assert npm is not None, "tsc is on PATH but npm is not"
    out = subprocess.run([npm, "root", "-g"], capture_output=True, text=True,
                         check=True, timeout=120)
    return os.path.join(out.stdout.strip(), "typescript")


def _nearest_tsconfig(path: pathlib.Path) -> str | None:
    for directory in path.parents:
        candidate = directory / "tsconfig.json"
        if candidate.is_file():
            return str(candidate)
        if directory == FIXTURES:
            return None
    return None


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def request_for(fixture_rel: str, span: tuple[int, int]) -> dict:
    path = FIXTURES / fixture_rel
    return {
        "protocol": 1,
        "typescript": _typescript_dir(),
        "tsconfig": _nearest_tsconfig(path),
        "file": str(path),
        "span": list(span),
        "copy_options": list(COPY_OPTIONS),
        "forced_options": FORCED_OPTIONS,
    }


def run_probe(request: dict) -> subprocess.CompletedProcess:
    return subprocess.run(
        [shutil.which("node"), str(PROBE)], input=json.dumps(request),
        capture_output=True, text=True, encoding="utf-8", timeout=300,
        cwd=tempfile.gettempdir())


def probe(fixture_rel: str, line: int, span: tuple[int, int] | None = None) -> dict:
    """Probe one fixture; the span is the claim line alone unless one is given."""
    done = run_probe(request_for(fixture_rel, span or (line, line)))
    assert done.returncode == 0, done.stderr
    assert done.stderr == ""
    return json.loads(done.stdout)


def codes_at(result: dict, fixture_rel: str, line: int) -> set[int]:
    want = _key(str(FIXTURES / fixture_rel))
    return {d["code"] for d in result["diagnostics"]
            if d["file"] is not None and _key(d["file"]) == want
            and d["start_line"] <= line <= d["end_line"]}


def all_codes(result: dict) -> set[int]:
    return {d["code"] for d in result["diagnostics"]}


def in_program(result: dict, fixture_rel: str) -> bool:
    want = _key(str(FIXTURES / fixture_rel))
    return any(_key(f) == want for f in result["files"])


def test_reports_version_and_program_files():
    r = probe("basic/null_access.ts", 3)
    assert r["protocol"] == 1
    assert r["version"].split(".")[0] == TS_MAJOR
    assert in_program(r, "basic/null_access.ts")


def test_undefined_name_has_2304():
    assert 2304 in codes_at(probe("basic/undefined_name.ts", 2), "basic/undefined_name.ts", 2)


def test_null_access_has_a_nullability_code():
    assert codes_at(probe("basic/null_access.ts", 3), "basic/null_access.ts", 3) & NULLABILITY


def test_bad_arg_has_2345():
    assert 2345 in codes_at(probe("basic/bad_arg.ts", 5), "basic/bad_arg.ts", 5)


def test_weak_config_is_overridden():
    """`strict: false` in the repo must not switch strictNullChecks off."""
    assert codes_at(probe("weak/null_access.ts", 3), "weak/null_access.ts", 3) & NULLABILITY


def test_nocheck_config_is_overridden():
    """`noCheck: true` in the repo would silence every semantic diagnostic."""
    r = probe("nocheck_config/null_access.ts", 3)
    assert codes_at(r, "nocheck_config/null_access.ts", 3) & NULLABILITY


def test_dropped_option_is_not_applied():
    """weak/ sets `noImplicitAny: false`, which is off the allow-list: 7006 stays."""
    assert 7006 in codes_at(probe("weak/implicit.ts", 1), "weak/implicit.ts", 1)


def test_unforced_option_off_the_allow_list_is_dropped():
    """noImplicitAny is also forced, so the test above cannot see the allow-list
    alone. weak/ sets `noUncheckedIndexedAccess: true`, which nothing forces back;
    applied, it would report `a[i].x` as possibly undefined (2532) at sites the
    repo's own code may guard, which the design rules out."""
    r = probe("weak/index_access.ts", 3)
    assert codes_at(r, "weak/index_access.ts", 3) == set()


def test_excluded_file_is_forced_into_the_program():
    done = run_probe(request_for("excluded/skipped.ts", (1, 1)))
    # The probe refuses a claimed file that is not in the program, so say why.
    assert done.returncode == 0, done.stderr
    assert in_program(json.loads(done.stdout), "excluded/skipped.ts")


def test_solution_config_follows_the_owning_reference():
    """`paths` lives in tsconfig.app.json; without it `@lib/util` is a 2307."""
    r = probe("solution/src/main.ts", 4)
    assert 2307 not in all_codes(r)
    assert codes_at(r, "solution/src/main.ts", 4) & NULLABILITY


def test_solution_config_without_an_owning_reference_exits_2():
    done = run_probe(request_for("solution/stray.ts", (1, 1)))
    assert done.returncode == 2
    assert done.stdout == ""
    assert "0 project references own" in done.stderr


def test_config_that_does_not_parse_exits_2():
    done = run_probe(request_for("broken/a.ts", (1, 1)))
    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr.count("\n") == 1
    # The compiler's own JSON error, not an internal crash on the way to it.
    assert "tsconfig.json: TS1" in done.stderr


def test_json_parse_line_is_an_any_line():
    assert 2 in probe("basic/json_parse.ts", 2)["any_lines"]


def test_any_helper_line_is_an_any_line():
    assert 3 in probe("basic/any_helper.ts", 3)["any_lines"]


def test_ambient_module_line_is_an_any_line():
    assert 3 in probe("basic/ambient_import.ts", 3)["any_lines"]


def test_as_any_line_is_an_any_line():
    assert 3 in probe("basic/as_any.ts", 3)["any_lines"]


def test_explicit_any_line_is_an_any_line():
    assert 2 in probe("basic/explicit_any.ts", 2)["any_lines"]


def test_typed_access_has_no_any_line():
    assert probe("basic/null_guarded.ts", 4, span=(2, 5))["any_lines"] == []


def test_directives_are_reported():
    want = {"lines": [3], "targets": [4], "nocheck": False}
    assert probe("basic/suppressed_ignore.ts", 4)["directives"] == want
    assert probe("basic/suppressed_expect.ts", 4)["directives"] == want
    assert probe("basic/suppressed_nocheck.ts", 4)["directives"]["nocheck"] is True


def test_trailing_directive_is_reported():
    """`const k = ""; // @ts-ignore` suppresses the next line (tsc 6.0.3 exits 0 on
    this fixture), and the comment is trailing trivia of `;`, not leading trivia."""
    directives = probe("basic/directive_trailing.ts", 4)["directives"]
    assert directives["lines"] == [3, 7]
    assert directives["targets"] == [4, 8]


def test_directive_target_walks_over_blank_and_comment_lines():
    """tsc looks for a directive above a diagnostic across blank and `//` lines, so
    the `// @ts-ignore` on line 3 suppresses `u.name` on line 6 (tsc exits 0)."""
    directives = probe("basic/directive_walkup.ts", 6)["directives"]
    assert directives["lines"] == [3]
    assert directives["targets"] == [6]


def test_directive_inside_a_string_is_not_reported():
    assert probe("basic/directive_in_string.ts", 5)["directives"]["lines"] == []


def test_directive_forms_the_compiler_honours_are_reported():
    """TypeScript 6.0.3 suppresses under `/// @ts-ignore`, `/* @ts-ignore */` and
    `// @ts-ignored`; a `^//\\s*@ts-ignore\\b` match would miss all three."""
    assert probe("basic/suppressed_variants.ts", 4)["directives"]["lines"] == [3, 7, 11]


def test_directive_after_a_regex_literal_is_reported():
    """A text scanner reads the backtick in `/`/` as a template and swallows the
    rest of the file, directive included; the parser knows it is a regex."""
    assert probe("basic/directive_after_regex.ts", 5)["directives"]["lines"] == [4]


def test_index_access_line_is_reported():
    assert 3 in probe("basic/index_access.ts", 3)["subscript_lines"]


def test_index_signature_property_read_is_a_subscript_line():
    """`m.alice` on a Record reads undefined like `m["alice"]`."""
    assert 3 in probe("basic/index_signature.ts", 3)["subscript_lines"]


def test_declared_property_is_not_a_subscript_line():
    assert probe("basic/declared_property.ts", 3)["subscript_lines"] == []


def test_index_access_spread_over_lines_reports_every_line():
    """`a[i]` on line 3 is dereferenced by `.x` on line 4; a claim may cite either."""
    assert probe("basic/index_multiline.ts", 4, span=(2, 5))["subscript_lines"] == [3, 4]


def test_cast_inside_a_call_argument_is_an_any_line():
    """`takesNumber(u as any)` reports nothing; only the cast itself is `any`."""
    r = probe("basic/as_any_arg.ts", 5)
    assert codes_at(r, "basic/as_any_arg.ts", 5) == set()
    assert 5 in r["any_lines"]


def test_type_names_and_module_specifiers_are_not_any_lines():
    """The checker gives `const` in `as const` and the string in `from "./helper"`
    the error type; neither carries a value, so neither marks its line blind."""
    assert probe("basic/double_cast.ts", 5, span=(1, 7))["any_lines"] == []
    assert probe("basic/any_helper.ts", 3, span=(1, 4))["any_lines"] == [3]


def test_non_null_assertion_is_an_assertion_line():
    assert 3 in probe("basic/non_null.ts", 3)["assertion_lines"]


def test_double_cast_is_an_assertion_line_and_as_const_is_not():
    r = probe("basic/double_cast.ts", 5, span=(1, 7))
    assert 5 in r["assertion_lines"]
    assert 7 not in r["assertion_lines"]


@pytest.mark.parametrize("fixture_rel, span, line", [
    ("destructure/array_binding.ts", (2, 5), 3),
    ("destructure/array_assign.ts", (2, 6), 4),
    ("destructure/record_binding.ts", (2, 5), 3),
    ("destructure/object_assign.ts", (2, 6), 4),
    ("destructure/rest_tuple.ts", (2, 5), 3),
], ids=["array_binding", "array_assign", "record_binding", "object_assign", "rest_tuple"])
def test_destructuring_by_index_is_a_subscript_line(fixture_rel, span, line):
    """`const [u] = items`, `[u] = items`, `const { alice } = m` on a Record and a
    position in a tuple's rest element read like `items[0]` and `m["alice"]`; under
    noUncheckedIndexedAccess tsc reports each use as possibly undefined."""
    assert line in probe(fixture_rel, line, span=span)["subscript_lines"]


def test_tuple_and_declared_destructuring_is_not_a_subscript_line():
    """A `useState` pair, the pairs of `Object.entries` and a declared property are
    checked members; marking them would block refutation in most React code."""
    assert probe("destructure/declared_binding.ts", 8, span=(3, 10))["subscript_lines"] == []


def test_index_read_outside_the_span_marks_the_use():
    """`const first = items[0]` at module level; `first.name` in the function."""
    assert probe("destructure/outer_index.ts", 5, span=(4, 6))["subscript_lines"] == [5]


@pytest.mark.parametrize("fixture_rel", ["outside_span/outer_bang.ts",
                                         "outside_span/outer_cast.ts"])
def test_assertion_outside_the_span_marks_the_use(fixture_rel):
    """`const app = find("app")!` at module level makes `app.text` in the function
    look safe; without the `!` tsc reports 18047 there (the control below)."""
    assert probe(fixture_rel, 4, span=(3, 5))["assertion_lines"] == [4]


def test_unasserted_declaration_outside_the_span_is_checked():
    r = probe("outside_span/outer_bang_ctl.ts", 4, span=(3, 5))
    assert codes_at(r, "outside_span/outer_bang_ctl.ts", 4) == {18047}
    assert r["assertion_lines"] == []


def test_definite_assignment_is_reported():
    """`let n!: number` silences TS2454 at `n + 1`; the control without `!` has it."""
    assert probe("outside_span/definite.ts", 3, span=(1, 4))["definite_assignment_lines"] == [2, 3]
    r = probe("outside_span/definite_ctl.ts", 3, span=(1, 4))
    assert codes_at(r, "outside_span/definite_ctl.ts", 3) == {2454}
    assert r["definite_assignment_lines"] == []


def test_module_detection_is_copied():
    """Under the repo's `moduleDetection: "force"` a.ts is a module, so `helper` is
    undefined in b.ts; dropped, both files are global scripts and it resolves."""
    assert 2304 in codes_at(probe("module_detection/b.ts", 2), "module_detection/b.ts", 2)


@pytest.mark.parametrize("fixture_rel", ["ts_extensions/main.ts", "rewrite_extensions/main.ts"])
def test_ts_extension_imports_are_copied(fixture_rel):
    """`import { h } from "./h.ts"` is a TS5097 in the claimed file unless the repo's
    allowImportingTsExtensions or rewriteRelativeImportExtensions is applied."""
    assert probe(fixture_rel, 3, span=(2, 4))["diagnostics"] == []


def test_import_member_codes_sit_on_the_import_line():
    """Observation for IMPORT_MEMBER_CODES, not a guard: the use sites report nothing."""
    r = probe("import_member/use.ts", 3, span=(2, 4))
    assert codes_at(r, "import_member/use.ts", 1) == {2305}
    assert codes_at(r, "import_member/use_default.ts", 1) == {1192}
    assert codes_at(r, "import_member/use.ts", 3) == set()


def test_declaration_file_target_exits_2():
    """skipLibCheck is forced, so a .d.ts would report nothing whatever it holds."""
    done = run_probe(request_for("basic/decl_target.d.ts", (1, 1)))
    assert done.returncode == 2
    assert done.stdout == ""
    assert "declaration file" in done.stderr


def test_unlocated_diagnostic_has_null_file():
    r = probe("types_node/uses_process.ts", 2)
    assert any(d["code"] == 2688 and d["file"] is None for d in r["diagnostics"])


def test_deprecated_repo_options_add_no_diagnostic():
    """baseUrl, node10 and es5 are unlocated TS5101/TS5107 errors in TypeScript 6,
    which would read as environment facts and block every refutation in the repo."""
    assert probe("deprecated/clean.ts", 2)["diagnostics"] == []


def test_unset_types_includes_every_at_types_package():
    """TypeScript 6.0.3 defaults `types` to [], so a global from node_modules/@types
    would be a 2304 that the repo's own TypeScript 5 build never reported. With no
    `types` in the repo config the probe asks for `["*"]`, the TypeScript 5 default."""
    assert probe("autotypes/uses_global.ts", 2)["diagnostics"] == []


def test_no_repo_config_still_includes_vendored_types():
    """With no tsconfig at all the probe has no `types` to copy, so TypeScript 6 would
    default it to [] and read a vendored global as an undefined name. Same default as
    for a config that leaves `types` unset."""
    r = probe("noconfig/uses_global.ts", 2)
    assert r["diagnostics"] == []


def test_explicit_empty_types_is_honoured():
    """`types: []` is the repo's own choice: the vendored global stays out of scope."""
    r = probe("types_empty/uses_global.ts", 2)
    assert 2304 in codes_at(r, "types_empty/uses_global.ts", 2)


def test_listed_types_resolve_from_the_config_directory():
    """`types: ["fakeglobal"]` resolves under the config's node_modules/@types, not
    under the probe's working directory, which is outside the audited tree."""
    r = probe("types_vendored/uses_global.ts", 2)
    assert r["diagnostics"] == []


def test_jsx_tag_adds_no_diagnostic():
    """A type query on the `div` tag would add a 2304 that no tsc run reports."""
    assert all_codes(probe("tsx/component.tsx", 3, span=(2, 4))) == {18047}


def test_intrinsic_jsx_tag_is_not_an_any_line():
    assert probe("tsx/component.tsx", 3, span=(2, 4))["any_lines"] == []


@pytest.mark.parametrize("request_body", [
    {"protocol": 1},
    "typescript missing",
], ids=["fields_missing", "typescript_missing"])
def test_bad_request_exits_2(request_body, tmp_path):
    if request_body == "typescript missing":
        request_body = dict(request_for("basic/null_access.ts", (1, 4)),
                            typescript=str(tmp_path / "no-typescript-here"))
    done = run_probe(request_body)
    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr.count("\n") == 1


# L4 fixes, settled end to end: the real probe and compiler, through
# TypeScriptBackend.settle, on files written to tmp_path. Each proof test was run red
# against the pre-fix production files and failed on its verdict assertion before the
# fix. The controls beside them show each guard leaves ordinary code refutable or
# confirmable.


def settle_text(tmp_path, text, line, claim_type="nullability", files=None):
    pytest.importorskip("tree_sitter_typescript")
    from cca_checks.claim import Claim
    from cca_checks.languages.typescript import TypeScriptBackend

    # A .git stops the config and @types walks at tmp_path, away from the machine's own.
    (tmp_path / ".git").mkdir(exist_ok=True)
    for name, body in (files or {}).items():
        (tmp_path / name).write_text(body, encoding="utf-8")
    p = tmp_path / "a.ts"
    p.write_bytes(text.encode("utf-8"))
    return TypeScriptBackend().settle(Claim("F1", str(p), line, claim_type)).verdict


GUARDLESS = "type U = { name: string };\nfunction f(u: U | null) {\n  return u.name;\n}\n"


@pytest.mark.parametrize("text, line", [
    ("type U = { name: string };\nconst s = `abc\nfunction f(u: U | null) {\n"
     "  return u.name;\n}\n", 4),
    ("type U = { name: string };\n/* unterminated\nfunction f(u: U | null) {\n"
     "  return u.name;\n}\n", 4),
    ("type U = { name: string };\n<<<<<<< HEAD\nfunction f(u: U) {\n  return u.name;\n}\n"
     "=======\nfunction f(u: U | null) {\n  return u.name;\n}\n>>>>>>> b\n", 8),
], ids=["template_before", "comment_before", "conflict_theirs"])
def test_syntax_error_never_refutes(tmp_path, text, line):
    """FIX-001 (BUG-001): the defect is real, but the parser did not read the file as
    written (TS1160, TS1010, TS1185). Red: FALSE_POSITIVE."""
    assert settle_text(tmp_path, text, line) == "UNCERTAIN"


def test_syntax_control_confirms(tmp_path):
    assert settle_text(tmp_path, GUARDLESS, 3) == "CONFIRMED"


LINE_BREAKS = [
    "/* comment \u2028 here */\nconst f = (u: U | null) => u.name;\ntype U = { name: string };\n",
    "// a\rconst g = 1;\nconst f = (u: U | null) => u.name;\ntype U = { name: string };\n",
    "// a\r\r\nconst f = (u: U | null) => u.name;\r\ntype U = { name: string };\r\n",
]


@pytest.mark.parametrize("text", LINE_BREAKS, ids=["u2028", "lone_cr", "cr_crlf"])
def test_line_break_only_tsc_counts_never_refutes(tmp_path, text):
    """FIX-002 (BUG-002): TypeScript counts a lone CR and U+2028 as line breaks, so its
    TS18047 lands on line 3 while the claim and the span say line 2. Red: FALSE_POSITIVE."""
    assert settle_text(tmp_path, text, 2) == "UNCERTAIN"


@pytest.mark.parametrize("text", LINE_BREAKS, ids=["u2028", "lone_cr", "cr_crlf"])
def test_probe_refuses_a_line_break_it_would_miscount(tmp_path, text):
    p = tmp_path / "a.ts"
    p.write_bytes(text.encode("utf-8"))
    done = run_probe(dict(request_for("basic/null_access.ts", (2, 2)), file=str(p),
                          tsconfig=None))
    assert done.returncode == 2
    assert done.stdout == ""
    assert done.stderr.count("\n") == 1


def test_line_break_control_confirms(tmp_path):
    text = "// comment here\nconst f = (u: U | null) => u.name;\ntype U = { name: string };\n"
    assert settle_text(tmp_path, text, 2) == "CONFIRMED"


@pytest.mark.parametrize("text, line, claim_type", [
    ("type P = { a: string; b: string; c: string };\nfunction f() {\n"
     "  const p: P = { a: 'x' };\n  return p;\n}\n", 4, "type"),
    ("function f(g: (() => void) | null) {\n  const h = 1;\n  g();\n}\n", 2, "nullability"),
], ids=["ts2739", "ts2721"])
def test_sibling_code_a_line_away_never_refutes(tmp_path, text, line, claim_type):
    """FIX-003 (STAKES-001): TS2739 and TS2721 are the claimed defect under a code the
    sets did not list, one line from the claim. Red: FALSE_POSITIVE."""
    assert settle_text(tmp_path, text, line, claim_type) == "UNCERTAIN"


FIELD = "type U = { name: string };\nclass C {\n  {field}\n  get() {\n    return this.u.name;\n  }\n}\n"


@pytest.mark.parametrize("field", ["u: U;", "declare u: U;"], ids=["uninitialized", "declare"])
def test_field_without_initializer_never_refutes(tmp_path, field):
    """FIX-004 (STAKES-004): `u: U;` and `declare u: U;` leave `this.u` undefined at run
    time while tsc types it U, as `u!: U` does. Red: FALSE_POSITIVE."""
    assert settle_text(tmp_path, FIELD.replace("{field}", field), 5) == "UNCERTAIN"


def test_initialized_field_still_refutes(tmp_path):
    text = FIELD.replace("{field}", "u: U = { name: 'x' };")
    assert settle_text(tmp_path, text, 5) == "FALSE_POSITIVE"


def test_property_initialization_code_in_another_file_blocks_refutation(tmp_path):
    """FIX-004, repair cycles 2 and 3: the never-assigned field is declared in another
    program file than the function that reads it; its TS2564 sits on the field's name
    there. Red: FALSE_POSITIVE."""
    files = {"model.ts": "export type U = { name: string };\nexport class M {\n  u: U;\n}\n",
             "tsconfig.json": '{ "include": ["*.ts"] }\n'}
    text = 'import { M } from "./model";\nexport function f(m: M) {\n  return m.u.name;\n}\n'
    assert settle_text(tmp_path, text, 3, files=files) == "UNCERTAIN"


def test_assigned_field_beside_a_never_assigned_one_still_refutes(tmp_path):
    """Negative control (repair cycle 3): `a: A;` is never assigned, but the claim reads
    `this.b`, which the constructor assigns; only the reads of `a` are marked."""
    text = ("type A = { x: number };\ntype B = { x: number };\nclass C {\n  a: A;\n  b: B;\n"
            "  constructor() { this.b = { x: 1 }; }\n  get() {\n    return this.b.x;\n  }\n}\n")
    assert settle_text(tmp_path, text, 8) == "FALSE_POSITIVE"


CTOR_FIELD = ("type U = { name: string };\nclass C {\n  u: U;\n"
              "  constructor() { this.u = { name: 'x' }; }\n  get() {\n"
              "    return this.u.name;\n  }\n}\n")


@pytest.mark.parametrize("claim_type", ["nullability", "type", "definedness"])
def test_constructor_assigned_field_still_refutes(tmp_path, claim_type):
    """Negative control (repair cycle 2): strictPropertyInitialization is forced, so tsc
    proves a field assigned in the constructor; marking it cost correct refutations."""
    assert settle_text(tmp_path, CTOR_FIELD, 6, claim_type) == "FALSE_POSITIVE"


def test_field_typed_with_undefined_still_refutes(tmp_path):
    """Negative control: `n: number | undefined` is checked at every read."""
    text = ("class C {\n  n: number | undefined;\n  get() {\n"
            "    return this.n === undefined ? 0 : this.n + 1;\n  }\n}\n")
    assert settle_text(tmp_path, text, 4) == "FALSE_POSITIVE"


def test_declaration_file_class_field_still_refutes(tmp_path):
    """Negative control: a field of a class in a .d.ts is the environment's word."""
    files = {"req.d.ts": "declare class Req { url: string }\n",
             "tsconfig.json": '{ "include": ["*.ts"] }\n'}
    text = "export function f(r: Req) {\n  return r.url.length;\n}\n"
    assert settle_text(tmp_path, text, 2, files=files) == "FALSE_POSITIVE"


@pytest.mark.parametrize("text, line", [
    ("interface State { user: { name: string } }\ndeclare function load(): unknown;\n"
     "const { user } = load() as State;\nexport function g() {\n  return user.name;\n}\n", 5),
    ("declare const m: { [k: string]: { a: { name: string } } };\nconst { a } = m[\"k\"];\n"
     "export function g() {\n  return a.name;\n}\n", 4),
], ids=["asserted", "indexed"])
def test_destructured_from_an_assertion_or_index_never_refutes(tmp_path, text, line):
    """FIX-005 (BUG-004): the binding element's own initializer is empty; the cast or
    index read sits on the variable declaration above it. Red: FALSE_POSITIVE."""
    assert settle_text(tmp_path, text, line) == "UNCERTAIN"


def test_destructured_from_a_typed_value_still_refutes(tmp_path):
    text = ("interface State { user: { name: string } }\ndeclare function load(): State;\n"
            "const { user } = load();\nexport function g() {\n  return user.name;\n}\n")
    assert settle_text(tmp_path, text, 5) == "FALSE_POSITIVE"


OVERLOADED = ('declare function on(e: "a", cb: (x: string) => number): void;\n'
              'declare function on(e: "b", cb: (x: number) => number): void;\n'
              "export function f() {\n"
              "{directive}"
              '  on("a",\n'
              "    (x) => {\n"
              "      return {ret};\n"
              "    });\n"
              "}\n")


@pytest.mark.parametrize("directive, line", [("", 6), ("  // @ts-ignore\n", 7)],
                         ids=["ts2769_on_call", "ts_ignore_above_call"])
def test_overloaded_callback_error_never_refutes(tmp_path, directive, line):
    """FIX-012 (STAKES-003): the callback returns a string where a number is due; tsc
    reports TS2769 on the call line, and a @ts-ignore above the call hides it, both
    outside the callback's span. Red: FALSE_POSITIVE."""
    text = OVERLOADED.replace("{directive}", directive).replace("{ret}", "x")
    assert settle_text(tmp_path, text, line, "type") == "UNCERTAIN"


def test_overloaded_callback_control_refutes(tmp_path):
    text = OVERLOADED.replace("{directive}", "").replace("{ret}", "x.length")
    assert settle_text(tmp_path, text, 6, "type") == "FALSE_POSITIVE"


OUTER_LET = ("interface Cfg { port: number }\nlet cfg: Cfg{init};\nexport function init() {\n"
             "  cfg = { port: 1 };\n}\nexport function get() {\n  return cfg.port;\n}\n")
TDZ = "{before}export function start() {\n  return limit * 2;\n}\nstart();\n{after}"


@pytest.mark.parametrize("text, line, claim_type", [
    (OUTER_LET.replace("{init}", ""), 7, "definedness"),
    (OUTER_LET.replace("{init}", ""), 7, "nullability"),
    (TDZ.replace("{before}", "").replace("{after}", "const limit = 10;\n"), 2, "definedness"),
], ids=["outer_let_definedness", "outer_let_nullability", "tdz_const"])
def test_outer_unassigned_or_later_declaration_never_refutes(tmp_path, text, line, claim_type):
    """FIX-013 (STAKES-005): tsc does not follow assignment across functions, so a
    module `let cfg: Cfg;` read in get(), or a `const` declared after the function that
    is called before it, reports nothing. Red: FALSE_POSITIVE."""
    assert settle_text(tmp_path, text, line, claim_type) == "UNCERTAIN"


@pytest.mark.parametrize("text, line, claim_type", [
    (OUTER_LET.replace("{init}", " = { port: 0 }"), 7, "definedness"),
    (OUTER_LET.replace("{init}", " = { port: 0 }"), 7, "nullability"),
    (TDZ.replace("{before}", "const limit = 10;\n").replace("{after}", ""), 3, "definedness"),
], ids=["initialized_let_definedness", "initialized_let_nullability", "const_before"])
def test_outer_initialized_or_earlier_declaration_still_refutes(tmp_path, text, line,
                                                                 claim_type):
    assert settle_text(tmp_path, text, line, claim_type) == "FALSE_POSITIVE"


def test_outer_let_typed_with_undefined_still_refutes(tmp_path):
    """Negative control (repair cycle 2): `let cur: N | undefined;` is checked for
    nullability at every read, so only the `let cfg: Cfg;` shape is marked."""
    text = ("interface N { v: number }\nlet cur: N | undefined;\n"
            "export function set(n: N) { cur = n; }\nexport function get() {\n"
            "  if (cur) return cur.v;\n}\n")
    assert settle_text(tmp_path, text, 5) == "FALSE_POSITIVE"


def test_every_install_type_definitions_code_is_listed():
    """FIX-014 (STAKES-006): every "Do you need to install type definitions" message in
    the installed TypeScript is in INSTALL_TYPES_CODES, so a new one cannot let a
    missing type package confirm an undefined name. Red: 2867 and 2868 were missing."""
    from cca_checks.tsc_check import INSTALL_TYPES_CODES

    lib = pathlib.Path(_typescript_dir()) / "lib" / "typescript.js"
    found = {int(code) for code in re.findall(
        r'diag\((\d+), \d+ /\* Error \*/, "[^"]*", "[^"]*Do you need to install type '
        r'definitions', lib.read_text(encoding="utf-8"))}
    # A scan that finds nothing would pass vacuously; 2580 is the node form.
    assert 2580 in found
    assert found <= INSTALL_TYPES_CODES, sorted(found - INSTALL_TYPES_CODES)


REFLECT = 'export function meta(t: object) {\n  return Reflect.getMetadata("k", t);\n}\n'


def test_unresolved_side_effect_import_withholds_type_confirm(tmp_path):
    """FIX-015 (STAKES-007): `import "./missing"` is a TS2882, and the global it would
    augment reads as a TS2339 on `Reflect.getMetadata`. Red: CONFIRMED."""
    assert settle_text(tmp_path, 'import "./missing";\n' + REFLECT, 3, "type") == "UNCERTAIN"


def test_module_error_on_a_side_effect_import_line_withholds_type_confirm(tmp_path):
    """FIX-015: a TS2307 on a line holding a side-effect import withholds the confirm,
    through the probe's side_effect_import_lines. Red: CONFIRMED."""
    text = 'import "./ok"; import { x } from "missing-pkg";\n' + REFLECT
    assert settle_text(tmp_path, text, 3, "type", files={"ok.ts": "export {};\n"}) == "UNCERTAIN"


MAIN_CONFIG = ('{"compilerOptions": {"target": "es2022", "module": "esnext", '
               '"moduleResolution": "bundler", "strict": true}, "include": ["*.ts"]}')
USES_REFLECT = 'export function m(t: object) {\n  return Reflect.getMetadata("k", t);\n}\n'


def test_unresolved_package_side_effect_import_in_another_file_withholds_confirm(tmp_path):
    """FIX-015, L6 REVISE: main.ts holds `import "reflect-metadata";` (a TS2882 there),
    which would augment the global `Reflect` that a.ts reads, so a.ts's TS2339 may be
    the machine's. Red: CONFIRMED."""
    files = {"main.ts": 'import "reflect-metadata";\nimport "./a";\n',
             "tsconfig.json": MAIN_CONFIG}
    assert settle_text(tmp_path, USES_REFLECT, 2, "type", files=files) == "UNCERTAIN"


def test_unresolved_relative_side_effect_import_in_another_file_still_confirms(tmp_path):
    """Control: `import "./app.css";` unresolved in main.ts augments no global, so it does
    not withhold confirmations in other files."""
    files = {"main.ts": 'import "./app.css";\nimport "./a";\n', "tsconfig.json": MAIN_CONFIG}
    assert settle_text(tmp_path, USES_REFLECT, 2, "type", files=files) == "CONFIRMED"


def test_ordinary_unresolved_import_still_confirms(tmp_path):
    """Control, the owner's settled decision: an unresolved ordinary import does not
    withhold a type confirm elsewhere in the file."""
    text = 'import { x } from "missing-pkg";\n' + REFLECT
    assert settle_text(tmp_path, text, 3, "type") == "CONFIRMED"


PREDICATE = ("interface User { name: string }\n"
             "function isUser(u: unknown): u is User { return true; }\n"
             "function assertUser(u: unknown): asserts u is User {}\n"
             "export function f(u: User | null) {\n  if (isUser(u)) return u.name;\n}\n"
             "export function g(u: User | null) {\n  assertUser(u);\n  return u.name;\n}\n"
             "export function h(u: User | null) {\n  if (u) return u.name;\n}\n")


@pytest.mark.parametrize("line", [5, 9], ids=["type_guard", "assertion_function"])
def test_user_type_predicate_never_refutes(tmp_path, line):
    """FIX-017 (STAKES-010): `u is User` and `asserts u is User` narrow on the author's
    word, which here is wrong. Red: FALSE_POSITIVE."""
    assert settle_text(tmp_path, PREDICATE, line) == "UNCERTAIN"


def test_plain_guard_still_refutes(tmp_path):
    assert settle_text(tmp_path, PREDICATE, 12) == "FALSE_POSITIVE"
