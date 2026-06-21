# Contributing to CCA-Audit

Thanks for your interest in contributing! This project welcomes contributions of all kinds.

## How to Contribute

### Reporting Issues

- Use GitHub Issues to report bugs or request features
- Include the pipeline (`/audit-fix` v1 or `/audit-fix-v2`), OS, and reproduction steps

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

Install in a test project and run `/audit-fix no-fix` (or `/audit-fix-v2 no-fix`) on a file with
known issues, and confirm the findings report is produced without applying fixes.

## License

By contributing, you agree that your contributions will be licensed under the MIT License.
