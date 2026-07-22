# CCA bug-detection benchmark

A small, **honest** benchmark of CCA's auditors + anti-hallucination gate on real bugs, designed to
separate *finding a bug* from *remembering a public patch*. Full write-up:
[**Your AI code reviewer's benchmark score is probably inflated**](../docs/blog-benchmark-memorization-gap.md).

## Headline

| | BugsInPy (2018–2020 libs) | Fresh (post-cutoff, 2026) |
|--|--|--|
| bugs | 12 | 10 |
| **recognized by the model** | **12/12 (100%)** | **3/10 (30%)** |
| confirmed recall — all | 10/12 (83%) | 5/10 (50%) |
| **confirmed recall — clean (unrecognized)** | **— (no clean data)** | **3/7 (43%)** |
| specificity (quiet on the fix) | 12/12 | 9/10 |
| fp-check drop rate | 17% | **42%** |
| — **bugs lost to the gate** | 0 | **1** |
| — false alarms prevented by the gate | 0 | 1 |

**The 40-point gap between memorized (83%) and novel (43%) code is the finding.** Full numbers +
per-bug detail + caveats: [results/RESULTS.md](results/RESULTS.md) ·
[results/PILOT_FINDINGS.md](results/PILOT_FINDINGS.md).

## Method

- **Ground truth** = the human fixing-commit's own diff (the exact lines changed to fix the bug).
- **Paired design**: run the auditors **blind** on both the *buggy* and *fixed* version of each file.
  A **catch** = a finding within ±3 lines of a fix hunk on the buggy file. **Specificity** = staying
  quiet at that spot on the fixed file.
- **Recognition probe**: a separate agent is asked whether it recognizes the exact file / could
  reproduce its canonical version from memory. Catches on *recognized* files are contaminated
  (recall via memory, not reasoning); the honest number is recall on **unrecognized** files.
- **fp-check gate**: every raw finding is re-verified against the code; unprovable ones are dropped.
  Each drop records a structured `drop_reason`, separating a **refutation** ("the code disproves this")
  from an **inconclusive** ("may be real, this file can't settle it") — they are opposite signals and
  a bare drop count hides the difference.
- **Drop adjudication**: a drop rate on its own is uninterpretable, because a gate that correctly
  suppresses a hallucination and a gate that kills a real bug both just increment it. Ground truth
  settles it deterministically: a drop landing on a fix hunk in the *buggy* file is a **wrong drop**
  (and **FATAL** if nothing else caught that bug); a drop landing there in the *fixed* file is a
  **correct drop** that bought specificity. The gate is scored in both directions.

Measures **detection + localization**, not fix-correctness (that would need each project's tests).

## Reproduce

The deterministic parts are plain Python + the GitHub CLI (`gh auth login` first). The audit step
runs the multi-agent auditors, so it needs **CCA installed in Claude Code**. Paths default to this
`benchmarks/` dir; override with `CCA_BENCH_DIR`.

```bash
# --- BugsInPy corpus ---
git clone --depth 1 https://github.com/soarsmu/BugsInPy   # into $CCA_BENCH_DIR
python harness/select_sample.py > harness/manifest.json   # seeded, blind (seed 1337)
python harness/materialize.py                             # fetch buggy+fixed @ commits, ground truth
python harness/build_tasks.py                             # -> harness/tasks_wf.json

# --- Fresh post-cutoff corpus ---
python harness/mine_fresh.py                              # mine recent bug-fix PRs -> fresh_manifest.json
python harness/fresh_materialize.py                       # -> data_fresh/ + tasks_fresh_wf.json

# --- Audit step (in Claude Code, with CCA agents installed) ---
# Run harness/audit_workflow.js via the Workflow tool, passing the tasks_*.json array as `args`.
# It returns per-file findings (raw + fp-check-confirmed) + the recognition verdict.
# Save that result array to results/wf_output.json (BugsInPy) / results/wf_fresh_output.json (fresh).

# --- Score ---
python harness/score.py results/wf_output.json       harness/bugs_index.json
python harness/score.py results/wf_fresh_output.json harness/bugs_fresh_index.json
```

## Files

| Path | What |
|------|------|
| `harness/select_sample.py` | pre-registered seeded sampler (BugsInPy) |
| `harness/materialize.py` · `fresh_materialize.py` | fetch buggy+fixed source @ commits + ground truth |
| `harness/mine_fresh.py` | mine recent bug-fix PRs for the uncontaminated corpus |
| `harness/build_tasks.py` | assemble the audit task list + ground-truth index |
| `harness/audit_workflow.js` | the parallel blind-audit + recognition-probe + fp-check workflow |
| `harness/score.py` | deterministic scorer (catch / specificity / recognition split) |
| `harness/*.json` | the exact sampled manifests + ground-truth indices used |
| `results/` | scored summaries + raw workflow outputs (the receipts) |

## Honest caveats

Pilot scale (7 clean bugs; 3/7 has wide error bars) · the gate dropped one *real* localized catch
(satpy #3367 — now flagged `FATAL` automatically, not by hand) · both stored runs predate the
`drop_reason` field, so their refuted/inconclusive split is **unmeasured**, not zero ·
one false alarm on a 318 KB file (clu-comics) · ±3-line localization tolerance ·
detection/localization only, not fix-correctness. See [results/RESULTS.md](results/RESULTS.md).
