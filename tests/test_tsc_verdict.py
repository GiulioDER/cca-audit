"""`cca_checks.tsc_check.verdict_for_tsc` on hand-built results; no Node runs here."""

import pytest

from cca_checks.claim import Claim
from cca_checks.tsc_check import (
    BLINDNESS_CODES,
    CODES_BY_CLAIM,
    DEFINEDNESS_CODES,
    ENVIRONMENT_CODES,
    NULLABILITY_CODES,
    TYPE_CODES,
    TscDiagnostic,
    TscResult,
    is_environment,
    same_file_key,
    verdict_for_tsc,
)

CLAIMED = same_file_key("src/claimed.ts")
OTHER = same_file_key("src/other.ts")
LINE = 5
SPAN = (2, 8)


def diag(code, line=None, file=CLAIMED, end=None):
    if line is None:
        return TscDiagnostic(code, f"message {code}", file, None, None)
    return TscDiagnostic(code, f"message {code}", file, line, line if end is None else end)


def res(**overrides):
    fields = dict(version="6.0.3", files=frozenset({CLAIMED}), diagnostics=(),
                  any_lines=frozenset(), assertion_lines=frozenset(),
                  directive_lines=frozenset(), directive_targets=frozenset(),
                  nocheck=False, subscript_lines=frozenset())
    fields.update(overrides)
    return TscResult(**fields)


def claim(claim_type="nullability", line=LINE):
    return Claim("F1", "src/claimed.ts", line, claim_type)


def test_code_sets_are_pairwise_disjoint():
    sets = [DEFINEDNESS_CODES, NULLABILITY_CODES, TYPE_CODES, ENVIRONMENT_CODES,
            BLINDNESS_CODES]
    for i, a in enumerate(sets):
        for b in sets[i + 1:]:
            assert not a & b
        assert not any(5000 <= c <= 5999 for c in a)
    assert is_environment(5101) and is_environment(2307) and not is_environment(18047)
    assert set(CODES_BY_CLAIM) == {"definedness", "type", "nullability"}


def test_none_result_is_uncertain_llm():
    v = verdict_for_tsc(claim(), None, NULLABILITY_CODES, (1, 9))
    assert (v.verdict, v.source) == ("UNCERTAIN", "llm")


def test_in_set_diag_covering_line_confirms():
    v = verdict_for_tsc(claim(), res(diagnostics=(diag(18047, 4, end=5),)),
                        NULLABILITY_CODES, SPAN)
    assert (v.verdict, v.source) == ("CONFIRMED", "tsc")


def test_clean_span_refutes():
    v = verdict_for_tsc(claim(), res(), NULLABILITY_CODES, SPAN)
    assert (v.verdict, v.source) == ("FALSE_POSITIVE", "tsc")


@pytest.mark.parametrize("claim_type", ["definedness", "type", "nullability"])
def test_nocheck_blocks_refutation(claim_type):
    v = verdict_for_tsc(claim(claim_type), res(nocheck=True), CODES_BY_CLAIM[claim_type], SPAN)
    assert v.verdict == "UNCERTAIN"


def test_directive_in_span_blocks_refutation():
    r = res(directive_lines=frozenset({4}), directive_targets=frozenset({5}))
    assert verdict_for_tsc(claim(), r, NULLABILITY_CODES, SPAN).verdict == "UNCERTAIN"


def test_directive_above_span_targeting_it_blocks_refutation():
    r = res(directive_lines=frozenset({1}), directive_targets=frozenset({3}))
    assert verdict_for_tsc(claim(), r, NULLABILITY_CODES, (3, 8)).verdict == "UNCERTAIN"


@pytest.mark.parametrize("claim_type", ["type", "nullability"])
def test_assertion_line_in_span_blocks_refutation(claim_type):
    r = res(assertion_lines=frozenset({SPAN[0] + 1}))
    v = verdict_for_tsc(claim(claim_type), r, CODES_BY_CLAIM[claim_type], SPAN)
    assert v.verdict == "UNCERTAIN"


def test_assertion_line_does_not_block_definedness():
    r = res(assertion_lines=frozenset({SPAN[0] + 1}))
    v = verdict_for_tsc(claim("definedness"), r, DEFINEDNESS_CODES, SPAN)
    assert v.verdict == "FALSE_POSITIVE"


def test_directive_does_not_block_confirmation():
    r = res(directive_lines=frozenset({4}), directive_targets=frozenset({LINE}),
            diagnostics=(diag(18047, LINE),))
    assert verdict_for_tsc(claim(), r, NULLABILITY_CODES, SPAN).verdict == "CONFIRMED"


