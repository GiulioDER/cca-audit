# Configuration

For local bug and accepted-lesson context in CCA and standalone Codex auditors, see
[Local auditor memory](local-auditor-memory.md). This path works without a hosted serving upgrade.

## Claude Code Variant

Configuration is done by editing the agent files directly in `.claude/agents/` and `.claude/commands/`.

### Adding project context

Edit `audit-fix.md`, Step 1 prompt template:
```
Context: This is a [your project type]. Prioritize [your priorities].
```

### Disabling auditors

Comment out any agent launch in Step 1 of `audit-fix.md`.

### Changing priority criteria

Edit Step 2 in `audit-fix.md`:
```
P1 Critical: [your criteria]
P2 High: [your criteria]
P3 Nice-to-have: [your criteria]
```

### Tiers and domain dispatch

`/audit-fix` is tiered (FAST / STANDARD / DEEP), auto-selected in Step 0.6. To tune it:
- **Tier thresholds** — edit Step 0.6 (the FAST size/file limits), or force a tier per run with the
  `fast` / `deep` argument.
- **Which diffs trigger the conditional auditors** — edit the `*_PATHS` lists
  (`HIGH_STAKES_PATHS` / `NUMERIC_PATHS` / `DATA_PATHS` / `DEP_PATHS` / `DEPLOY_PATHS`) in Step 0.5.
- **Project invariants** — add your hard rules to the High-Stakes / Data-Integrity / Deployability
  auditor scopes (look for `CUSTOMIZE:`), and replace the `{PROJECT_CONTEXT}` placeholder in the
  Step 1 prompt template with your project's description.

`/audit-fix-v2` is a backward-compatible alias that just forces the DEEP tier.

## Deterministic layer

### Which languages are covered

Ask the installation, don't assume:

```
python -m cca_checks capabilities --file src/engine.rs
→ {"language": "rust",
   "claim_types": ["clock_leak", "error_swallow", "overflow", "panic_path", "taint", "unsafe_op"],
   "unavailable": {}}
```

`"language": null` means no backend covers that extension and every finding in the file rides LLM
adjudication. A claim type under `unavailable` is one whose tool is missing *here* — install it, or
expect those findings to escalate. Adding a language is documented in [extending.md](extending.md).

### Installing the tools

| extra | pulls in | enables |
|---|---|---|
| `pip install "cca-audit[verify]"` | `hypothesis`, `pytest`, `pyright`, `mpmath`, `tree-sitter*` | the deterministic layer, except `taint` |
| `pipx install semgrep` | semgrep, isolated | `taint` claims (recommended) |
| `pip install "cca-audit[taint]"` | semgrep, in your environment | `taint` claims, accepting its pins |
| `pip install "cca-audit[numeric]"` | `hypothesis`, `pytest`, `mpmath` | `numeric` claims |
| `pip install "cca-audit[rust]"` | `tree-sitter`, `tree-sitter-rust` | Rust `clock_leak` + span resolution |

**semgrep is not in `verify` on purpose.** `cca_checks` never imports it: it is resolved on PATH and
spawned, like `cargo`. But semgrep hard-pins `mcp==1.29.0`, `ruamel.yaml.clib` and `pywin32`, so
shipping it inside `verify` let an audit install rewrite packages unrelated to auditing. `pipx`
isolates it and still puts the binary on PATH, which is all `resolve_tool` needs. The `taint` extra
remains for anyone who wants it in-environment with those pins accepted knowingly.

`pyright` stays in `verify`; it carries no such pins. The Rust toolchain is different again: `cargo`
and `clippy` belong to the target project and are not pip extras. Missing any of them escalates the
affected claim types; it never silently passes them.

### Environment knobs

| variable | default | what it bounds |
|---|---|---|
| `CCA_TIMEOUT_S` | 120 | each pyright / semgrep / pytest invocation |
| `CCA_RUST_TIMEOUT_S` | 600 | each `cargo clippy` invocation — a **cold** build of a crate and all its dependencies, which routinely exceeds the 120s above |
| `CCA_MAX_EXAMPLES` | 200 | Hypothesis examples per property |
| `CCA_SUBSTRATE_TOL` | 1e-9 | relative divergence that confirms a numeric finding (bounded to [1e-15, 1.0]) |
| `CCA_SUBSTRATE_DPS` | 50 | decimal digits of the reference substrate |
| `CCA_CLOCK_STRONG_PARAMS` | `now,as_of,clock,…` | parameter names whose *dead* presence can confirm a clock leak — shared by the Python and Rust checkers |
| `CCA_CLOCK_WEAK_PARAMS` | `ts,timestamp,at,…` | names that only raise the question |
| `CCA_REMOTE_SEMGREP` | unset | an ssh destination (alias or `user@host`) where semgrep runs when it cannot run locally; see [remote-semgrep.md](remote-semgrep.md). Read from the environment only, never from the audited repository |

