# Documentation Map

CCA-Audit's documentation has three layers: use the tool, understand the pipeline, and inspect the
design evidence behind the deterministic verifiers.

## Start Here

| Need | Read |
|---|---|
| Install and run the tool | [README](../README.md#install) |
| Understand the audit pipeline | [Pipeline diagram](pipeline-diagram.md) |
| Tune tiers, domain dispatch, and verifier tools | [Configuration](configuration.md) |
| Add an auditor or language backend | [Extending](extending.md) |
| Check auditor ownership boundaries | [Auditor scopes](auditor-scopes.md) |
| Review security implications | [Security policy](../SECURITY.md) |

## Design Evidence

The design of record is [v3-design.md](v3-design.md). The dated specs and plans under
[`docs/superpowers/`](superpowers/) are intentionally retained because they record rejected
approaches, tradeoffs, and verification evidence. Do not treat them as install docs; treat them as
the engineering log behind the current behavior.

| Area | Spec | Plan |
|---|---|---|
| v3 deterministic verification | [Design of record](v3-design.md) | [Initial plan](superpowers/plans/2026-07-09-v3-deterministic-verification.md) |
| Type and nullability | [Spec](superpowers/specs/2026-07-10-v3.1-type-nullability-design.md) | [Plan](superpowers/plans/2026-07-10-v3.1-type-nullability.md) |
| Taint via Semgrep | [Spec](superpowers/specs/2026-07-10-v3.2-taint-semgrep-design.md) | [Plan](superpowers/plans/2026-07-10-v3.2-taint-semgrep.md) |
| Numeric properties | [Spec](superpowers/specs/2026-07-21-numeric-differential-oracle-design.md) | [Plan](superpowers/plans/2026-07-21-numeric-differential-oracle.md) |
| Substrate differential checks | [Spec](superpowers/specs/2026-07-21-substrate-differential-design.md) | [Plan](superpowers/plans/2026-07-21-substrate-differential.md) |
| Fresh corpus benchmark | [Spec](specs/2026-07-24-fresh-corpus-scale-design.md) | [Benchmark README](../benchmarks/README.md) |

## Articles

- [Fluency isn't evidence](blog-fluency-isnt-evidence.md)
- [Why AI code review hallucinates](blog-why-ai-review-hallucinates.md)
- [The benchmark memorization gap](blog-benchmark-memorization-gap.md)

## Maintenance Rule

If a README claim depends on a date, result count, version, or upstream status, either make the
source link explicit or move the detail into a dated doc. README is published as the PyPI long
description, so every README link must be absolute.
