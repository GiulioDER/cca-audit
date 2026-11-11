"""`TypeScriptBackend` registration and settle wiring; no Node runs here."""

import os

import pytest

from cca_checks import languages, treesitter
from cca_checks.claim import Claim
from cca_checks.languages import typescript as ts_backend
from cca_checks.languages.typescript import TypeScriptBackend
from cca_checks.toolpath import resolve_tool
from cca_checks.tsc_check import TscResult, same_file_key


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

    monkeypatch.setattr(ts_backend, "typescript_enclosing_span", no_span)
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
