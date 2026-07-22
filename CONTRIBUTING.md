# Contributing to CCA-Audit

Thanks for your interest in contributing! This project welcomes contributions of all kinds.

## How to Contribute

### Reporting Issues

- Use GitHub Issues to report bugs or request features
- Include the tier used (`/audit-fix`, or `fast` / `deep` / the `/audit-fix-v2` alias), OS, and reproduction steps

### Pull Requests

1. Fork the repo and create a feature branch (`git checkout -b feat/my-feature`)
2. Make your changes
3. Test your changes (see Testing below)
4. Submit a PR with a clear description

### Adding a Custom Auditor

See [docs/extending.md](docs/extending.md) for the full guide. In short:

1. Create a new agent file following the existing pattern (status block, scope section, checks, output format)
2. Define a **non-overlapping scope** -- your auditor should not duplicate checks from existing auditors
3. Add your auditor to the orchestrator's Step 1 parallel launch
4. Update `docs/auditor-scopes.md` with the new scope boundaries

### Code Style

- Agent markdown files: follow the existing structure (frontmatter, status block, scope, checks, output format, execution logging, output verification)

## Testing

The `cca_checks` package has a real test suite. Run it before opening a PR:

```bash
pip install -e ".[dev]"
pytest -q
ruff check cca_checks tests
```

The acceptance suite additionally exercises `pyright` and `semgrep` end-to-end and **skips silently
when they are absent** — so a green run without them is weaker than it looks. For the full picture:

```bash
pip install -e ".[verify]"   # hypothesis, pytest, pyright, semgrep
```

CI runs the suite on Python 3.10–3.13, plus a job that builds the wheel, installs it into a clean
venv and checks the bundled rule files are reachable through `importlib.resources`. That job matters
because tests run against the source tree: a module or data file missing from the wheel is invisible
to `pytest` and only surfaces as an `ImportError` for someone who installed the package.

**A behavioural change to a checker needs a red→green test.** Add it under `tests/`, then verify it
is genuinely red by stashing the source change (`git stash push -- cca_checks`) and confirming the
test fails before you restore it. A test that passes with and without the fix documents nothing.

Then, for the end-to-end pipeline: install in a test project and run `/audit-fix no-fix` (or
`/audit-fix deep no-fix`) on a file with known issues, and confirm the findings report is produced
without applying fixes.

### Tunables

`CCA_TIMEOUT_S` (default 120) and `CCA_MAX_EXAMPLES` (default 200) override the external-tool
timeout and the Hypothesis example budget. A malformed value falls back to the default rather than
raising — a checker that refused to start would silently degrade every claim to LLM-only
adjudication.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
