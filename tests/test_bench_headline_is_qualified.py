"""The benchmark's withdrawn headline must not come back unqualified.

What went wrong, and why a test rather than a note: the pilot published a
40-point memorization gap from 10/12 against 3/7. Both *arms* carried an
interval and were described as having "wide error bars". The interval on the
**difference** -- the quantity the claim is actually about -- was never
computed. It is [-0.022, +0.700], Fisher exact p = 0.129, and it contains zero.

`docs/specs/2026-07-24-fresh-corpus-scale-design.md` recorded the other half of
the problem (the arms are different corpora) on 2026-07-24. Thirteen days later
`benchmarks/README.md`, `benchmarks/results/RESULTS.md` and the blog post still
led with the gap as a finding. A note in a spec did not propagate; a failing
test does.

So this asserts a *disclosure invariant*, not a numerical result: any document
that states both 83 and 43 must, in the same document, also carry the
retraction. The numbers are free to be quoted -- they were measured -- but they
may not be quoted bare.

Verified to fail against the pre-fix text: on the three documents as they stood
at 539885c, this test reports all three as unqualified.
"""

from __future__ import annotations

import pathlib
import re

import pytest

_ROOT = pathlib.Path(__file__).resolve().parent.parent

# Every document that quotes the pair. If a new one starts quoting it, add it
# here; an unlisted document is not silently exempt, it is simply not yet known
# to this test, which is why _test_no_unlisted_document_quotes_the_gap sweeps.
_GATED = (
    "benchmarks/README.md",
    "benchmarks/results/RESULTS.md",
    "docs/blog-benchmark-memorization-gap.md",
)

# The claim, in the forms it has actually been written in: "83% ... 43%",
# "83-vs-43", "10/12 ... 3/7". Matching on the pair rather than on one number
# keeps an incidental "43%" elsewhere from tripping the gate.
_QUOTES_THE_PAIR = re.compile(r"\b83\b.{0,400}?\b43\b|\b43\b.{0,400}?\b83\b", re.S)

# At least one of these must appear in a document that quotes the pair. They are
# alternatives rather than a conjunction so the prose stays free: what is pinned
# is that the reader is told, not the wording used to tell them.
_RETRACTION_MARKERS = (
    "withdrawn",
    "does not establish",
    "not a result it established",
    "cannot claim",
    "gap I cannot claim",
)

# The interval that carries the retraction. If someone re-runs the pilot at a
# larger n these strings change, and this test SHOULD fail then: a new interval
# means the retraction text has to be rewritten, not silently inherited.
_INTERVAL_EVIDENCE = ("[-0.022, +0.700]", "[−0.022, +0.700]")
_P_VALUE_EVIDENCE = ("p = 0.129", "p = 0.129")


def _read(rel: str) -> str:
    return (_ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", _GATED)
def test_a_document_quoting_the_gap_also_carries_its_retraction(rel: str) -> None:
    text = _read(rel)
    if not _QUOTES_THE_PAIR.search(text):
        pytest.skip(f"{rel} no longer quotes the 83/43 pair; nothing to qualify")
    lowered = text.lower()
    assert any(m in lowered for m in _RETRACTION_MARKERS), (
        f"{rel} quotes the 83/43 gap without any retraction marker. "
        f"The interval on the difference is [-0.022, +0.700] (Fisher exact p = 0.129) "
        f"and contains zero; the pilot does not establish this gap. "
        f"Expected one of: {_RETRACTION_MARKERS}"
    )


@pytest.mark.parametrize("rel", _GATED)
def test_the_retraction_shows_the_interval_that_justifies_it(rel: str) -> None:
    text = _read(rel)
    if not _QUOTES_THE_PAIR.search(text):
        pytest.skip(f"{rel} no longer quotes the 83/43 pair")
    assert any(e in text for e in _INTERVAL_EVIDENCE), (
        f"{rel} retracts the gap but does not show the interval on the difference. "
        f"A retraction a reader cannot check is an assertion. Expected {_INTERVAL_EVIDENCE[0]}."
    )
    assert any(e in text for e in _P_VALUE_EVIDENCE), (
        f"{rel} retracts the gap but does not show the Fisher exact p. Expected 'p = 0.129'."
    )


def test_no_unlisted_markdown_quotes_the_gap_bare() -> None:
    """Catch the next document, not just the three that already went wrong.

    The failure mode being guarded is propagation: the gap appeared in three
    places and was corrected in a fourth (the spec). Anything new that quotes it
    must either qualify it or be added to _GATED deliberately.
    """
    offenders = []
    for path in sorted(_ROOT.rglob("*.md")):
        rel = path.relative_to(_ROOT).as_posix()
        if rel in _GATED or "/node_modules/" in f"/{rel}" or rel.startswith("docs/specs/"):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        if not _QUOTES_THE_PAIR.search(text):
            continue
        if not any(m in text.lower() for m in _RETRACTION_MARKERS):
            offenders.append(rel)
    assert not offenders, (
        "these documents quote the 83/43 gap with no retraction: "
        f"{offenders}. Either qualify them or add them to _GATED."
    )
