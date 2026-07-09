from dataclasses import dataclass

@dataclass(frozen=True)
class Claim:
    finding_id: str
    file: str
    line: int  # 1-indexed
    claim_type: str
    proposition: str = ""
    predicted_impact: str = ""

@dataclass(frozen=True)
class Verdict:
    finding_id: str
    verdict: str  # CONFIRMED | FALSE_POSITIVE | UNCERTAIN
    evidence: str
    source: str   # pyright | pytest | llm

def make_verdict(finding_id: str, verdict: str, evidence: str, source: str) -> Verdict:
    # Artifact-or-UNCERTAIN: a decisive verdict must carry evidence.
    if verdict in ("CONFIRMED", "FALSE_POSITIVE") and not evidence.strip():
        return Verdict(finding_id, "UNCERTAIN", "no evidence artifact; escalated", source)
    return Verdict(finding_id, verdict, evidence, source)
