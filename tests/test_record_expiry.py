"""When a RE-call audit record has lapsed: RE-call's rule, read the same way by the PRIOR BUGS
brief, the LESSONS block and the PRIOR_FP matcher (finding NUM-001, RE-call PR #977 audit,
2026-10-27).

RE-call (`recall.frontmatter.validity_bounds`) holds a date-only `valid_until` through
23:59:59.999999 UTC of that day; RE-call #1011 (draft) adds the `expired` flag it computes that way.
Red proof (user rule, 2026-09-08) is in each test's docstring; every mutation was restored.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from cca_checks.bugs import envelope

RESPONSES = Path(__file__).parent / "fixtures" / "audit_memory_responses"


def test_a_date_only_valid_until_holds_through_its_own_day():
    """Invariant: a record is current on its `valid_until` day and lapsed from the next.

    Red 2026-10-27 against the pre-fix `envelope.expired` (`<=`, be13f2f):
    `tests/test_record_expiry.py::test_a_date_only_valid_until_holds_through_its_own_day`
    failed at `assert not envelope.expired("2026-10-22", "2026-10-22")`.
    """
    assert not envelope.expired("2026-10-22", "2026-10-21")
    assert not envelope.expired("2026-10-22", "2026-10-22")
    assert envelope.expired("2026-10-22", "2026-10-23")


def test_today_is_the_utc_date_not_the_hosts(east_of_utc_late_evening):
    """Invariant: the date expiry is judged on is the UTC date, as RE-call's is, even where the
    host's local date is already the next day.

    Red 2026-10-27: mutation `datetime.now(timezone.utc)` -> `datetime.now()` (the host's local
    wall clock) in `envelope.utc_today`; failed at `assert envelope.utc_today() == "2026-10-22"`
    ('2026-10-23'). The pre-fix code has no `utc_today`, so this proof is by mutation.
    """
    assert envelope.utc_today() == "2026-10-22"
    assert not envelope.expired("2026-10-22", envelope.utc_today())


def test_none_never_lapses_and_a_malformed_value_fails_closed():
    """Invariant: no `valid_until` never lapses; one that does not read as a date is not proof
    the record is current.

    Red 2026-10-27: mutation `if valid_until is None: return False` -> `return True` in
    `envelope.expired`; failed at `assert not envelope.expired(None, "9999-12-31")`.
    Red 2026-10-27: mutation `except ValueError: return True` -> `return False` in
    `envelope.expired`; failed at `assert envelope.expired("not a date", "2000-01-01")`.
    """
    assert not envelope.expired(None, "9999-12-31")
    assert envelope.expired("not a date", "2000-01-01")
    assert envelope.expired("2026-13-01", "2000-01-01")


def test_recalls_flag_wins_over_valid_until_both_ways():
    """Invariant: an entry's boolean `expired` (RE-call #1011 (draft)) decides, whichever way it points;
    without one, the rule above reads `valid_until`.

    Red 2026-10-27: mutation deleting `if isinstance(flag, bool): return flag` in
    `envelope.record_expired`; failed at the first assertion (the far `valid_until` read as
    current). Red 2026-10-27: mutation `return flag` -> `return flag or expired(...)` in
    `envelope.record_expired`; failed at the second assertion (a lapsed `valid_until` overrode
    RE-call's `False`). The pre-fix code has no `record_expired`, so these proofs are by
    mutation; the consumer tests in test_match_fp.py and test_prior_records.py are red on it.
    """
    today = "2026-10-22"
    assert envelope.record_expired({"expired": True, "valid_until": "2099-12-31"}, today)
    assert not envelope.record_expired({"expired": False, "valid_until": "2026-10-01"}, today)
    assert envelope.record_expired({"valid_until": "2026-10-21"}, today)
    assert not envelope.record_expired({"valid_until": "2026-10-22"}, today)
    assert not envelope.record_expired({"expired": None, "valid_until": None}, today)


def test_a_flag_that_is_not_a_boolean_drops_the_record():
    """Invariant: a saved answer is untrusted, and a record with a wrong-typed field is dropped
    rather than read; an `expired` that is not a boolean is such a field. A missing or null one
    is not (saved answers from before RE-call #1011 (draft) carry none).

    Red 2026-10-27 against the pre-fix `envelope._record` (be13f2f):
    `tests/test_record_expiry.py::test_a_flag_that_is_not_a_boolean_drops_the_record` failed at
    `assert envelope.records(answer("false"))[0] == []` (the record was kept). Red 2026-10-27:
    mutation deleting the `expired` type check in `envelope._record`; failed at the same
    assertion.
    """
    base = json.loads((RESPONSES / "fp_answer.json").read_text(encoding="utf-8"))

    def answer(flag):
        value = copy.deepcopy(base)
        value["records"][0]["expired"] = flag
        return value

    assert envelope.records(answer("false"))[0] == []
    assert len(envelope.records(answer(False))[0]) == 1
    assert len(envelope.records(answer(None))[0]) == 1
    assert len(envelope.records(base)[0]) == 1
