import ast


def enclosing_span(path: str, line_1based: int) -> tuple[int, int]:
    """1-indexed inclusive line span of the innermost function containing the line.

    Falls back to the whole module when the line sits at module level. Scoped to the
    function because a blindness diagnostic fires on the `def` (the untyped
    parameter), not on the access that dereferences it -- and, for taint, because a
    sink call may sit on a different line than the expression an auditor flagged.
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
