"""TypeScript function spans, from tree-sitter.

The backend class arrives in a later task; this module holds only the span code.
Grammar attributes in the installed tree-sitter-typescript 0.23 are
`language_typescript` and `language_tsx` (see `treesitter._GRAMMAR_MODULES`).
"""

from .. import treesitter as ts

#: Node types that open a function scope. Arrow functions and methods are included
#: because most TypeScript is written with them, and a missing type would fall back
#: to the whole file, which is the wide answer.
FUNCTION_TYPES = frozenset({
    "function_declaration", "function_expression", "arrow_function",
    "method_definition", "generator_function_declaration", "generator_function",
})


def grammar_for(path: str) -> str:
    """`.tsx` needs the tsx grammar: plain TypeScript parses `<div>` as a cast."""
    return "tsx" if path.lower().endswith(".tsx") else "typescript"


def typescript_enclosing_span(path: str, line_1based: int) -> tuple[int, int]:
    return ts.enclosing_span(path, line_1based, grammar_for(path), FUNCTION_TYPES)