def test_directive_outside_span_does_not_block():
    r = res(directive_lines=frozenset({1}), directive_targets=frozenset({2}))
    v = verdict_for_tsc(claim(line=7), r, NULLABILITY_CODES, (5, 9))
    assert v.verdict == "FALSE_POSITIVE"


def test_definedness_confirm_blocked_by_2688():
    r = res(diagnostics=(diag(2304, LINE), diag(2688)))
    v = verdict_for_tsc(claim("definedness"), r, DEFINEDNESS_CODES, SPAN)
    assert v.verdict == "UNCERTAIN"


def test_definedness_confirm_survives_2307():
    r = res(diagnostics=(diag(2304, LINE), diag(2307, 1)))
    v = verdict_for_tsc(claim("definedness"), r, DEFINEDNESS_CODES, SPAN)
    assert v.verdict == "CONFIRMED"


def test_other_code_at_line_is_uncertain():
    r = res(diagnostics=(diag(9999, LINE),))
    v = verdict_for_tsc(claim(), r, NULLABILITY_CODES, SPAN)
    assert v.verdict == "UNCERTAIN" and "9999" in v.evidence


def test_no_span_never_refutes():
    assert verdict_for_tsc(claim(), res(), NULLABILITY_CODES, None).verdict == "UNCERTAIN"


def test_in_set_diag_elsewhere_in_span_is_uncertain():
    r = res(diagnostics=(diag(18047, LINE + 1),))
    v = verdict_for_tsc(claim(), r, NULLABILITY_CODES, SPAN)
    assert v.verdict == "UNCERTAIN" and "off by a line" in v.evidence


@pytest.mark.parametrize("claim_type", ["definedness", "type", "nullability"])
def test_environment_diag_in_file_blocks_refutation(claim_type):
    r = res(diagnostics=(diag(2307, 1),))
    v = verdict_for_tsc(claim(claim_type), r, CODES_BY_CLAIM[claim_type], SPAN)
    assert v.verdict == "UNCERTAIN"


@pytest.mark.parametrize("claim_type", ["definedness", "type", "nullability"])
def test_unlocated_diag_blocks_refutation(claim_type):
    r = res(diagnostics=(diag(2688, file=None),))
    v = verdict_for_tsc(claim(claim_type), r, CODES_BY_CLAIM[claim_type], SPAN)
    assert v.verdict == "UNCERTAIN"


def test_environment_diag_in_another_file_does_not_block():
    r = res(diagnostics=(diag(2307, 1, file=OTHER),))
    assert verdict_for_tsc(claim(), r, NULLABILITY_CODES, SPAN).verdict == "FALSE_POSITIVE"


def test_subscript_line_blocks_nullability_refutation():
    r = res(subscript_lines=frozenset({LINE}))
    assert verdict_for_tsc(claim(), r, NULLABILITY_CODES, SPAN).verdict == "UNCERTAIN"
    assert verdict_for_tsc(claim("type"), r, TYPE_CODES, SPAN).verdict == "FALSE_POSITIVE"


@pytest.mark.parametrize("claim_type", ["type", "nullability"])
def test_any_line_in_span_blocks_refutation(claim_type):
    r = res(any_lines=frozenset({SPAN[0] + 1}))
    v = verdict_for_tsc(claim(claim_type), r, CODES_BY_CLAIM[claim_type], SPAN)
    assert v.verdict == "UNCERTAIN"


def test_any_line_does_not_block_definedness():
    r = res(any_lines=frozenset({SPAN[0] + 1}))
    v = verdict_for_tsc(claim("definedness"), r, DEFINEDNESS_CODES, SPAN)
    assert v.verdict == "FALSE_POSITIVE"


@pytest.mark.parametrize("claim_type", ["type", "nullability"])
def test_blindness_diag_in_span_blocks_refutation(claim_type):
    r = res(diagnostics=(diag(7006, SPAN[0]),))
    v = verdict_for_tsc(claim(claim_type), r, CODES_BY_CLAIM[claim_type], SPAN)
    assert v.verdict == "UNCERTAIN"


def test_diag_in_another_file_at_same_line_is_ignored():
    r = res(diagnostics=(diag(18047, LINE, file=OTHER),))
    assert verdict_for_tsc(claim(), r, NULLABILITY_CODES, SPAN).verdict == "FALSE_POSITIVE"
