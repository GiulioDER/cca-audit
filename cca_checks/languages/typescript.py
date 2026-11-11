"""TypeScript function spans, from tree-sitter.

The backend settles definedness, type and nullability from the compiler's own
diagnostics (`tsc_check`); the span code here scopes a refutation to one function.
Grammar attributes in the installed tree-sitter-typescript 0.23 are
`language_typescript` and `language_tsx` (see `treesitter._GRAMMAR_MODULES`).
"""

from .. import treesitter as ts
from ..claim import Claim, Verdict, make_verdict
from ..toolpath import tool_unavailable_reason
from ..tsc_check import (
    CODES_BY_CLAIM,
    run_tsc,
    typescript_package,
    unsupported_typescript,
    verdict_for_tsc,
)

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


_DECLARATION_SUFFIXES = (".d.ts", ".d.mts", ".d.cts")


class TypeScriptBackend:
    name = "typescript"
    extensions = frozenset({".ts", ".tsx", ".mts", ".cts"})
    claim_types = frozenset(CODES_BY_CLAIM)

    def enclosing_span(self, path: str, line_1based: int) -> tuple[int, int]:
        return typescript_enclosing_span(path, line_1based)

    def unavailable_claim_types(self) -> dict[str, str]:
        """All three claim types, with the reason, when the compiler cannot run.

        A missing grammar is deliberately not a reason: it costs only the span, and
        without a span the verdict can still confirm but never refute.
        """
        reason = tool_unavailable_reason("node")
        if reason is None:
            package = typescript_package()
            reason = ("no trusted global typescript package" if package is None
                      else unsupported_typescript(package))
        return dict.fromkeys(self.claim_types, reason) if reason else {}

    def settle(self, claim: Claim) -> Verdict:
        if claim.file.lower().endswith(_DECLARATION_SUFFIXES):
            # Declaration files are not checked under skipLibCheck, so tsc's silence
            # about one proves nothing.
            return make_verdict(claim.finding_id, "UNCERTAIN",
                                f"{claim.file} is a declaration file, which skipLibCheck "
                                f"leaves unchecked; escalated", "llm")
        try:
            span = typescript_enclosing_span(claim.file, claim.line)
        except Exception:
            span = None
        result = run_tsc(claim.file, span or (claim.line, claim.line))
        return verdict_for_tsc(claim, result, CODES_BY_CLAIM[claim.claim_type], span)
