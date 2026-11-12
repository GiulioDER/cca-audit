"""`TypeScriptBackend` registration and settle wiring; no Node runs here."""

import dataclasses
import os

import pytest

from cca_checks import languages, treesitter
from cca_checks.claim import Claim
from cca_checks.languages import typescript as ts_backend
from cca_checks.languages.typescript import TypeScriptBackend
from cca_checks.toolpath import resolve_tool
from cca_checks.tsc_check import TscDiagnostic, TscResult, same_file_key


def clean_result(path):
    return TscResult(version="6.0.3", files=frozenset({same_file_key(path)}), diagnostics=(),
                     any_lines=frozenset(), assertion_lines=frozenset(),
                     directive_lines=frozenset(), directive_targets=frozenset(),
                     nocheck=False, subscript_lines=frozenset(),
                     definite_assignment_lines=frozenset(), types_incomplete=False)


@pytest.mark.parametrize("ext", [".ts", ".tsx", ".mts", ".cts"])
def test_registry_resolves_each_extension(ext):
    assert languages.resolve("a" + ext).name == "typescript"


@pytest.mark.parametrize("ext", [".js", ".jsx", ".mjs", ".cjs"])
def test_js_stays_uncovered(ext):
    assert languages.resolve("a" + ext) is None


def test_declares_exactly_three_claim_types():
    assert TypeScriptBackend().claim_types == {"definedness", "type", "nullability"}


def test_declaration_file_never_refutes(monkeypatch):
    """A clean tsc result on a .d.ts proves nothing: skipLibCheck leaves it unchecked."""
    monkeypatch.setattr(ts_backend, "run_tsc", lambda path, span: clean_result(path))
    monkeypatch.setattr(ts_backend, "typescript_enclosing_span", lambda path, line: (1, 5))
    for name in ("types.d.ts", "types.d.mts", "types.d.cts"):
        v = TypeScriptBackend().settle(Claim("F1", name, 3, "nullability"))
        assert v.verdict == "UNCERTAIN"
        assert v.source == "llm"
        assert "skipLibCheck" in v.evidence


def test_unavailable_when_node_missing(monkeypatch):
    monkeypatch.setattr(ts_backend, "tool_unavailable_reason", lambda name: "node not found")
    out = TypeScriptBackend().unavailable_claim_types()
    assert set(out) == {"definedness", "type", "nullability"}
    assert all("node" in reason for reason in out.values())


def test_unavailable_when_typescript_package_missing(monkeypatch):
    monkeypatch.setattr(ts_backend, "tool_unavailable_reason", lambda name: None)
    monkeypatch.setattr(ts_backend, "typescript_package", lambda: None)
    assert set(TypeScriptBackend().unavailable_claim_types()) == {
        "definedness", "type", "nullability"}


def test_grammar_missing_does_not_mark_unavailable(monkeypatch):
    def no_grammar(*args):
        raise treesitter.GrammarUnavailable("grammar missing")

    monkeypatch.setattr(ts_backend, "tool_unavailable_reason", lambda name: None)
    monkeypatch.setattr(ts_backend, "typescript_package", lambda: "/pkg/typescript")
    monkeypatch.setattr(ts_backend, "unsupported_typescript", lambda package: None)
    monkeypatch.setattr(treesitter, "_parser", no_grammar)
    assert TypeScriptBackend().unavailable_claim_types() == {}


def test_span_failure_never_refutes(monkeypatch):
    def no_span(path, line):
        raise treesitter.GrammarUnavailable("grammar missing")

    monkeypatch.setattr(ts_backend, "typescript_spans", no_span)
    monkeypatch.setattr(ts_backend, "run_tsc", lambda path, span: clean_result(path))
    v = TypeScriptBackend().settle(Claim("F1", "src/app.ts", 3, "nullability"))
    assert v.verdict == "UNCERTAIN"


def test_unavailable_when_typescript_major_unsupported(monkeypatch, tmp_path):
    """TypeScript 7 ships a package.json but no createProgram, so every claim would
    escalate while capabilities reported TypeScript available."""
    (tmp_path / "package.json").write_text('{"version": "7.0.0"}')
    monkeypatch.setattr(ts_backend, "tool_unavailable_reason", lambda name: None)
    monkeypatch.setattr(ts_backend, "typescript_package", lambda: str(tmp_path))
    out = TypeScriptBackend().unavailable_claim_types()
    assert set(out) == {"definedness", "type", "nullability"}
    assert all("7.0.0" in reason for reason in out.values())


