"""The parsing layer's own guarantees.

The rule this file protects is the package's central one, applied to the parser: a
parse that could not run must never be indistinguishable from a parse that found
nothing. `parse` therefore raises rather than returning an empty tree, and
`has_error` exists so a caller can tell "I read all of this file" from "I gave up
halfway" before it refutes anything.
"""

import pathlib

import pytest

from cca_checks import treesitter as ts
from cca_checks.languages.rust import FUNCTION_TYPES

tree_sitter = pytest.importorskip("tree_sitter")
pytest.importorskip("tree_sitter_rust")

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "rust" / "src"
CLOCK = str(FIXTURES / "clock.rs")


def span(line):
    return ts.enclosing_span(CLOCK, line, "rust", FUNCTION_TYPES)


def test_span_is_the_enclosing_function():
    """`settle_dead_param` opens at 11 and the body's last line is 13."""
    lo, hi = span(12)
    assert lo == 11
    assert hi >= 13


def test_span_picks_the_innermost_scope():
    """A closure is its own scope. Falling back to the enclosing function would give
    a WIDE span, and a wide span makes a refutation rest on silence across code the
    claim never referred to."""
    outer_lo, _ = span(69)
    inner_lo, _ = span(71)
    assert inner_lo > outer_lo, "the closure's span must beat the function's"


def test_span_falls_back_to_the_whole_file_at_item_level():
    """Line 5 is a `use` declaration, outside every function."""
    lo, hi = span(5)
    assert lo == 1
    assert hi > 60


def test_span_is_1_indexed():
    """tree-sitter's start_point is 0-indexed; semgrep's start.line is 1-indexed and
    pyright's range.start.line is 0-indexed. Every mismatch here is an off-by-one in
    a span that decides whether a refutation is allowed."""
    lo, _ = span(11)
    assert lo == 11


def test_unknown_language_raises_rather_than_returning_an_empty_tree():
    with pytest.raises(ts.GrammarUnavailable):
        ts.parse(CLOCK, "cobol")


def test_missing_grammar_is_reported_as_unavailable(monkeypatch):
    """An uninstalled extra must not look like a file with nothing in it."""
    ts._parser.cache_clear()
    monkeypatch.setitem(ts._GRAMMAR_MODULES, "rust", "tree_sitter_no_such_grammar")
    with pytest.raises(ts.GrammarUnavailable, match="not installed"):
        ts.parse(CLOCK, "rust")
    ts._parser.cache_clear()


def test_a_clean_file_reports_no_parse_error():
    assert ts.has_error(ts.parse(CLOCK, "rust")) is False


def test_a_broken_file_reports_a_parse_error(tmp_path):
    """tree-sitter always returns a tree, inserting ERROR nodes where it gave up. A
    caller that refuted on that tree's silence would be refuting on the parser's
    failure, so `has_error` has to be able to say so."""
    broken = tmp_path / "broken.rs"
    broken.write_text("fn f( { let x = ;;; }", encoding="utf-8")
    assert ts.has_error(ts.parse(str(broken), "rust")) is True


def test_a_bom_does_not_read_as_a_parse_error(tmp_path):
    """Many Windows editors write a UTF-8 BOM. Left in, tree-sitter reports it as an
    ERROR node at offset 0 and every such file becomes permanently unrefutable --
    the same failure `scope.py` avoids with utf-8-sig."""
    withbom = tmp_path / "bom.rs"
    withbom.write_bytes(b"\xef\xbb\xbffn f() -> i64 { 1 }\n")
    assert ts.has_error(ts.parse(str(withbom), "rust")) is False


def test_offsets_survive_non_ascii_source(tmp_path):
    """tree-sitter node positions are BYTE offsets. Reading as str and re-encoding
    puts every line number out by the number of multi-byte characters above it --
    silent, and position-dependent."""
    unicode_src = tmp_path / "u.rs"
    unicode_src.write_text(
        '// caffè — naïve ¡\nfn f(as_of: i64) -> i64 {\n    as_of\n}\n', encoding="utf-8")
    lo, hi = ts.enclosing_span(str(unicode_src), 3, "rust", FUNCTION_TYPES)
    assert (lo, hi) == (2, 4)


def test_walk_is_iterative(tmp_path):
    """A generated or minified source nests deeper than Python's recursion limit, and
    a RecursionError escaping the parser surfaces as a crash mid-audit."""
    deep = tmp_path / "deep.rs"
    depth = 2000
    deep.write_text("fn f() -> i64 {" + "(" * depth + "1" + ")" * depth + "}\n",
                    encoding="utf-8")
    root = ts.parse(str(deep), "rust")
    assert sum(1 for _ in ts.walk(root)) > depth


def test_missing_file_raises_oserror():
    with pytest.raises(OSError):
        ts.parse(str(FIXTURES / "does_not_exist.rs"), "rust")
