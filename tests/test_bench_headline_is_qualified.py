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


# --- the figures themselves, recomputed rather than pinned as strings ------------------

def _interval_module():
    """Import `benchmarks/harness/interval.py` by path.

    `benchmarks/harness/` is a directory of runnable scripts, not an installed package,
    so there is no import path to it from `tests/`.
    """
    import importlib.util

    path = _ROOT / "benchmarks" / "harness" / "interval.py"
    spec = importlib.util.spec_from_file_location("_cca_interval", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_retraction_figures_are_what_the_module_computes() -> None:
    """The documents quote three figures; nothing recomputed them until this test.

    They were asserted as string literals only, so a change to `interval.py` could not be
    detected: the retraction would keep quoting numbers the code no longer produced. Since
    the retraction is the whole point of the change, its arithmetic has to be checkable.
    """
    interval = _interval_module()
    bugsinpy, fresh = interval.RETRACTED_COMPARISON
    assert (bugsinpy.hits, bugsinpy.n) == (10, 12)
    assert (fresh.hits, fresh.n) == (3, 7)

    lo, hi = interval.wilson(3, 7)
    assert (round(lo, 3), round(hi, 3)) == (0.158, 0.750)
    lo, hi = interval.wilson(10, 12)
    assert (round(lo, 3), round(hi, 3)) == (0.552, 0.953)

    lo, hi = interval.newcombe_difference(bugsinpy, fresh)
    assert (round(lo, 3), round(hi, 3)) == (-0.022, 0.700), (
        f"the published difference interval is [-0.022, +0.700]; the module now computes "
        f"[{lo:.4f}, {hi:.4f}]. If that is intended, the retraction text has to be rewritten "
        f"rather than silently inherited."
    )
    p = interval.fisher_exact_two_sided(bugsinpy, fresh)
    assert round(p, 3) == 0.129, f"published p = 0.129, module computes {p:.6f}"
    assert lo < 0 < hi, "the retraction rests on the interval containing zero"


def test_fisher_exact_matches_exact_rational_arithmetic_at_scale() -> None:
    """Guards the specific defect the epsilon fix removed.

    The comparison used an ABSOLUTE `+ 1e-9` tolerance on a probability, so once the
    observed table's probability fell below ~1e-9 it swept in strictly more probable
    tables and the function could not return anything smaller. At (300,100 / 100,300) it
    returned 1.61e-9 for a true 6.01e-47.

    The pilot's own counts sit nowhere near that floor, which is why it was invisible, and
    this module exists precisely to be re-run at the larger n the scale design calls for.
    So the guard is a large-count case against an independent exact reference, not a
    re-assertion of the small numbers above.
    """
    from fractions import Fraction
    from math import comb

    interval = _interval_module()

    def reference(a1: int, a2: int, b1: int, b2: int) -> Fraction:
        """The same hypergeometric definition, in exact rationals, written independently."""
        row1, row2, col1 = a1 + a2, b1 + b2, a1 + b1
        total = a1 + a2 + b1 + b2

        def table(x: int) -> Fraction:
            return Fraction(comb(row1, x) * comb(row2, col1 - x), comb(total, col1))

        observed = table(a1)
        lo, hi = max(0, col1 - row2), min(row1, col1)
        return sum((table(x) for x in range(lo, hi + 1) if table(x) <= observed), Fraction(0))

    cases = [
        (10, 2, 3, 4),        # the pilot itself
        (300, 100, 100, 300),  # p ~ 6e-47: below the old absolute floor
        (200, 0, 0, 200),      # p ~ 2e-119: far below it
        (1, 1, 1, 1),          # every table equiprobable by symmetry -> p = 1
        (7, 7, 7, 7),          # ditto, larger
        (0, 5, 5, 0),          # a zero cell in each arm
    ]
    for a1, a2, b1, b2 in cases:
        got = interval.fisher_exact_two_sided(
            interval.Arm("a", a1, a1 + a2), interval.Arm("b", b1, b1 + b2)
        )
        want = float(reference(a1, a2, b1, b2))
        assert got == pytest.approx(want, rel=1e-12, abs=0.0), (
            f"({a1},{a2} / {b1},{b2}): module {got:.6e} vs exact {want:.6e}"
        )
