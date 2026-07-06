# Example: catching a basis-point sizing bug

A 30-line position sizer with a **subtle, money-losing units bug** — and one
**plausible-but-false** finding planted to bait a naive reviewer. This is the
reproducible proof asset for CCA: it catches the real bug that a single-pass review
skims past, and it *drops its own false positive* at the anti-hallucination gate.

- The real bug: `risk_limit_bps` is in basis points but scaled as if it were a
  percent (`/ 100` instead of `/ 10_000`) → risk budget **100x** too large. The
  adjacent line scales bps correctly via `BPS_PER_UNIT`, so the inconsistency is
  right there — and the test only asserts `size > 0`, so it stays **green**.
- The false positive: a `ZeroDivisionError` looks possible at the `return`, but
  `model.py` validates `price > 0` and `stop_distance_bps >= 1`, so it can never fire.

## Reproduce

```bash
# from the repo root, with cca-audit installed into .claude/
git checkout demo/bps-sizing
# then, in an interactive Claude Code session:
/audit-fix commit 1
```

See [DEMO-SPEC.md](DEMO-SPEC.md) for the design of record and [DEMO.md](DEMO.md) for
the annotated real run (added after the rehearsal).
