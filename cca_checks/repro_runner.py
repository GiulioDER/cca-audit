import subprocess
from typing import Optional
from .claim import Verdict, make_verdict

def run_repro(finding_id: str, test_path: str, expected_error: Optional[str]) -> Verdict:
    proc = subprocess.run(["python", "-m", "pytest", "-xq", test_path],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-800:]
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
