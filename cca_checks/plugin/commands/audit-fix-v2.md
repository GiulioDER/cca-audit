---
description: "DEPRECATED ALIAS. CCA v2's safety gates are now the DEFAULT in the canonical tiered /audit-fix. Kept for backward compatibility; it just forces the DEEP tier. Prefer /audit-fix (auto-tiered)."
---

# CCA Audit+Fix v2 — alias → `/audit-fix` (DEEP tier)

> **This is now a thin alias.** The v2 safety gates — anti-hallucination (L2.5), anti-regression
> (L5.5), conditional domain auditors (high-stakes / numeric / data), deployability, and fix→finding
> mapping — are the **DEFAULT** in the canonical pipeline [`/audit-fix`](./audit-fix.md). They are no
> longer a manual opt-in, and the canonical pipeline auto-selects FAST / STANDARD / DEEP by diff risk.

The old v1/v2 split is gone: keeping two full specs in sync was itself a drift hazard (and a CCA
finding category). There is now ONE spec.

## Behaviour

Run the canonical pipeline with the **`deep`** tier forced:

```
/audit-fix deep $ARGUMENTS
```

Everything — the Findings Schema, tiers, all layers (L1→L7), and the deferred second pass — is
defined in [`audit-fix.md`](./audit-fix.md). Do **not** duplicate the spec here.

`/audit-fix-v2 deferred` → `/audit-fix deep deferred`.
