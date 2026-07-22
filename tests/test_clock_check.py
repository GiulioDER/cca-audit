"""Contract for the clock-leak checker.

The defect class: code that accepts an INJECTED clock (a `now=` / `as_of=` /
`clock=` parameter, threaded through so callers can simulate time) and then reads
the WALL clock anyway. It survives review because both halves look correct in
isolation, and it only misbehaves once simulated and real time diverge -- i.e. at
scale, long after the diff was approved.

The verdict asymmetry under test:
  CONFIRMED       a strong injected-time parameter is present, NEVER referenced in
                  the scope, and the scope reads the wall clock. The parameter is
                  provably dead and the wall clock is provably read.
  UNCERTAIN       the injected clock IS also used (co-occurrence is not a defect --
                  an audit-log timestamp is a legitimate instance), or the clock
                  may reach the scope by a route one file cannot see.
  FALSE_POSITIVE  only when NO clock read of any kind is in the enclosing scope.

The point of the dead-parameter discriminator is that CONFIRMED feeds an auto-fix
path. "Both present" is far too weak to license rewriting money-path code.
"""
import textwrap

import pytest

from cca_checks.claim import Claim
from cca_checks.clock_check import verdict_for_clock_leak


def write(tmp_path, src, name="m.py"):
    p = tmp_path / name
    p.write_text(textwrap.dedent(src).lstrip(), encoding="utf-8")
    return str(p)


def settle(tmp_path, src, line, name="m.py"):
    path = write(tmp_path, src, name)
    return verdict_for_clock_leak(Claim("F1", path, line, "clock_leak"))


# --- CONFIRMED: the parameter is dead and the wall clock is read ---------------

def test_dead_injected_param_plus_wall_clock_read_is_confirmed(tmp_path):
    # The Synapse bug, minimized: `now` is threaded in and then ignored.
    v = settle(tmp_path, """
        from datetime import datetime

        def is_stale(record, now=None):
            return (datetime.now() - record.ts).days > 30
    """, 4)
    assert v.verdict == "CONFIRMED"
    assert v.source == "ast"
    assert "now" in v.evidence


def test_confirmed_resolves_through_an_import_alias(tmp_path):
    # `dt.now()` is the same defect wearing a different name. A checker that only
    # matched the literal text `datetime.now` would refute this file.
    v = settle(tmp_path, """
        import datetime as dt

        def stamp(as_of):
            return dt.datetime.utcnow()
    """, 4)
    assert v.verdict == "CONFIRMED"


@pytest.mark.parametrize("call", [
    "datetime.datetime.now()", "datetime.datetime.utcnow()",
    "datetime.datetime.today()", "datetime.date.today()",
    "time.time()", "time.time_ns()",
])
def test_each_catalogued_wall_clock_source_confirms(tmp_path, call):
    v = settle(tmp_path, f"""
        import datetime
        import time

        def f(now):
            return {call}
    """, 5)
    assert v.verdict == "CONFIRMED", f"{call} was not recognised as a wall-clock read"


# --- UNCERTAIN: real co-occurrence, but not provably a defect ------------------

def test_injected_param_that_is_actually_used_only_escalates(tmp_path):
    # Legitimate shape: `now` drives the logic, the wall clock stamps an audit log.
    # Auto-"fixing" this would be a wrong edit, so it must never CONFIRM.
    v = settle(tmp_path, """
        from datetime import datetime

        def is_stale(record, now):
            log(created=datetime.now())
            return (now - record.ts).days > 30
    """, 4)
    assert v.verdict == "UNCERTAIN"
    assert "now" in v.evidence


def test_wall_clock_with_no_injected_signal_escalates_never_refutes(tmp_path):
    # The clock may be threaded via self, a global, or a caller this file cannot
    # see. Absence of a parameter is not absence of injected time.
    v = settle(tmp_path, """
        from datetime import datetime

        def f(record):
            return datetime.now()
    """, 4)
    assert v.verdict == "UNCERTAIN"


def test_weak_param_name_never_licenses_confirmed(tmp_path):
    # `timestamp` is as often plain data as it is an injected clock. It is enough
    # to raise the question, never enough to settle it.
    v = settle(tmp_path, """
        from datetime import datetime

        def f(timestamp):
            return datetime.now()
    """, 4)
    assert v.verdict == "UNCERTAIN"


def test_self_clock_attribute_is_a_signal_but_only_escalates(tmp_path):
    v = settle(tmp_path, """
        from datetime import datetime

        class A:
            def f(self):
                self.clock()
                return datetime.now()
    """, 5)
    assert v.verdict == "UNCERTAIN"


