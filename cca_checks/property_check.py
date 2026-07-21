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
from .properties import MAX_EXAMPLES

TIMEOUT_S = 120
SOURCE = "hypothesis"

# Hypothesis prints the shrunk input under this banner, up to a blank line.
_FALSIFYING = re.compile(r"Falsifying example:.*?(?=\n\s*\n|\Z)", re.S)
# Our own violation message, which names the property and the required relation.
_PROPERTY_LINE = re.compile(r"^.*PROPERTY .+ violated \|.*$", re.M)
_NO_HYPOTHESIS = "No module named 'hypothesis'"


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

    prop = _PROPERTY_LINE.search(out)
    evidence = "property violated:\n" + example.group(0).strip()
    if prop:
        evidence += "\n" + prop.group(0).strip()
    return make_verdict(finding_id, "CONFIRMED", evidence, SOURCE)
