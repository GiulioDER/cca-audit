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
