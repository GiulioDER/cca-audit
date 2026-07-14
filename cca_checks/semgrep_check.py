import json
import os
import subprocess
from importlib import resources
from typing import Optional

from .claim import Claim, Verdict, make_verdict
from .scope import enclosing_span

SUPPORTED_SINK_CLASSES = frozenset({"sql", "command", "code_exec", "path"})

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


def run_semgrep(config: str, path: str) -> Optional[list[dict]]:
    """Run semgrep offline over one file.

    None  = could not tell (missing binary, timeout, crash, unparseable output,
            semgrep reported errors, or nothing was scanned) -> escalate.
    list  = semgrep ran; possibly empty, meaning it genuinely matched nothing.
    Never conflate the two.
    """
    cmd = ["semgrep", "--config", config, "--json", "--metrics=off",
           "--disable-version-check", "--quiet", os.path.abspath(path)]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=120)
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
    if claim.sink_class not in SUPPORTED_SINK_CLASSES or not claim.file.endswith(".py"):
        return make_verdict(
            claim.finding_id, "UNCERTAIN",
            f"sink class {claim.sink_class!r} / language not covered by the bundled "
            f"catalog; escalated", "llm")

    if sinks is None and taint is None:
        sinks = run_semgrep(rules_path(SINKS_RULES), claim.file)
        if sinks is not None:
            taint = run_semgrep(rules_path(TAINT_RULES), claim.file) or []

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

    ev = (f"semgrep: no {cls} sink in the enclosing scope @ {claim.file}:{claim.line} "
          f"(lines {lo}-{hi}); the finding's premise does not hold")
    return make_verdict(claim.finding_id, "FALSE_POSITIVE", ev, "semgrep")
