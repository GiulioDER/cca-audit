## What and why

<!-- What this changes and why. Link the issue if there is one. -->

## Checklist

- [ ] `pytest -q` passes (`pip install -e ".[dev]"` first; `.[verify]` if this touches
      `pyright_check.py` / `semgrep_check.py` so the acceptance suite actually exercises them
      instead of skipping)
- [ ] `ruff check cca_checks tests` is clean
- [ ] If this changes a checker's behavior: added a red→green test under `tests/` — verified it's
      genuinely red by stashing the source change (`git stash push -- cca_checks`) before restoring it
- [ ] If this adds a new auditor or claim type: `docs/auditor-scopes.md` (or the claim-type list in
      `docs/v3-design.md`) is updated so the scope stays non-overlapping

## If this PR changes verdict behavior

<!-- Delete this section if it doesn't. -->

- **Claim type(s) affected:**
- **Old verdict → new verdict, and on what input:**
- **Red→green test:** <!-- link the test; a change here without one won't be reviewed -->
