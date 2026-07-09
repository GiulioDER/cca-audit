import subprocess
from typing import Optional
from .claim import Verdict, make_verdict

def run_repro(finding_id: str, test_path: str, expected_error: Optional[str]) -> Verdict:
    proc = subprocess.run(["python", "-m", "pytest", "-xq", test_path],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-800:]
    if proc.returncode != 0:  # a failing test == the impact reproduced
        if expected_error and expected_error not in out:
            return make_verdict(finding_id, "UNCERTAIN",
                                f"repro failed but not with '{expected_error}':\n{tail}", "pytest")
        return make_verdict(finding_id, "CONFIRMED", f"repro reproduced the impact:\n{tail}", "pytest")
    return make_verdict(finding_id, "UNCERTAIN",
                        "repro did not trigger the impact through the validated boundary; escalated",
                        "pytest")
