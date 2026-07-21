"""Settle a numeric claim by executing declared properties under Hypothesis.

WARNING: run_properties executes the target's code. pytest imports conftest.py
during collection, so running this against a repo you do not trust executes that
repo's code with your privileges and environment. Do not point it at untrusted
code without a sandbox (container / seccomp / a scrubbed, offline env).

Verdict asymmetry, and why: a violated property yields a concrete falsifying
input, which is evidence. Properties *holding* across a bounded search is not
evidence of correctness — it is only the absence of a counterexample. So this
checker can CONFIRM but can never return FALSE_POSITIVE. That mirrors
semgrep_check, where the reachable verdicts are the other way round.
"""

import re
import subprocess
import sys

from .claim import Verdict, make_verdict
from .config import MAX_EXAMPLES, TIMEOUT_S

SOURCE = "hypothesis"

# Hypothesis prints the shrunk input under this banner, up to a blank line or the
# start of a pytest separator/summary block. Terminating on the separator matters:
# pytest's grouped-exception output has no blank line before the summary, so a
# bare `(?=\n\s*\n|\Z)` runs to end-of-output and swallows the short test summary
# and timing footer into the evidence.
_FALSIFYING = re.compile(
    r"Falsifying example:.*?(?=\n\s*\n|\n=+[ =]|\n-+[ -]|\n\+-+|\Z)", re.S)
# Our own violation message, which names the property and the required relation.
_PROPERTY_LINE = re.compile(r"^.*PROPERTY .+ violated \|.*$", re.M)
_NO_HYPOTHESIS = "No module named 'hypothesis'"
_NO_PYTEST = "No module named pytest"


def _uncertain(finding_id: str, why: str) -> Verdict:
    return make_verdict(finding_id, "UNCERTAIN", why, SOURCE)


def run_properties(finding_id: str, test_path: str) -> Verdict:
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-xq", "-p", "no:cacheprovider",
             "--", test_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=TIMEOUT_S,
        )
    except subprocess.TimeoutExpired:
        return _uncertain(finding_id,
                          f"property check timed out after {TIMEOUT_S}s; escalated")
    except OSError:
        return _uncertain(finding_id,
                          "property check could not run (pytest unavailable); escalated")

    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-800:]

    # Checked before the returncode: a missing optional dependency surfaces as
    # rc=1 or rc=2 depending on where the import sits, and neither is a result.
    if _NO_HYPOTHESIS in out:
        return _uncertain(finding_id,
                          "property check unavailable (hypothesis not installed); escalated")
    # Same reasoning for pytest itself: `python -m pytest` with pytest absent exits
    # rc=1, which is the returncode this checker reads as "a property failed". The
    # OSError branch above cannot catch it because the interpreter always launches.
    if _NO_PYTEST in out:
        return _uncertain(finding_id,
                          "property check unavailable (pytest not installed); escalated")

    rc = proc.returncode
    if rc == 0:
        return _uncertain(finding_id,
                          f"no counterexample in {MAX_EXAMPLES} examples; escalated")
    if rc != 1:
        return _uncertain(finding_id,
                          f"property check could not run/collect (pytest rc={rc}); "
                          f"escalated:\n{tail}")

    example = _FALSIFYING.search(out)
    if not example:
        # rc==1 means a test failed, but without Hypothesis's banner it was a
        # plain assertion, not a property violation. Confirming on that would
        # let any red test settle a numeric finding.
        return _uncertain(finding_id,
                          f"property test failed without a falsifying example "
                          f"(not a property violation); escalated:\n{tail}")

    # Hypothesis prints "Falsifying example:" for ANY exception raised inside
    # a @given test -- not just PropertyViolation. An incidental crash (e.g. a
    # ZeroDivisionError in the code under test) shrinks and banners exactly
    # like a real property violation, so the banner alone is not sufficient
    # evidence. Only the six helpers in cca_checks/properties.py emit the
    # "PROPERTY ... violated" line, so requiring BOTH matches also enforces
    # that vocabulary: an auditor who writes a raw `assert` instead of calling
    # a helper can no longer reach CONFIRMED. That is the anti-tautology
    # guarantee (see properties.py's module docstring) enforced at the
    # verdict boundary, not just at authoring time.
    # Hypothesis reports multiple bugs from one @given test by default
    # (report_multiple_bugs=True), emitting one banner per distinct exception. The
    # banner search and the PROPERTY search below are independent first-matches
    # over the whole output, so with more than one banner they can pair bug A's
    # shrunk input with bug B's property line -- yielding a CONFIRMED whose named
    # counterexample does not violate the declared property, and which a downstream
    # agent cannot reproduce. We cannot bind them reliably after the fact, so
    # ambiguity escalates rather than guessing a pairing.
    if len(_FALSIFYING.findall(out)) > 1:
        return _uncertain(finding_id,
                          f"multiple falsifying examples reported; the property "
                          f"violation cannot be bound to a specific counterexample "
                          f"(likely an unrelated exception alongside the property "
                          f"failure); escalated:\n{tail}")

    prop = _PROPERTY_LINE.search(out)
    if not prop:
        return _uncertain(finding_id,
                          f"falsifying example found, but the failure is not "
                          f"a declared property violation (no 'PROPERTY ... "
                          f"violated' line) -- likely an unrelated exception "
                          f"in the code under test; escalated:\n{tail}")

    evidence = "property violated:\n" + example.group(0).strip()
    evidence += "\n" + prop.group(0).strip()
    return make_verdict(finding_id, "CONFIRMED", evidence, SOURCE)
