"""Counts stated twice on the README must not disagree -- or be stated twice.

Both defects this guards against were live on master at 0.8.0, on the page
that is also the PyPI project description:

- The tests badge read **610** while the Engineering table read **378**. Same
  page, same fact, two numbers. The badge had been updated with the suite; the
  table had not.
- The intro read *"Ten specialised auditors"* while the auditor tables listed
  **eleven** rows. Deployability was added and the prose was never touched.

Neither is checkable against ground truth from inside a test — the true test
count depends on collection (asserting it here would mean this file changes on
every test added, and it would still be a hand-maintained number), and the
auditor set is defined by prompt files, not by code. What *is* checkable, and
is exactly what failed, is that the two statements of the same fact agree.

So this asserts internal consistency, not correctness. A release still needs a
human to confirm the numbers are right; this only guarantees the page cannot
contradict itself.
"""

import pathlib
import re

_README = pathlib.Path(__file__).resolve().parent.parent / "README.md"

_WORD_TO_INT = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6,
    "seven": 7, "eight": 8, "nine": 9, "ten": 10, "eleven": 11,
    "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15,
}

_BADGE_TESTS = re.compile(r"badge/tests-(\d+)%20passing")
_TABLE_TESTS = re.compile(r"^\|\s*Tests\s*\|\s*(\d+)", re.MULTILINE)
_PROSE_AUDITORS = re.compile(r"^(\w+) specialised auditors", re.MULTILINE | re.IGNORECASE)
# A row in the auditor tables: `| **Name** | ... |`. The verification agents
# (fp-check, differential-review, architect-reviewer) are named in prose, not
# in these tables, which is why they are not counted here.
_AUDITOR_ROW = re.compile(r"^\|\s*\*\*([^*]+)\*\*\s*(?:\*\(single authority\)\*\s*)?\|", re.MULTILINE)


def _text() -> str:
    return _README.read_text(encoding="utf-8")


def _auditor_rows(text: str) -> list[str]:
    """Rows of the Core and Conditional auditor tables, between their headings."""
    section = re.search(
        r"^\*\*Core\*\*.*?^Plus the verification agents", text, re.MULTILINE | re.DOTALL
    )
    assert section, (
        "Could not locate the auditor tables (expected a '**Core**' heading and a "
        "'Plus the verification agents' line). If the README was restructured, update "
        "this locator -- do not delete the check."
    )
    return [m.group(1).strip() for m in _AUDITOR_ROW.finditer(section.group(0))]


def test_the_test_count_is_stated_exactly_once():
    """The Engineering table is the only copy; the badge was removed in 0.8.1.

    The original defect was two copies disagreeing. 0.8.1 resolved it by
    deleting one copy rather than syncing it, because nothing in the release
    path updated the badge and it had already drifted. So the invariant is no
    longer "the copies agree" -- with one copy that assertion cannot fail, and a
    check that cannot fail is exactly what this project exists to prevent. It is
    now "there is still only one copy": reintroducing the badge brings back a
    hand-maintained number with nothing to keep it current.
    """
    text = _text()
    assert not _BADGE_TESTS.search(text), (
        "README carries a hardcoded tests badge again. Nothing in the release path "
        "updates it, so it will drift from the Engineering table -- which is what "
        "shipped on the PyPI page through 0.8.0. State the count once, in the table."
    )
    assert _TABLE_TESTS.search(text), (
        "no `| Tests | N` row found in the Engineering table -- the count is now stated "
        "only there, so losing that row means the page states it nowhere"
    )


def test_the_stated_auditor_count_matches_the_tables():
    """The intro's count equals the number of rows in the auditor tables."""
    text = _text()
    prose = _PROSE_AUDITORS.search(text)
    assert prose, "no 'N specialised auditors' sentence found in README.md"

    word = prose.group(1).lower()
    stated = _WORD_TO_INT.get(word)
    assert stated is not None, (
        f"README says {word!r} specialised auditors; extend _WORD_TO_INT in this test "
        "if that is a real number word."
    )

    rows = _auditor_rows(text)
    assert stated == len(rows), (
        f"README's intro says {word} ({stated}) specialised auditors, but the Core + "
        f"Conditional tables list {len(rows)}: {', '.join(rows)}. An auditor was added "
        "or removed and the prose was not updated."
    )


def test_the_guards_detect_the_defects_they_were_written_for():
    """Both checks must be able to fail, on the exact values that were live.

    A regex that silently stopped matching would leave both tests green on a
    self-contradicting README -- a check that verifies nothing, which is the
    failure mode this project exists to prevent.
    """
    broken = (
        '<img src="https://img.shields.io/badge/tests-610%20passing-brightgreen"/>\n'
        "| Tests | 378, on every push and PR |\n"
    )
    assert _BADGE_TESTS.search(broken).group(1) == "610"
    assert _TABLE_TESTS.search(broken).group(1) == "378"

    assert _PROSE_AUDITORS.search("Ten specialised auditors read your diff").group(1) == "Ten"
    assert _WORD_TO_INT["ten"] != len(_auditor_rows(_text()))
