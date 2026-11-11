import pytest

from cca_checks import treesitter as ts
from cca_checks.languages.typescript import grammar_for, typescript_enclosing_span
from cca_checks.treesitter import GrammarUnavailable

pytest.importorskip("tree_sitter_typescript")


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
    # No trailing newline: the root node would otherwise end on the empty line after it.
    p.write_text("const a = 1;\nconst b = a;")
    assert typescript_enclosing_span(str(p), 2) == (1, 2)


def test_missing_grammar_raises(monkeypatch, tmp_path):
    p = tmp_path / "a.ts"
    p.write_text("const a = 1;\n")
    ts._parser.cache_clear()
    monkeypatch.setitem(ts._GRAMMAR_MODULES, "typescript", ("tree_sitter_no_such_grammar", "language"))
    try:
        with pytest.raises(GrammarUnavailable):
            typescript_enclosing_span(str(p), 1)
    finally:
        ts._parser.cache_clear()
