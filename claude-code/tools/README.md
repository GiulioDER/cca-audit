# CCA checkers — statistical filtering + red-state proof

Two checkers the `/audit-fix` pipeline invokes. Both exist because a rule an LLM
applies by re-reading is not a measurement — the same reason the `numeric`
auditor settles arithmetic with a Hypothesis counterexample instead of a second
opinion.

| File | Pipeline step | What it settles |
|------|---------------|-----------------|
| `cca_scorecard.py` | Step 2.6 | Per-`(auditor, category)` precision over a 90-day window. Enforces the `n < 10` guard mechanically. |
| `cca_tautology_check.py` | Step 4 (`snapshot`) + Step 5.6 (`verify`) | Whether each claimed red→green test was *actually red* against the pre-fix code. |

## Why they are here and not in `cca_checks/`

These are **not** part of the installable package. `cca_checks` is imported
(`python -m cca_checks ...`); these two are shelled out to *by path* from the
orchestrator prompt. They live beside `agents/` and `commands/` because they are
part of the same deployable bundle, and `install.sh` / `install.ps1` copy all
three into `.claude/`.

That the installer copies them is the point. Before 2026-07-24 these files lived
in a different repository altogether and were synced to `~/.claude/tools/` by
hand, so `audit-fix.md` shipped referencing two paths that nothing ever created:
Steps 2.6 and 5.6 were "command not found" on every machine except the author's,
and the pipeline reported no scorecard and no red-state proof without ever saying
why. The prompt now resolves `.claude/tools/` first and falls back to
`$HOME/.claude/tools/`, so both a project-local install and a global one work.

**Edit here, never in `.claude/tools/`.** The deployed copy is downstream. A
divergent pair is the drift class that left the orchestrator prompt itself
unversioned for a day (see `docs/specs/`).

## Safety properties worth not regressing

**`cca_scorecard.py` is additive-only by construction.** `Report` has no field
able to express a drop/suppress/exclude, and `test_never_emits_a_drop` pins that
— both by scanning the dataclass fields and by feeding it the worst possible cell
(50 straight false positives) and asserting it still only ever *adds* scrutiny.
A rarely-right auditor can still be the only one to catch the one real Critical:
*a suppression rate is not a score.* Routing ships OFF.

**`cca_tautology_check.py` has three verdicts, not two.** `RED` (failed pre-fix
on behaviour) · `TAUTOLOGICAL` (passed pre-fix — proves nothing) · `INCONCLUSIVE`
(ImportError / missing symbol / not collected — never reached the code). The
third matters: pytest counts an exception raised *inside* a test body as
`1 failed`, so exit codes alone cannot tell an `ImportError` from a real
defect-pin. It is deliberately **not** "AssertionError only" — a genuine proof
can fail pre-fix with a domain exception (an injection test failing with
`OperationalError` *is* the defect manifesting); only symbol-resolution failures
mean the test never reached the code under test.

## Run the tests

They are in this directory, not under `tests/`, because they import the modules
as bare siblings (`import cca_scorecard`). `claude-code/tools` is listed in
`testpaths`, so a plain `pytest` at the repo root collects them.

```bash
pytest claude-code/tools -q
```

Provenance: ported from WecoAI's AIDE (the third layer of its
prompt-rules / hard-coded-guards / statistical-filtering defence), 2026-07-24.
The AIDE² "recursive self-improvement" claim itself is oversold — the paper
concedes no ignition — but these two mechanisms stood up.
