import ast
import json
import os
import subprocess
import tempfile
from typing import Optional

from .claim import Claim, Verdict, make_verdict

# --- Rule sets -------------------------------------------------------------
# A claim type maps to exactly one interpretation of a pyright diagnostic, so
# these three sets must stay pairwise disjoint (enforced by a test).

DEFINEDNESS_RULES = frozenset({
    "reportUndefinedVariable",
    "reportUnboundVariable",
    "reportMissingImports",
})

NULLABILITY_RULES = frozenset({
    "reportOptionalMemberAccess",
    "reportOptionalSubscript",
    "reportOptionalCall",
    "reportOptionalIterable",
    "reportOptionalOperand",
    "reportOptionalContextManager",
})

# reportGeneralTypeIssues is the pre-split legacy name. Keeping it means an older
# pyright still CONFIRMS. Rule-set drift may cost us a confirmation; it must never
# buy us a refutation.
TYPE_RULES = frozenset({
    "reportArgumentType",
    "reportAssignmentType",
    "reportReturnType",
    "reportCallIssue",
    "reportIndexIssue",
    "reportOperatorIssue",
    "reportAttributeAccessIssue",
    "reportRedeclaration",
    "reportIncompatibleMethodOverride",
    "reportIncompatibleVariableOverride",
    "reportGeneralTypeIssues",
})

# Strict-mode rules that mean "pyright has no type information here".
BLINDNESS_RULES = frozenset({
    "reportMissingParameterType",
    "reportUnknownParameterType",
    "reportUnknownMemberType",
    "reportUnknownVariableType",
    "reportUnknownArgumentType",
    "reportMissingTypeStubs",
})

# Claim types whose *refutation* is only valid if pyright actually had types.
# definedness is annotation-independent: pyright resolves a name whether or not
# anything in the file is typed, so its silence is real evidence.
TYPE_DEPENDENT_CLAIMS = frozenset({"type", "nullability"})

RULES_BY_CLAIM = {
    "definedness": DEFINEDNESS_RULES,
    "nullability": NULLABILITY_RULES,
    "type": TYPE_RULES,
}

REFUTE_LABEL = {
    "definedness": "undefined-symbol",
    "nullability": "optional-access",
    "type": "type",
}


# --- pyright invocation -----------------------------------------------------

def run_pyright(path: str) -> Optional[list[dict]]:
    try:
        proc = subprocess.run(["pyright", "--outputjson", path], capture_output=True, text=True)
    except FileNotFoundError:
        # pyright binary not on PATH: distinct "tool unavailable" signal (None),
        # NOT an empty list -- an empty list means "pyright ran and found nothing".
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    return data.get("generalDiagnostics", [])


def _diags_at(diags: list[dict], line_1based: int) -> list[dict]:
    """All diagnostics on a line. pyright's range.start.line is 0-indexed."""
    out = []
    for d in diags:
        start = (d.get("range") or {}).get("start") or {}
        if start.get("line", -1) + 1 == line_1based:
            out.append(d)
    return out


def _diag_at(diags: list[dict], line_1based: int, rules: frozenset) -> Optional[dict]:
    for d in _diags_at(diags, line_1based):
        if d.get("rule") in rules:
            return d
    return None


# --- The blindness probe ----------------------------------------------------
# definedness is annotation-independent. type/nullability are not: given
# `def charge(user):`, `user.card.token` produces no diagnostic -- not because the
# access is safe, but because pyright knows nothing about `user`. Refuting there
# would silently trade a false positive for a false negative. So before refuting a
# type-dependent claim we ask pyright, in strict mode, whether it could see at all.

def enclosing_span(path: str, line_1based: int) -> tuple[int, int]:
    """1-indexed inclusive line span of the innermost function containing the line.

    Falls back to the whole module when the line sits at module level. Scoped to the
    function because the blindness diagnostic fires on the `def` (the untyped
    parameter), not on the access that dereferences it -- and scoped to the function
    rather than the file because file scope would escalate everything.
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


def run_pyright_strict(path: str) -> Optional[list[dict]]:
    """pyright over `path` in strict mode. None on any failure (assume blind).

    Strict is the only mode that emits the blindness rules, and pyright has no CLI
    flag for typeCheckingMode -- hence the generated temporary project config.
    """
    abs_path = os.path.abspath(path)
    try:
        with tempfile.TemporaryDirectory() as td:
            cfg = os.path.join(td, "pyrightconfig.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"include": [abs_path], "typeCheckingMode": "strict"}, fh)
            proc = subprocess.run(["pyright", "--project", td, "--outputjson"],
                                  capture_output=True, text=True)
    except (FileNotFoundError, OSError):
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    return data.get("generalDiagnostics", [])


def pyright_is_blind_at(path: str, line_1based: int) -> bool:
    """True if pyright has no type information in the claim's enclosing scope.

    Returns True on ANY failure. A probe that could not run must never license a
    refutation.
    """
    try:
        lo, hi = enclosing_span(path, line_1based)
        diags = run_pyright_strict(path)
    except Exception:
        return True
    if diags is None:
        return True
    for d in diags:
        if d.get("rule") in BLINDNESS_RULES:
            start = (d.get("range") or {}).get("start") or {}
            line = start.get("line", -1) + 1
            if lo <= line <= hi:
                return True
    return False


# --- Verdict ----------------------------------------------------------------

def verdict_for_claim(
    claim: Claim,
    diags: Optional[list[dict]],
    rules: frozenset,
    blind_probe=None,
) -> Verdict:
    """Settle a claim against pyright's diagnostics. Three-way, never false-refutes.

    For type-dependent claims a refutation is only issued when the blindness probe
    confirms pyright actually had type information in the enclosing scope.
    """
    if diags is None:
        # tool unavailable: never conflate with "pyright ran and was silent" (FALSE_POSITIVE)
        return make_verdict(claim.finding_id, "UNCERTAIN",
                            "pyright unavailable; falling back to LLM", "llm")

    hit = _diag_at(diags, claim.line, rules)
    if hit:
        ev = f"pyright {hit['rule']} @ {claim.file}:{claim.line}: {hit['message']}"
        return make_verdict(claim.finding_id, "CONFIRMED", ev, "pyright")

    at_line = _diags_at(diags, claim.line)
    if at_line:
        # pyright sees something here, just not under a rule we recognise. A renamed
        # rule must not be read as "no bug" -- escalate instead of refuting.
        seen = ", ".join(sorted({str(d.get("rule")) for d in at_line}))
        ev = (f"pyright reported {len(at_line)} diagnostic(s) @ {claim.file}:{claim.line} "
              f"but none in the expected rule set (saw: {seen}); escalated")
        return make_verdict(claim.finding_id, "UNCERTAIN", ev, "pyright")

    if claim.claim_type in TYPE_DEPENDENT_CLAIMS:
        probe = blind_probe or pyright_is_blind_at
        if probe(claim.file, claim.line):
            ev = (f"pyright has no type information in the enclosing scope "
                  f"@ {claim.file}:{claim.line}; escalated")
            return make_verdict(claim.finding_id, "UNCERTAIN", ev, "pyright")

    label = REFUTE_LABEL.get(claim.claim_type, claim.claim_type)
    ev = f"pyright: no {label} diagnostic @ {claim.file}:{claim.line}"
    return make_verdict(claim.finding_id, "FALSE_POSITIVE", ev, "pyright")


def verdict_for_definedness(claim: Claim, diags: Optional[list[dict]]) -> Verdict:
    return verdict_for_claim(claim, diags, DEFINEDNESS_RULES)
