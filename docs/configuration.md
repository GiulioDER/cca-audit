# Configuration

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
| `pip install cca_checks[verify]` | everything below | the whole deterministic layer |
| `pip install cca_checks[numeric]` | `hypothesis`, `pytest`, `mpmath` | `numeric` claims |
| `pip install cca_checks[rust]` | `tree-sitter`, `tree-sitter-rust` | Rust `clock_leak` + span resolution |

`pyright`, `semgrep` and `cargo`/`clippy` are **not** pip extras — the first two are installed
separately, and the Rust toolchain belongs to the target project. Missing any of them escalates the
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

A malformed value falls back to the default rather than crashing the checker: refusing to start
would take the whole deterministic layer down, and a layer that is down is indistinguishable from
one that found nothing.

## Output Reports

### Output directory

Auditors return their findings as structured JSON, which the pipeline consumes as the **authoritative**
source. They may **optionally** also write a human-readable trail to `.claude/audits/` (the pipeline
does not depend on these files). Files that may be created:

| File | Content |
|------|---------|
| `AUDIT_CODE.md` | Code quality findings |
| `AUDIT_BUGS.md` | Runtime bug findings |
| `AUDIT_SECURITY.md` | Security findings |
| `AUDIT_PERF.md` | Performance findings |
| `AUDIT_DOCS.md` | Documentation findings |
| `AUDIT_ENV.md` | Environment config findings |
| `AUDIT_DEPLOY.md` | Deployability findings |
| `AUDIT_DEPS.md` | Dependency findings |
| `AUDIT_NUMERIC.md` | Numerical / units / sign findings |
| `AUDIT_FPCHECK.md` | Findings-verification verdicts (Step 2.5) |
| `AUDIT_DIFFREVIEW.md` | Differential review of the fix diff (Step 5.5) |
| `FIXES.md` | Consolidated fix plan |
| `EXECUTION_LOG.md` | Run history |
