import ast
from typing import Optional


def enclosing_span(path: str, line_1based: int) -> tuple[int, int]:
    """1-indexed inclusive line span of the innermost function containing the line.

    Falls back to the whole module when the line sits at module level. Scoped to the
    function because a blindness diagnostic fires on the `def` (the untyped
    parameter), not on the access that dereferences it -- and, for taint, because a
    sink call may sit on a different line than the expression an auditor flagged.
    """
    with open(path, encoding="utf-8") as fh:
        src = fh.read()
    tree = ast.parse(src)
    best: Optional[tuple[int, int]] = None
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            end = getattr(node, "end_lineno", None) or node.lineno
            if node.lineno <= line_1based <= end:
                if best is None or node.lineno > best[0]:
                    best = (node.lineno, end)
    if best is not None:
        return best
    return (1, max(1, src.count("\n") + 1))
