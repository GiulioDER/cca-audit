import json
import subprocess
from typing import Optional
from .claim import Claim, Verdict, make_verdict

DEFINEDNESS_RULES = {"reportUndefinedVariable", "reportUnboundVariable", "reportMissingImports"}

def run_pyright(path: str) -> list[dict]:
    proc = subprocess.run(["pyright", "--outputjson", path], capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    return data.get("generalDiagnostics", [])

def _diag_at(diags: list[dict], line_1based: int, rules: set[str]) -> Optional[dict]:
    for d in diags:
        start = d.get("range", {}).get("start", {})
        if start.get("line", -1) + 1 == line_1based and d.get("rule") in rules:
            return d
    return None

def verdict_for_definedness(claim: Claim, diags: list[dict]) -> Verdict:
    hit = _diag_at(diags, claim.line, DEFINEDNESS_RULES)
    if hit:
        ev = f"pyright {hit['rule']} @ {claim.file}:{claim.line}: {hit['message']}"
        return make_verdict(claim.finding_id, "CONFIRMED", ev, "pyright")
    ev = f"pyright: no undefined-symbol diagnostic @ {claim.file}:{claim.line}"
    return make_verdict(claim.finding_id, "FALSE_POSITIVE", ev, "pyright")
