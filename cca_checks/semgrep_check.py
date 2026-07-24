import json
import os
import subprocess
from importlib import resources

from .claim import Claim, Verdict, make_verdict
from .config import TIMEOUT_S
from .scope import enclosing_span
from .toolpath import resolve_tool

SUPPORTED_SINK_CLASSES = frozenset({"sql", "command", "code_exec", "path"})

#: A rule id that is NOT a sink. It must match any file of its language that contains
#: a function, and it exists so that "semgrep matched nothing" can be told apart from
#: "semgrep could not read this file".
#:
#: WHY THIS IS NEEDED AT ALL, AND WHY IT IS OPTIONAL. `run_semgrep` already checks
#: `paths.scanned`, which proves semgrep OPENED the file -- but not that its parser
#: understood it. On a language whose grammar support is younger than Python's, a
#: file semgrep failed to parse is scanned, reported without errors, and matches
#: nothing: byte-for-byte what a file with no sinks looks like, and it is the shape
#: that licenses a FALSE_POSITIVE. A catalog that ships this rule gets its silence
#: cross-examined; one that does not is trusted as before, so the Python catalog is
#: unaffected and the mechanism is opt-in per language.
CONTROL_RULE = "parse-control"

# The Python backend's catalog. Kept as module constants because the packaging job
# in ci.yml asserts these files reached the wheel; `catalog_for` is what the checker
# itself uses, so a second language does not touch these names.
SINKS_RULES = "python_sinks.yaml"
TAINT_RULES = "python_taint.yaml"

# Semgrep can neither prove a taint bug (it flags safely-parameterized calls) nor
# prove its absence (rule coverage is finite). What it settles soundly is whether a
# sink of a given class occurs in a scope. So: only a provably-absent sink refutes; a
# sink we do not recognise (LOOSE) escalates; a recognised sink escalates with its
# facts. CONFIRMED is unreachable here, by design.


def rules_path(name: str) -> str:
    """Filesystem path to a bundled rule file, working from an installed wheel."""
    return str(resources.files("cca_checks") / "rules" / name)


def catalog_for(path: str) -> tuple[str, str] | None:
    """(sinks, taint) rule filenames for `path`'s language, or None if uncovered.

    None means "escalate", for either reason: no backend covers the file at all, or a
    backend covers it but ships no sink catalog. Both must be loud, because running
    one language's sink patterns over another's source matches nothing, and an empty
    result set is precisely what licenses a FALSE_POSITIVE here.
    """
    from . import languages

    backend = languages.resolve(path)
    catalog = getattr(backend, "semgrep_catalog", None)
    if catalog is None:
        return None
    return catalog("sinks"), catalog("taint")


def rule_name(check_id) -> str:
    """Semgrep namespaces check_id by the config file's path. Rule ids contain no dots.

    check_id is untrusted input from semgrep's JSON output: it must be a str to be
    meaningful, but a malformed result could carry None, an int, or a dict. Any
    non-string input yields "" rather than raising, so a malformed entry falls through
    to "no match" here and is handled by the caller's escalate-don't-drop policy.
    """
    if not isinstance(check_id, str):
        return ""
    return check_id.rsplit(".", 1)[-1]


