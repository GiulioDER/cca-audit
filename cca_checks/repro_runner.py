import subprocess
import sys
from typing import Optional
from .claim import Verdict, make_verdict

# `python -m pytest` with pytest absent exits rc=1 -- the SAME code as a genuine
# test failure -- and prints this to stderr. The OSError branch below cannot catch
# it, because the interpreter itself always launches. Left unchecked, a machine
# without pytest reports "the repro failed", and since confirmation only requires
# the predicted error string to appear anywhere in the output, a claim predicting
# e.g. "No module named" is CONFIRMED by the absence of pytest. An environment gap
# must never manufacture evidence.
NO_PYTEST = "No module named pytest"

# WARNING: run_repro executes the target's test code. pytest imports conftest.py
# during collection, so running this against a repo you do not trust executes
# that repo's code with your privileges and environment. Do not point it at
# untrusted code without a sandbox (container / seccomp / a scrubbed, offline env).
def run_repro(finding_id: str, test_path: str, expected_error: Optional[str]) -> Verdict:
    try:
        proc = subprocess.run(
            # -p no:cacheprovider: keep the checker side-effect free. Without it
            # pytest writes .pytest_cache/ into the repo under audit, dirtying the
            # very tree the pipeline is reviewing (and failing on a read-only mount).
            [sys.executable, "-m", "pytest", "-xq", "-p", "no:cacheprovider",
             "--", test_path],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return make_verdict(finding_id, "UNCERTAIN",
                            "repro timed out after 120s; escalated", "pytest")
    except OSError:
        # e.g. the interpreter or pytest could not be launched
        return make_verdict(finding_id, "UNCERTAIN",
                            "repro could not run (pytest unavailable); escalated", "pytest")
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-800:]
    # Checked before the returncode, for the same reason property_check checks for
    # a missing hypothesis first: a missing dependency surfaces as an ordinary
    # rc=1, which is indistinguishable from the result we are looking for.
    if NO_PYTEST in out:
        return make_verdict(finding_id, "UNCERTAIN",
                            "repro could not run (pytest not installed); escalated", "pytest")
    rc = proc.returncode
    # pytest returncodes: 0=all passed, 1=tests failed, 2=interrupted,
    # 3=internal error, 4=usage error, 5=no tests collected. Only rc==1 is a
    # genuine test *failure* (the thing we want to treat as "reproduced");
    # 2/3/4/5 are collection/usage errors and must never be read as CONFIRMED.
    if rc == 0:
        return make_verdict(finding_id, "UNCERTAIN",
                            "repro did not trigger the impact through the validated boundary; escalated",
                            "pytest")
    if rc != 1:
        return make_verdict(finding_id, "UNCERTAIN",
                            f"repro could not run/collect (pytest rc={rc}); escalated:\n{tail}", "pytest")
    if not expected_error:
        return make_verdict(finding_id, "UNCERTAIN",
                            f"repro failed but no predicted error to confirm against:\n{tail}", "pytest")
    if expected_error not in out:
        return make_verdict(finding_id, "UNCERTAIN",
                            f"repro failed but not with '{expected_error}':\n{tail}", "pytest")
    return make_verdict(finding_id, "CONFIRMED", f"repro reproduced the impact:\n{tail}", "pytest")
