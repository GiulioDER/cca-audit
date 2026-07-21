from cca_checks.claim import Claim, make_verdict


def test_confirmed_without_evidence_becomes_uncertain():
    v = make_verdict("BUG-1", "CONFIRMED", "", "pyright")
    assert v.verdict == "UNCERTAIN"

def test_confirmed_with_evidence_stands():
    v = make_verdict("BUG-1", "CONFIRMED", "pyright: undefined X", "pyright")
    assert v.verdict == "CONFIRMED"
    assert v.source == "pyright"

def test_claim_is_constructible():
    c = Claim("BUG-1", "sizer.py", 12, "definedness", "X undefined")
    assert c.line == 12 and c.claim_type == "definedness"
