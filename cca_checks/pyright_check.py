import json
import os
import subprocess
import tempfile
from typing import Optional

from .claim import Claim, Verdict, make_verdict
from .scope import enclosing_span

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
    """pyright over `path` in normal mode.

    None means "could not tell -- escalate": pyright is missing, crashed, timed
    out, produced unparseable output, or (per `summary.filesAnalyzed`) never
    actually analyzed the file. A `list` (possibly empty) means pyright
    genuinely ran and reported those diagnostics -- an empty list is "ran
    clean, genuinely silent", not "we couldn't tell". Mirrors
    `run_pyright_strict`'s fail-safe cascade so the two stay consistent: any
    failure mode that would otherwise read as false "ran clean" must escalate
    instead, because that silence is what licenses a FALSE_POSITIVE.
    """
    try:
        proc = subprocess.run(
            ["pyright", "--outputjson", os.path.abspath(path)],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
        )
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        # covers FileNotFoundError (pyright binary not on PATH) and any other OS-level
        # failure launching the subprocess.
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    # Guard summary access: must be a dict to safely call .get()
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return None
    files_analyzed = summary.get("filesAnalyzed")
    # Guard filesAnalyzed comparison: must be int to compare with int
    if not isinstance(files_analyzed, int) or files_analyzed < 1:
        # pyright ran but analyzed nothing -- e.g. an unreadable file. This is
        # "could not tell", not "ran clean".
        return None
    diags = data.get("generalDiagnostics", [])
    return diags if isinstance(diags, list) else None


def _diags_at(diags: list[dict], line_1based: int) -> list[dict]:
    """All diagnostics on a line. pyright's range.start.line is 0-indexed."""
    out = []
    for d in diags:
        if not isinstance(d, dict):
            continue
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

def run_pyright_strict(path: str) -> Optional[list[dict]]:
    """pyright over `path` in strict mode. None on any failure (assume blind).

    Strict is the only mode that emits the blindness rules, and pyright has no CLI
    flag for typeCheckingMode -- hence the generated temporary project config.

    The file is passed to pyright *positionally*, not via the config's `include`.
    pyright treats `include` entries as glob patterns, so an absolute path containing
    a glob metacharacter (`[`, `]`, `*`, `?`) would match a different path or nothing,
    causing pyright to silently analyze zero files and report a clean, empty
    diagnostics list. A positional file argument is a literal path and overrides the
    config's include list, while the config still supplies typeCheckingMode. We then
    double-check pyright's own `summary.filesAnalyzed` before trusting silence --
    zero files analyzed must read as "could not tell" (None), never as "ran clean"
    ([]); conflating the two is exactly the false-refutation hole this closes.
    """
    abs_path = os.path.abspath(path)
    try:
        with tempfile.TemporaryDirectory() as td:
            cfg = os.path.join(td, "pyrightconfig.json")
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({"typeCheckingMode": "strict"}, fh)
            proc = subprocess.run(
                ["pyright", "--project", td, "--outputjson", abs_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120,
            )
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        # covers FileNotFoundError (pyright binary not on PATH) and any other OS-level
        # failure launching the subprocess or writing the temp config.
        return None
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return None
    # Guard summary access: must be a dict to safely call .get()
    summary = data.get("summary")
    if not isinstance(summary, dict):
        return None
    files_analyzed = summary.get("filesAnalyzed")
    # Guard filesAnalyzed comparison: must be int to compare with int
    if not isinstance(files_analyzed, int) or files_analyzed < 1:
        # pyright ran but analyzed nothing -- e.g. include/glob mismatch, or an
        # unreadable file. This is "could not tell", not "ran clean".
        return None
    diags = data.get("generalDiagnostics", [])
    return diags if isinstance(diags, list) else None


def pyright_is_blind_at(path: str, line_1based: int) -> bool:
    """True if pyright has no type information in the claim's enclosing scope.

    Returns True on ANY failure. A probe that could not run must never license a
    refutation. The entire body lives inside one try/except: a diagnostics list
    containing a malformed (non-dict) entry must escalate to blind, not raise past
    this function and into the pipeline.
    """
    try:
        lo, hi = enclosing_span(path, line_1based)
        diags = run_pyright_strict(path)
        if diags is None:
            return True
        for d in diags:
            if d.get("rule") in BLINDNESS_RULES:
                start = (d.get("range") or {}).get("start")
                if not start or "line" not in start:
                    # A blindness-rule diagnostic whose line we can't determine is
                    # unsafe to treat as "outside the span" -- assume blind.
                    return True
                line = start["line"] + 1
                if lo <= line <= hi:
                    return True
        return False
    except Exception:
        return True


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
        ev = f"pyright {hit.get('rule')} @ {claim.file}:{claim.line}: {hit.get('message', '')}"
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