def test_monotonic_sources_escalate_rather_than_confirm(tmp_path):
    # Not a wall clock, but still un-injectable. Commonly legitimate for durations,
    # so it raises the question without settling it.
    v = settle(tmp_path, """
        import time

        def f(now):
            return time.perf_counter()
    """, 4)
    assert v.verdict == "UNCERTAIN"


def test_uncalled_clock_handle_blocks_refutation(tmp_path):
    # `default_factory=datetime.now` never appears in call position here, but the
    # wall clock is plainly reachable. Refuting would be unsound.
    v = settle(tmp_path, """
        from datetime import datetime
        import dataclasses

        def f(now):
            return dataclasses.field(default_factory=datetime.now)
    """, 5)
    assert v.verdict == "UNCERTAIN"


def test_star_import_blocks_refutation(tmp_path):
    # `now()` may well be datetime's. A file that cannot resolve its own names
    # cannot prove the absence of a clock read.
    v = settle(tmp_path, """
        from datetime import *

        def f(record):
            return record.ts
    """, 4)
    assert v.verdict == "UNCERTAIN"
    assert "*" in v.evidence or "star" in v.evidence.lower()


# --- FALSE_POSITIVE: only on provable absence ---------------------------------

def test_no_clock_read_in_scope_refutes_the_premise(tmp_path):
    v = settle(tmp_path, """
        from datetime import datetime

        def f(now):
            return now.year
    """, 4)
    assert v.verdict == "FALSE_POSITIVE"
    assert v.source == "ast"


def test_a_clock_read_in_a_DIFFERENT_function_does_not_save_the_claim(tmp_path):
    # Scope discipline: the neighbouring function's `datetime.now()` is not
    # evidence about this one.
    v = settle(tmp_path, """
        from datetime import datetime

        def clean(now):
            return now.year

        def dirty(now):
            return datetime.now()
    """, 4)
    assert v.verdict == "FALSE_POSITIVE"


def test_shadowed_name_never_confirms(tmp_path):
    # `time` is a PARAMETER here, not the module, so `time.time()` is not provably a
    # wall-clock read. Confirming would auto-"fix" correct code; refuting would
    # assume the caller never passes a clock. Neither is knowable -- so: escalate.
    v = settle(tmp_path, """
        def f(now, time):
            return time.time()
    """, 2)
    assert v.verdict == "UNCERTAIN"
    assert "rebound" in v.evidence


def test_an_import_inside_the_function_is_not_treated_as_shadowing(tmp_path):
    # The local binding here IS the real module, so the leak must still confirm.
    v = settle(tmp_path, """
        def f(now):
            import time
            return time.time()
    """, 2)
    assert v.verdict == "CONFIRMED"


# --- "could not check" is never "checked and found nothing" -------------------

def test_unparseable_file_escalates(tmp_path):
    v = settle(tmp_path, """
        def f(:
    """, 1)
    assert v.verdict == "UNCERTAIN"


def test_missing_file_escalates(tmp_path):
    v = verdict_for_clock_leak(Claim("F1", str(tmp_path / "nope.py"), 1, "clock_leak"))
    assert v.verdict == "UNCERTAIN"


def test_non_python_file_escalates(tmp_path):
    v = settle(tmp_path, "SELECT now();\n", 1, name="q.sql")
    assert v.verdict == "UNCERTAIN"


def test_bom_prefixed_file_is_still_analysed(tmp_path):
    # A BOM is legal in Python source and is what many Windows editors write. A
    # plain utf-8 read leaves U+FEFF in the text and ast.parse raises -- which would
    # silently degrade every claim on the file to UNCERTAIN.
    p = tmp_path / "bom.py"
    p.write_text("from datetime import datetime\n\n\ndef f(now):\n    return now.year\n",
                 encoding="utf-8-sig")
    v = verdict_for_clock_leak(Claim("F1", str(p), 4, "clock_leak"))
    assert v.verdict == "FALSE_POSITIVE"


def test_module_level_line_is_handled_without_crashing(tmp_path):
    v = settle(tmp_path, """
        from datetime import datetime

        STARTED = datetime.now()
    """, 3)
    assert v.verdict in {"UNCERTAIN", "CONFIRMED", "FALSE_POSITIVE"}
    assert v.evidence.strip()


# --- every decisive verdict carries an artifact -------------------------------

@pytest.mark.parametrize("src,line", [
    ("from datetime import datetime\n\ndef f(now=None):\n    return datetime.now()\n", 3),
    ("from datetime import datetime\n\ndef f(now):\n    return now.year\n", 3),
])
def test_decisive_verdicts_cite_coordinates(tmp_path, src, line):
    v = settle(tmp_path, src, line)
    assert v.verdict in {"CONFIRMED", "FALSE_POSITIVE"}
    assert v.evidence.strip(), "a decisive verdict must carry evidence"
    assert "m.py" in v.evidence
