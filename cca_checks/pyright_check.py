import json
import os
import subprocess
import tempfile

from .claim import Claim, Verdict, make_verdict
from .config import TIMEOUT_S
from .scope import enclosing_span
from .toolpath import resolve_tool

# --- Rule sets -------------------------------------------------------------
# A claim type maps to exactly one interpretation of a pyright diagnostic, so
# these three sets must stay pairwise disjoint (enforced by a test).

# reportMissingImports is deliberately NOT here. It reports an *environment*
# fact -- pyright could not resolve the import from its configured search paths --
# not a fact about the symbol being undefined. A file doing
# `sys.path.insert(0, ...)` then importing from it runs fine yet draws the
# diagnostic, as do editable installs, namespace packages and stub-only deps. Since
# `definedness` is exempt from the blindness probe (see TYPE_DEPENDENT_CLAIMS), a
# rule in this set is decisive with no backstop, and confirming a hallucinated
# "undefined symbol" claim on an environment artifact is exactly the failure this
# package exists to prevent. Dropping it costs a confirmation and gains nothing
# unsound: the diagnostic still lands in `_diags_at`, so it escalates to UNCERTAIN.
DEFINEDNESS_RULES = frozenset({
    "reportUndefinedVariable",
    "reportUnboundVariable",
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

def run_pyright(path: str) -> list[dict] | None:
    """pyright over `path` in normal mode.

    None means "could not tell -- escalate": pyright is missing, crashed, timed
    out, produced unparseable output, or (per `summary.filesAnalyzed`) never
    actually analyzed the file. A `list` (possibly empty) means pyright
    genuinely ran and reported those diagnostics -- an empty list is "ran
    clean, genuinely silent", not "we couldn't tell". Mirrors
    `run_pyright_strict`'s fail-safe cascade so the two stay consistent: any
    failure mode that would otherwise read as false "ran clean" must escalate
    instead, because that silence is what licenses a FALSE_POSITIVE.

    The analysis configuration is generated here, never inherited. Left to
    auto-discovery, pyright reads the *audited repo's* `pyrightconfig.json` /
    `[tool.pyright]`, so that repo could set `typeCheckingMode: "off"` (or
    suppress an individual rule) and convert our silence into a FALSE_POSITIVE
    against itself. `enableTypeIgnoreComments: false` closes the same hole at
    comment granularity -- a `# type: ignore` would otherwise silence both this
    pass and the strict blindness probe, so the probe would report "not blind"
    and license the refutation. The auditor must control the configuration that
    its refutations rest on; the audited repo must not.
    """
    return _run_pyright_with_config(path, {
        # pyright's own CLI default, pinned so it cannot be lowered by the target.
        "typeCheckingMode": "standard",
        "enableTypeIgnoreComments": False,
    })


def _run_pyright_with_config(path: str, config: dict) -> list[dict] | None:
    """Run pyright over one file under a generated project config.

    Shared by the normal and strict passes so their fail-safe cascades cannot
    drift: every failure mode that would otherwise read as a false "ran clean"
    returns None (escalate) rather than [] (genuinely silent).

    The file is passed to pyright *positionally*, not via the config's `include`.
    pyright treats `include` entries as glob patterns, so an absolute path
    containing a glob metacharacter (`[`, `]`, `*`, `?`) would match a different
    path or nothing, causing pyright to silently analyze zero files and report a
    clean, empty diagnostics list. A positional file argument is a literal path and
    overrides the config's include list, while the config still supplies the
    analysis settings.
    """
    exe = resolve_tool("pyright")
    if exe is None:
        # Missing from PATH, or resolved inside the audited tree (hijack attempt).
        return None
    abs_path = os.path.abspath(path)
    try:
        with tempfile.TemporaryDirectory() as td:
            cfg = os.path.join(td, "pyrightconfig.json")
            # `--project <tempdir>` would otherwise make that empty temp directory
            # pyright's execution-environment root, breaking import resolution for
            # the file under analysis -- `from settings import X` next to the file
            # would draw a spurious reportMissingImports. Pin the root back to the
            # working directory (which is what pyright infers with no --project) and
            # add the file's own directory to the search path, so pinning the
            # analysis SETTINGS does not also change import SEMANTICS.
            search = [os.getcwd(), os.path.dirname(abs_path)]
            with open(cfg, "w", encoding="utf-8") as fh:
                json.dump({**config,
                           "extraPaths": search,
                           "executionEnvironments": [
                               {"root": os.getcwd(), "extraPaths": search}]}, fh)
            proc = subprocess.run(
                [exe, "--project", td, "--outputjson", abs_path],
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=TIMEOUT_S,
            )
    except subprocess.TimeoutExpired:
        return None
    except OSError:
        # covers any OS-level failure launching the subprocess or writing the temp config.
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


def _line_bounds(d: dict) -> tuple[int, int] | None:
    """1-based (first, last) line a diagnostic covers, or None if unreadable.

    pyright's range.start.line / range.end.line are 0-indexed. Every field is
    untrusted: `range` may be missing or a list, `start`/`end` may be non-dicts,
    and `line` may be None or a string. None means "this diagnostic's location
    cannot be determined" -- which the caller must escalate on, never silently
    drop (see `_unlocatable_in_rules`).
    """
    rng = d.get("range")
    if not isinstance(rng, dict):
        return None
    start = rng.get("start")
    if not isinstance(start, dict):
        return None
    first = start.get("line")
    # bool is an int subclass; a JSON `true` here is malformed, not line 1.
    if not isinstance(first, int) or isinstance(first, bool):
        return None
    end = rng.get("end")
    last = end.get("line") if isinstance(end, dict) else None
    if not isinstance(last, int) or isinstance(last, bool) or last < first:
        last = first
    return first + 1, last + 1


def _diags_at(diags: list[dict], line_1based: int) -> list[dict]:
    """All diagnostics whose range COVERS the given line.

    Kept line-exact-ish on purpose: a diagnostic's own [start, end] extent is the
    most this may match. Widening the CONFIRM side further -- e.g. to the enclosing
    function -- would let a hallucinated line inside a long wrapped call collect a
    binding CONFIRMED, which is the failure this package exists to prevent. The
    asymmetry is deliberate: matching may confirm, only span-wide SILENCE may
    refute (see `_diags_in_span`).
    """
    out = []
    for d in diags:
        if not isinstance(d, dict):
            continue
        bounds = _line_bounds(d)
        if bounds is None:
            continue
        if bounds[0] <= line_1based <= bounds[1]:
            out.append(d)
    return out


def _diags_in_span(diags: list[dict], lo: int, hi: int, rules: frozenset) -> list[dict]:
    """In-rule-set diagnostics anywhere within [lo, hi], 1-indexed inclusive."""
    out = []
    for d in diags:
        if not isinstance(d, dict) or d.get("rule") not in rules:
            continue
        bounds = _line_bounds(d)
        if bounds is None:
            continue
        if bounds[0] <= hi and bounds[1] >= lo:
            out.append(d)
    return out


def _unlocatable_in_rules(diags: list[dict], rules: frozenset) -> list[dict]:
    """In-rule-set diagnostics whose location could not be determined.

    These must not fall through to a refutation. A diagnostic that positively
    exists under a rule we care about, but whose line we cannot read, is evidence
    that something may be wrong here -- the inverse of "pyright was silent". This
    mirrors the escalate-don't-drop policy `hits_in_span` applies in
    semgrep_check and `pyright_is_blind_at` applies to the probe.
    """
    return [d for d in diags
            if isinstance(d, dict) and d.get("rule") in rules and _line_bounds(d) is None]


def _diag_at(diags: list[dict], line_1based: int, rules: frozenset) -> dict | None:
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

def run_pyright_strict(path: str) -> list[dict] | None:
    """pyright over `path` in strict mode. None on any failure (assume blind).

    Strict is the only mode that emits the blindness rules, and pyright has no CLI
    flag for typeCheckingMode -- hence the generated temporary project config.

    The file is passed positionally and `summary.filesAnalyzed` is double-checked
    before silence is trusted -- see `_run_pyright_with_config`, which both passes
    share so their fail-safe cascades cannot drift.

    `enableTypeIgnoreComments: false` matters most here: pyright honours
    `# type: ignore` / `# pyright: ignore` by default, and a suppressed file yields
    neither a claim diagnostic nor a blindness diagnostic -- so the probe would
    report "not blind" and license a refutation on a file it was never allowed to
    see. Disabling suppression is what makes a not-blind answer mean anything.
    """
    return _run_pyright_with_config(path, {
        "typeCheckingMode": "strict",
        "enableTypeIgnoreComments": False,
    })


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
    diags: list[dict] | None,
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

    unlocatable = _unlocatable_in_rules(diags, rules)
    if unlocatable:
        # A diagnostic under a rule we care about positively exists, but we cannot
        # place it. Refuting here would drop real evidence on a parsing failure.
        seen = ", ".join(sorted({str(d.get("rule")) for d in unlocatable}))
        ev = (f"pyright reported {len(unlocatable)} diagnostic(s) in the expected rule set "
              f"(saw: {seen}) whose location could not be determined; escalated")
        return make_verdict(claim.finding_id, "UNCERTAIN", ev, "pyright")

    # Only span-wide silence may refute. pyright anchors a diagnostic at the start
    # of the offending expression, while an auditor cites the line carrying the
    # specific access; for a multi-line statement the two differ by a line or two,
    # and a line-exact miss would otherwise become a confident FALSE_POSITIVE about
    # a defect that is really there. Escalating instead costs a refutation, which
    # is the affordable direction. (scope.enclosing_span exists for exactly this
    # off-by-N reasoning -- it was applied to the blindness probe but not here.)
    try:
        lo, hi = enclosing_span(claim.file, claim.line)
    except Exception:
        lo = hi = None
    if lo is not None:
        nearby = _diags_in_span(diags, lo, hi, rules)
        if nearby:
            where = ", ".join(sorted({f"{_line_bounds(d)[0]}" for d in nearby}))
            ev = (f"pyright reported no {REFUTE_LABEL.get(claim.claim_type, claim.claim_type)} "
                  f"diagnostic exactly at {claim.file}:{claim.line}, but did report one in the "
                  f"enclosing scope (lines {lo}-{hi}, at {where}) -- the claim may be off by a "
                  f"line rather than false; escalated")
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


def verdict_for_definedness(claim: Claim, diags: list[dict] | None) -> Verdict:
    return verdict_for_claim(claim, diags, DEFINEDNESS_RULES)