def test_node_in_audited_root_is_refused(tmp_path, monkeypatch):
    """A `node` planted in the audited root, executable and first on PATH, is never
    returned. Needs neither node nor tsc: only the resolver runs, nothing executes.
    Moved from tests/acceptance, whose module skip made it skip without them."""
    for name in ("node", "node.exe"):
        plant = tmp_path / name
        plant.write_text("not a real node")
        plant.chmod(0o755)
    monkeypatch.setenv("PATH", str(tmp_path) + os.pathsep + os.environ.get("PATH", ""))
    monkeypatch.chdir(tmp_path)
    found = resolve_tool("node")
    assert found is None or os.path.dirname(os.path.realpath(found)) != os.path.realpath(tmp_path)


# L4 fixes, settled through `settle` with a real tree-sitter parse and a hand-built
# compiler result, so no Node runs. Each proof was run red against the pre-fix
# languages/typescript.py and failed on its verdict assertion before the fix.


def settle_source(monkeypatch, tmp_path, source, line, claim_type="nullability",
                  diagnostics=(), **fields):
    pytest.importorskip("tree_sitter_typescript")
    p = tmp_path / "a.ts"
    p.write_bytes(source.encode("utf-8"))
    key = same_file_key(str(p))
    located = tuple(TscDiagnostic(code, f"message {code}", key, at, at)
                    for code, at in diagnostics)
    monkeypatch.setattr(ts_backend, "run_tsc", lambda path, span: dataclasses.replace(
        clean_result(path), diagnostics=located, **fields))
    return TypeScriptBackend().settle(Claim("F1", str(p), line, claim_type))


GUARDLESS = "type U = { name: string };\nfunction f(u: U | null) {\n  return u.name;\n}\n"


@pytest.mark.parametrize("source, line", [
    ("type U = { name: string };\nconst s = `abc\nfunction f(u: U | null) {\n"
     "  return u.name;\n}\n", 4),
    ("type U = { name: string };\n/* unterminated\nfunction f(u: U | null) {\n"
     "  return u.name;\n}\n", 4),
    ("type U = { name: string };\n<<<<<<< HEAD\nfunction f(u: U) {\n  return u.name;\n}\n"
     "=======\nfunction f(u: U | null) {\n  return u.name;\n}\n>>>>>>> b\n", 8),
], ids=["template_before", "comment_before", "conflict_theirs"])
def test_file_that_does_not_parse_never_refutes(monkeypatch, tmp_path, source, line):
    """FIX-001 (BUG-001): tree-sitter recovered from a syntax error, so the span it
    gives is a guess about code it did not read as written. Red: FALSE_POSITIVE."""
    v = settle_source(monkeypatch, tmp_path, source, line)
    assert v.verdict == "UNCERTAIN"


def test_file_that_parses_still_refutes(monkeypatch, tmp_path):
    """Control: the same function without the syntax error is refuted on a clean result."""
    assert settle_source(monkeypatch, tmp_path, GUARDLESS, 3).verdict == "FALSE_POSITIVE"


OVERLOADED = ('declare function on(e: "a", cb: (x: string) => number): void;\n'
              'declare function on(e: "b", cb: (x: number) => number): void;\n'
              "export function f() {\n"
              "{directive}"
              '  on("a",\n'
              "    (x) => {\n"
              "      return x;\n"
              "    });\n"
              "}\n")


def test_overload_error_on_the_call_line_blocks_refutation(monkeypatch, tmp_path):
    """FIX-012 (STAKES-003): tsc reports the callback's error as TS2769 on the call line
    (4), outside the callback's span (5-7). Red: FALSE_POSITIVE."""
    v = settle_source(monkeypatch, tmp_path, OVERLOADED.replace("{directive}", ""), 6,
                      "type", diagnostics=[(2769, 4)])
    assert v.verdict == "UNCERTAIN" and "off by a line" in v.evidence


def test_directive_above_the_call_blocks_refutation(monkeypatch, tmp_path):
    """FIX-012: `// @ts-ignore` on line 4 suppresses the call on line 5, outside the
    callback's span (6-8). Red: FALSE_POSITIVE."""
    v = settle_source(monkeypatch, tmp_path, OVERLOADED.replace("{directive}", "  // @ts-ignore\n"),
                      7, "type", directive_lines=frozenset({4}),
                      directive_targets=frozenset({5}))
    assert v.verdict == "UNCERTAIN" and "@ts-ignore" in v.evidence


def test_wide_span_does_not_widen_the_blindness_scans(monkeypatch, tmp_path):
    """Control: an `any` on the call line is outside the callback, so it does not make
    the callback's own types unseen; only the off by a line and directive checks widen."""
    v = settle_source(monkeypatch, tmp_path, OVERLOADED.replace("{directive}", ""), 6,
                      "type", any_lines=frozenset({4}), assertion_lines=frozenset({4}))
    assert v.verdict == "FALSE_POSITIVE"