A malformed value falls back to the default rather than crashing the checker: refusing to start
would take the whole deterministic layer down, and a layer that is down is indistinguishable from
one that found nothing.

## Output Reports

### Output directory

Auditors return their findings as structured JSON, which the pipeline consumes as the **authoritative**
source. The Layer 1 auditors write no files: they run in parallel, and their replies are the whole
output. The agents of later layers may **optionally** write a human-readable trail to
`.claude/audits/` (the pipeline does not depend on these files). Files that may be created:

| File | Content |
|------|---------|
| `AUDIT_FPCHECK.md` | Findings-verification verdicts (Step 2.5) |
| `AUDIT_DIFFREVIEW.md` | Differential review of the fix diff (Step 5.5) |
| `FIXES.md` | Consolidated fix plan |
| `EXECUTION_LOG.md` | Run history |

## Recovering anchors from older runs

Older ledger rows may have no finding anchors. Recover them from local archived runs and
git objects with a dry run first:

```bash
python -m cca_checks.bugs.recover_anchors --repo /path/to/clone --json
```

Repeat `--repo` for each repository. The default archive is `recorded/` under the bug ledger
directory; repeat `--archive /path/to/archive-parent` to inspect other captured runs. A row
must match the archive's repository and run, and its finding file must appear in both L2
and its own L4 fix. Symbol locations come from the archived input tree, with their hashes
taken from the landed fix. The captured repair must match that landed symbol or file.
Missing input trees can supply file context, but never a guessed symbol. Unsupported rows
remain unanchored and appear in `unresolved` with a reason.

After reviewing the plan, rerun with `--write` to append provenance-bearing annotations to
`anchors.jsonl`, then run `python -m cca_checks bugs rerender --all` to update generated memos.
Neither command rewrites the bug ledger. Record, prior-brief and bug-row readers share these rules:
authored anchors take precedence, and an annotation must match the row's repository, fix
commit, run and recorded files. Repeating recovery skips rows already carrying effective
anchors. No network request, dependency installation or audited repository command is needed.

### Older findings without run IDs

Some historical rows were parsed from fix commit messages and have no run ID. Inspect a
separate Git evidence plan with:

```bash
python -m cca_checks.bugs.recover_git_anchors --repo /path/to/clone
```

Repeat `--repo` for each local clone. Recovery requires the exact finding ID and summary in
that commit's own parsed bullet. A source filename must be unambiguous within the row's files
and actually changed by the fix. Alternatively, an explicitly quoted Python definition or
function call must identify one changed definition. Duplicate definition families are refused.
Trailing test and fixture references cannot supply a production location.

These annotations provide file context only. Their hashes come from regular UTF-8 blobs at
the recorded fix commit, never the working tree. Version 2 provenance binds the original null
or non-null run ID, finding, ordinal and summary to the commit message hash and explicit
references. Definition-based references also retain the parent and both definition hashes.
The historical evidence tier is preserved; this does not establish a verified false positive.

Add `--single-file` to also anchor a fix that changed exactly one production file. Such a row
gets a file-level anchor with no symbol and the provenance method `git-single-production-file`.
Test, documentation and configuration paths do not count as production: `tests/`, `test/`,
`fixtures/`, `docs/` and `.github/` count only as the first path component (a nested
`src/pkg/fixtures/x.py` is a production file), while `test_*`, `*_test.*` and `CHANGELOG*` names
and the extensions `.md .txt .json .jsonl .yaml .yml .toml .cfg .ini .lock` count at any depth.
Findings with the prefixes `ENV`, `DEP` and `DEPLOY` are refused, as are fixes with more than
one parent, more than one production file, or a production file the row does not list. The
provenance records the exclusion list version (currently 1). `--write` is refused unless the
installed brief ranking reader understands this method. The plan was sized on 213 rows on
2026-10-28, which fell to about 92 the same evening after other recoveries; the real count is
unmeasured until the dry run.

After reviewing the JSON plan, add `--write` to append supported annotations, then run
`python -m cca_checks bugs rerender --all`. Existing annotations and authored anchors retain
precedence. No ledger row, missing run ID, severity or proof flag is rewritten.
