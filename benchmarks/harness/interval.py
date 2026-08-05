"""Recompute the intervals the pilot's headline comparison needed and never had.

The pilot published a 40-point memorization gap from 10/12 (BugsInPy) against
3/7 (fresh, unrecognized). Both arms carried an interval in the write-up. The
*difference* did not, and the difference is what the claim is about.

Prior work: [[feedback-a-cross-corpus-gap-confounds-the-variable-you-claim-2026-07-24]]
recorded the *confounding* half of this on 2026-07-24 (the two arms differ in era,
selection and hunk size as well as recognition), and
`docs/specs/2026-07-24-fresh-corpus-scale-design.md` specifies the fix. Neither
computed the interval on the **difference**, which is the half this module adds and
the one that turns "imprecise" into "not established". Searched:
docs_search(source_type="memory", "memorization gap benchmark confidence interval on
the difference, CCA bug detection recall BugsInPy contamination"), no gap warning.

Run it and read the last line:

    python harness/interval.py

Everything here is closed-form and depends on nothing but the four counts, so
it needs no corpus, no network and no `scipy`. The counts live in
`RETRACTED_COMPARISON` and are the numbers `score.py` produced on the two runs
recorded in `results/RESULTS.md`.
"""

from __future__ import annotations

import math
from typing import NamedTuple

Z95 = 1.959963984540054


class Arm(NamedTuple):
    label: str
    hits: int
    n: int


# The two arms of the withdrawn comparison, as scored in results/RESULTS.md.
RETRACTED_COMPARISON = (
    Arm("BugsInPy, confirmed recall, all bugs", 10, 12),
    Arm("Fresh corpus, confirmed recall, clean (unrecognized) bugs", 3, 7),
)


def wilson(hits: int, n: int, z: float = Z95) -> tuple[float, float]:
    """Wilson score interval. Chosen over Wald because Wald is degenerate here.

    At n=7 the normal approximation puts mass outside [0, 1] and collapses to a
    zero-width interval at hits in {0, n} -- exactly the regime this pilot is in,
    so the interval that would have been most reassuring is the one that is
    least trustworthy.
    """
    if n <= 0:
        raise ValueError("n must be positive")
    if not 0 <= hits <= n:
        raise ValueError("hits must lie in [0, n]")
    p = hits / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2.0 * n)) / denom
    half = z * math.sqrt(p * (1.0 - p) / n + z * z / (4.0 * n * n)) / denom
    return (centre - half, centre + half)


def newcombe_difference(a: Arm, b: Arm, z: float = Z95) -> tuple[float, float]:
    """Newcombe's method 10 interval for p_a - p_b, built from the two Wilson intervals.

    Independent samples: the two arms are different corpora, so there is no
    pairing to exploit and nothing to gain from a paired test here.
    """
    la, ua = wilson(a.hits, a.n, z)
    lb, ub = wilson(b.hits, b.n, z)
    pa, pb = a.hits / a.n, b.hits / b.n
    lo = (pa - pb) - math.sqrt((pa - la) ** 2 + (ub - pb) ** 2)
    hi = (pa - pb) + math.sqrt((ua - pa) ** 2 + (pb - lb) ** 2)
    return (lo, hi)


def fisher_exact_two_sided(a: Arm, b: Arm) -> float:
    """Two-sided Fisher exact p for the 2x2 [[a.hits, a.miss], [b.hits, b.miss]].

    Summing every table at most as probable as the observed one, which is the
    definition rather than an approximation to it. Exact because the counts are
    small enough that an asymptotic chi-square would not be trustworthy -- the
    same reason the intervals above are Wilson and not Wald.
    """
    a1, a2 = a.hits, a.n - a.hits
    b1, b2 = b.hits, b.n - b.hits
    row1, row2 = a1 + a2, b1 + b2
    col1, total = a1 + b1, a1 + a2 + b1 + b2

    def table_p(x: int) -> float:
        return (
            math.comb(row1, x)
            * math.comb(row2, col1 - x)
            / math.comb(total, col1)
        )

    observed = table_p(a1)
    lo = max(0, col1 - row2)
    hi = min(row1, col1)
    # 1e-9 absorbs float error on tables that are equiprobable by symmetry;
    # without it, a table exactly as extreme as the observed one can be dropped
    # and the p-value comes back too small.
    return min(1.0, sum(table_p(x) for x in range(lo, hi + 1) if table_p(x) <= observed + 1e-9))


def _pct(x: float) -> str:
    return f"{x * 100:+.1f}pp"


def main() -> None:
    a, b = RETRACTED_COMPARISON
    for arm in (a, b):
        lo, hi = wilson(arm.hits, arm.n)
        print(f"{arm.label}")
        print(f"    {arm.hits}/{arm.n} = {arm.hits / arm.n:.3f}   Wilson 95% [{lo:.3f}, {hi:.3f}]")
    lo, hi = newcombe_difference(a, b)
    p = fisher_exact_two_sided(a, b)
    print(
        f"\ngap  {_pct(a.hits / a.n - b.hits / b.n)}"
        f"   Newcombe 95% [{_pct(lo)}, {_pct(hi)}]   Fisher exact p = {p:.3f}"
    )
    print(
        "\nThe interval contains zero, so this pilot does not establish the gap it was "
        "published with.\nSee benchmarks/README.md 'What this pilot does not establish'."
    )


if __name__ == "__main__":
    main()
