"""Enclosing-scope resolution, dispatched to the backend that owns the file's language.

`pyright_check` and `semgrep_check` both ask for the enclosing span before deciding
whether a tool's silence covers the claim's neighbourhood. Neither should know which
parser answered -- Python's `ast`, Rust's tree-sitter grammar, or a future one -- so
this module keeps the name they import and routes on extension.

The Python implementation lives here rather than in the backend because moving it
would have made the language layer and the parser change in the same commit, and the
existing suite is the only proof that introducing the layer changed no behaviour.
"""

import ast


def enclosing_span(path: str, line_1based: int) -> tuple[int, int]:
    """1-indexed inclusive line span of the innermost function containing the line.

    Raises `LookupError` when no backend covers the file. Callers already wrap this
    in a bare `except` and escalate, which is the correct handling: a span we cannot
    compute must NOT silently widen to the whole file, because a whole-file span
    makes a refutation rest on silence across code the claim never referred to.
    """
    # Imported inside the function, not at module scope, to keep the dependency
    # one-directional -- the same reason `substrate.py` defers its `properties`
    # import. `languages.python` imports THIS module at its module scope, so a
    # top-level `from . import languages` here closes the cycle and every import of
    # `cca_checks.scope` fails with a partially-initialised module.
    from . import languages

    backend = languages.resolve(path)
    if backend is None:
        raise LookupError(
            f"no deterministic backend covers {path!r}; its enclosing scope cannot "
            f"be determined")
    return backend.enclosing_span(path, line_1based)


def python_enclosing_span(path: str, line_1based: int) -> tuple[int, int]:
    """The Python implementation. Falls back to the whole module at module level.

    Scoped to the function because a blindness diagnostic fires on the `def` (the
    untyped parameter), not on the access that dereferences it -- and, for taint,
    because a sink call may sit on a different line than the expression an auditor
    flagged.
    """
    # utf-8-sig, not utf-8: a leading BOM is legal in Python source (and is what
    # many Windows editors write), but a plain utf-8 read leaves U+FEFF in the text
    # and ast.parse raises SyntaxError on it. That failure is caught upstream and
    # degrades every scope-dependent check on the file to UNCERTAIN, so the whole
    # deterministic layer goes dark on a file it should have handled.
    with open(path, encoding="utf-8-sig") as fh:
        src = fh.read()
    tree = ast.parse(src)
    best: tuple[int, int] | None = None
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            end = getattr(node, "end_lineno", None) or node.lineno
            if node.lineno <= line_1based <= end:
                if best is None or node.lineno > best[0]:
                    best = (node.lineno, end)
    if best is not None:
        return best
    return (1, max(1, src.count("\n") + 1))
