import json
import subprocess
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


# --- Verdict ----------------------------------------------------------------

def verdict_for_claim(
    claim: Claim,
    diags: Optional[list[dict]],
    rules: frozenset,
    blind_probe=None,
) -> Verdict:
    """Settle a claim against pyright's diagnostics. Three-way, never false-refutes.

    blind_probe is injected in Task 2; until then a refutation is unconditional.
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

    label = REFUTE_LABEL.get(claim.claim_type, claim.claim_type)
    ev = f"pyright: no {label} diagnostic @ {claim.file}:{claim.line}"
    return make_verdict(claim.finding_id, "FALSE_POSITIVE", ev, "pyright")


def verdict_for_definedness(claim: Claim, diags: Optional[list[dict]]) -> Verdict:
    return verdict_for_claim(claim, diags, DEFINEDNESS_RULES)
