# CCA × BugsInPy detection pilot — findings (2026-07-15)

**Sample:** 12 bugs, seed 1337, blind stratified across 12 of 17 projects (PySnooper, ansible,
black, cookiecutter, fastapi, keras, luigi, matplotlib, pandas, sanic, tornado, youtube-dl).
**Method:** blind whole-file auditors (bug-auditor + numeric-auditor on numeric/data projects) on
the **buggy and fixed** versions of each fix-touched file, a **recognition probe**, and the
**fp-check** anti-hallucination gate. Ground truth = fixing-commit self-diff; ±3-line localization
tolerance. Run: workflow `wf_e93b2ca9-ef0`, 74 agents, 4.69M tokens, ~18 min.

## Results
| Metric | Result |
|--------|--------|
| Recall (caught + localized, post fp-check) | **10 / 12** |
| Specificity (quiet on the fixed version at the fix site) | **12 / 12** |
| fp-check | 23 raw buggy findings → 19 confirmed, **4 dropped**; no catch flipped |
| Misses | black/7 (≈3k-line file), sanic/3 |
| **Recognition (contamination)** | **12 / 12 files recognized** |

Per-bug catches localized cleanly (e.g. PySnooper@26, fastapi@22, luigi@166, matplotlib@235,
tornado@1220, youtube-dl@3522, pandas@[863,991], cookiecutter@[92,263], ansible@[518,842],
keras@981).

## Conclusion (the honest one)
**BugsInPy (famous 2018–2020 libraries) is ~100% contaminated for a current LLM auditor — it is
NOT usable as a "precision you can prove" recall benchmark.** The recognition probe caught every
file, and the auditors' own notes describe diffing against the memorized canonical upstream rather
than reasoning from first principles. Both recall AND specificity are recognition-aided here.

What the pilot *does* prove:
1. The harness works end-to-end (sample → materialize → blind audit → fp-check → score) and
   produces well-localized, sane results.
2. fp-check meaningfully filtered (4/23 dropped) without losing a real catch.
3. The paired buggy/fixed design discriminates perfectly on this sample.

## Next step
Pivot the headline credibility asset to a **fresh, post-training-cutoff corpus** (real bug-fix
commits merged after ~2026-02) where the recognition probe should read `recognized=false`. The
existing harness is reused verbatim — only the sampler changes. The 100%-contamination result
above becomes a strong honesty section ("why you can't just run an AI auditor on an old bug
benchmark").