def run_semgrep(config: str, path: str) -> list[dict] | None:
    """Run semgrep offline over one file.

    None  = could not tell (missing binary, timeout, crash, unparseable output,
            semgrep reported errors, or nothing was scanned) -> escalate.
    list  = semgrep ran; possibly empty, meaning it genuinely matched nothing.
    Never conflate the two.

    `--disable-nosem` and `--no-git-ignore` exist because an empty result set is
    what licenses a FALSE_POSITIVE, and both suppression mechanisms are controlled
    by the repo under audit. A `# nosemgrep` comment on a backdoored line, or a
    crafted `.gitignore` / `.semgrepignore`, would otherwise let that repo delete
    the evidence against itself and collect a refutation carrying an authoritative
    `source: semgrep`. A refutation may only rest on a scan the audited repo could
    not influence.
    """
    exe = resolve_tool("semgrep")
    if exe is None:
        # Missing from PATH, or resolved inside the audited tree (hijack attempt).
        return None
    cmd = [exe, "--config", config, "--json", "--metrics=off",
           "--disable-version-check", "--quiet", "--disable-nosem", "--no-git-ignore",
           os.path.abspath(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return None
    except OSError:  # includes FileNotFoundError
        return None
    if not (proc.stdout or "").strip():
        return None
    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    try:
        if not isinstance(data, dict) or data.get("errors"):
            return None
        paths = data.get("paths")
        scanned = paths.get("scanned") if isinstance(paths, dict) else None
        if not isinstance(scanned, list) or len(scanned) < 1:
            return None
        results = data.get("results")
        return results if isinstance(results, list) else None
    except Exception:
        return None


def hits_in_span(results: list[dict], lo: int, hi: int, rule_id: str) -> list[dict]:
    """Results for `rule_id` whose start line falls inside [lo, hi], 1-indexed inclusive.

    Semgrep's start.line is 1-indexed -- unlike pyright's 0-indexed range.start.line.

    A result is skipped outright when it is not a dict (it carries no class, so it
    cannot be a hit for this class) or when its rule name does not match `rule_id`.
    Otherwise -- the rule name matches -- a line we cannot interpret (missing/None/
    non-int `start.line`, or a non-dict `start`) does NOT drop the result: it is
    included. An uninterpretable entry for the claimed sink class must read as
    "a sink may be present" (escalate), never as "no sink" (refute); only a line we
    can positively place outside [lo, hi] rules a hit out.
    """
    out = []
    for r in results:
        if not isinstance(r, dict) or rule_name(r.get("check_id")) != rule_id:
            continue
        start = r.get("start")
        line = start.get("line") if isinstance(start, dict) else None
        if not isinstance(line, int) or lo <= line <= hi:
            out.append(r)
    return out


def catalog_has_control(sink_rules: str) -> bool:
    """True if the catalog file ships a `parse-control` rule.

    Read from the file rather than hardcoded per language, so adding the control to a
    catalog is the single act that switches the check on for it -- there is no second
    place to remember, and therefore no way for the two to disagree.
    """
    try:
        with open(rules_path(sink_rules), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return False
    return f"id: {CONTROL_RULE}\n" in text or f"id: {CONTROL_RULE} " in text


def _control_fired(results: list[dict], sink_rules: str) -> bool:
    """Did the parse control match, on a catalog that ships one?

    True when the catalog has no control at all -- that language's silence is trusted
    exactly as it was before, so this is opt-in and the Python path is untouched.

    NOTE THE SCOPE: the control is checked ANYWHERE IN THE FILE, not within the
    claim's enclosing span. It answers "did the parser understand this file", and a
    function elsewhere in the file proves that just as well as one here. Scoping it
    to the span would make every claim inside a scope containing no function item --
    a `const` block, a trait definition -- permanently unrefutable for a reason that
    has nothing to do with parsing.
    """
    if not catalog_has_control(sink_rules):
        return True
    return any(isinstance(r, dict) and rule_name(r.get("check_id")) == CONTROL_RULE
               for r in results)


def _describe(hits: list[dict], file: str) -> str:
    parts = []
    for h in hits:
        line = (h.get("start") or {}).get("line")
        msg = (h.get("extra") or {}).get("message", "")
        parts.append(f"{rule_name(h.get('check_id', ''))} @ {file}:{line}: {msg}".rstrip(": "))
    return "; ".join(parts)


def verdict_for_taint(claim: Claim, sinks=None, taint=None) -> Verdict:
    """Settle a taint claim. Refutes a false premise; never confirms.

    `sinks`/`taint` are injectable result lists. When both are omitted, semgrep runs.
    """
    # Two things must be covered before a scan means anything: the sink CLASS an
    # agent named, and the LANGUAGE the file is written in. The class check is
    # unchanged. The language check no longer hardcodes `.endswith(".py")` -- it asks
    # the registry which catalog covers the file, so a second language is picked up
    # here without editing this module.
    #
    # It stays at the TOP rather than beside the `run_semgrep` calls below, because
    # `sinks`/`taint` are injectable: with results supplied, the scan is skipped and
    # a language check further down would be skipped with it. The caller would then
    # fall through to `enclosing_span`, which fails for its own reasons and escalates
    # with "could not determine the enclosing scope" -- still safe, but it tells the
    # reader to go looking at the file's syntax instead of at the missing catalog.
    catalog = catalog_for(claim.file)
    if claim.sink_class not in SUPPORTED_SINK_CLASSES or catalog is None:
        return make_verdict(
            claim.finding_id, "UNCERTAIN",
            f"sink class {claim.sink_class!r} / language not covered by the bundled "
            f"catalog; escalated", "llm")

    if sinks is None and taint is None:
        sink_rules, taint_rules = catalog
        sinks = run_semgrep(rules_path(sink_rules), claim.file)
        if sinks is not None:
            taint = run_semgrep(rules_path(taint_rules), claim.file) or []

    if sinks is None:
        return make_verdict(claim.finding_id, "UNCERTAIN",
                            "semgrep unavailable; falling back to LLM", "llm")
    if taint is None:
        taint = []

    try:
        lo, hi = enclosing_span(claim.file, claim.line)
    except Exception:
        return make_verdict(claim.finding_id, "UNCERTAIN",
                            f"could not determine the enclosing scope @ {claim.file}:"
                            f"{claim.line}; escalated", "semgrep")

    cls = claim.sink_class
    strict = hits_in_span(sinks, lo, hi, f"sink-strict-{cls}")
    if strict:
        ev = f"semgrep: {cls} sink present in the enclosing scope: {_describe(strict, claim.file)}"
        traces = hits_in_span(taint, lo, hi, f"taint-{cls}")
        if traces:
            ev += (f" | taint rule matched: {_describe(traces, claim.file)}"
                   f" -- a match is not proof: semgrep cannot distinguish a real injection"
                   f" from a safely-parameterized call. Adjudicate.")
        return make_verdict(claim.finding_id, "UNCERTAIN", ev, "semgrep")

    loose = hits_in_span(sinks, lo, hi, f"sink-loose-{cls}")
    if loose:
        ev = (f"semgrep: possible unrecognized sink in the enclosing scope "
              f"({_describe(loose, claim.file)}); not a vetted {cls} sink, so the premise "
              f"cannot be refuted; escalated")
        return make_verdict(claim.finding_id, "UNCERTAIN", ev, "semgrep")

    # Everything below this point is a REFUTATION resting on semgrep's silence, so
    # the silence gets cross-examined first: did the parser actually read this file?
    if not _control_fired(sinks, catalog[0]):
        return make_verdict(
            claim.finding_id, "UNCERTAIN",
            f"semgrep matched no {cls} sink in the enclosing scope @ "
            f"{claim.file}:{claim.line}, but the catalog's parse control did not fire "
            f"either -- the file was scanned yet apparently not understood, so this "
            f"silence is not evidence of absence; escalated", "semgrep")

    ev = (f"semgrep: no {cls} sink in the enclosing scope @ {claim.file}:{claim.line} "
          f"(lines {lo}-{hi}); the finding's premise does not hold")
    return make_verdict(claim.finding_id, "FALSE_POSITIVE", ev, "semgrep")
