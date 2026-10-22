<p align="center">
  <a href="https://github.com/GiulioDER/cca-audit/actions/workflows/ci.yml"><img src="https://github.com/GiulioDER/cca-audit/actions/workflows/ci.yml/badge.svg?branch=master" alt="CI"/></a>
  <img src="https://img.shields.io/badge/python-3.10%20%7C%203.11%20%7C%203.12%20%7C%203.13-blue" alt="Python 3.10 to 3.13"/>
  <img src="https://img.shields.io/badge/typed-pyright-informational" alt="Type-checked with pyright"/>
  <a href="https://github.com/GiulioDER/cca-audit/blob/master/LICENSE"><img src="https://img.shields.io/badge/license-MIT-lightgrey" alt="MIT license"/></a>
</p>

# CCA-Audit

A multi-agent code auditor for [Claude Code](https://docs.anthropic.com/en/docs/claude-code) and
Codex. No finding reaches the fix plan until it has been verified against the code.

Eleven specialised auditors read your diff in parallel. Each finding is then re-derived from the code: by
`pyright`, `clippy`, `semgrep`, `pytest` or `hypothesis` where a tool can settle it, and by
adversarial review where none can. Only survivors get fixed. A differential pass checks each fix for
scope creep, and an architect gate refuses the change unless every confirmed finding has a fix and
every edit has a finding.

It also remembers. Bugs it already fixed and false alarms it already rejected come back to the next
audit of the same code, through [RE-call](https://github.com/GiulioDER/RE-call), so it does not
repeat its own mistakes.

The design rule: a check that could not run must never look like a check that passed.

## Results on code I did not write

**SciPy, merged.** Hunt mode found a copy-paste defect in `signal.decimate`: the complex-filter guard
tested `system.poles` twice and never `system.zeros`, so a filter with complex zeros crashed with
`ValueError` on valid input. Latent for three years. Fixed in
[scipy/scipy#25654](https://github.com/scipy/scipy/pull/25654), merged 23 Jul 2026 with my
regression test. I opened the PR myself under SciPy's AI policy and disclosed the assistance.

**Polymarket py-sdk, fixed by the maintainers.** Price validation counted decimal places instead of
checking tick-grid membership, so on `0.005` markets a price like `0.007` was signed and then
rejected by the exchange. Reported in
[Polymarket/py-sdk#162](https://github.com/Polymarket/py-sdk/issues/162); a maintainer shipped the
fix in [#181](https://github.com/Polymarket/py-sdk/pull/181). Of four candidates, the adversarial
verifier rejected two; the survivor was reproduced on about 23,000 price and tick cases first.

Pointed at its own code, CCA also found that CI had silently skipped about 25 tests on every Python
version ([PR #22](https://github.com/GiulioDER/cca-audit/pull/22)).

## What is different

- **A tool settles the claim where one can.** `pyright` for definedness, nullability and type,
  `semgrep` for taint, a `pytest` repro for crashes, `hypothesis` for arithmetic. An LLM may not
  overturn a tool verdict, and adjudicates only what no tool could settle.
- **A verdict claims only what its evidence supports.** `semgrep` can prove a sink absent, never an
  injection real. A clean property search returns `UNCERTAIN`, not "correct".
- **The fix is audited too.** Each hunk must map to its finding. A red-state proof reverts each fix
  and re-runs its test; a test that passes on the old code is reported as tautological.
- **Risk picks the tier, not the user.** Diffs touching money, auth or numeric code are forced to the
  full adversarial path.
- **Every step is gated.** Each layer is recorded with `python -m cca_checks pipeline record`. A
  commit guard refuses `git commit` until the run is complete and the architect reads `APPROVED`.

<p align="center">
  <a href="https://github.com/GiulioDER/cca-audit/blob/master/docs/diagrams/README.md">
    <picture>
      <source media="(prefers-color-scheme: dark)" srcset="https://raw.githubusercontent.com/GiulioDER/cca-audit/master/docs/diagrams/cca-overview-dark.png">
      <img src="https://raw.githubusercontent.com/GiulioDER/cca-audit/master/docs/diagrams/cca-overview-light.png" alt="CCA-Audit architecture: two entry points, scope and tier selection, parallel auditors, consolidation, verification backed by deterministic settlers, the fix and prove loop, the architect gate, and the commit guard reading the recorded run state" width="100%">
    </picture>
  </a>
</p>

## Memory across audits

An auditor without memory starts every run from nothing: it can miss a bug class it fixed in the
same function last month, and raise again a false alarm a verifier already rejected. CCA keeps what
its runs settled and hands it back through [RE-call](https://github.com/GiulioDER/RE-call), a
memory server that matches records to code by file, symbol and a hash of the code.

- **A ledger of real bugs.** When a run's pull request merges, each fix becomes a row in
  `~/.claude/cca-bugs/`, keyed by the merge commit. A row is `proved` only when a verifier
  confirmed the bug, the fix merged, the architect approved it and its regression test failed on
  the old code. The ledger is append only: nothing rewrites a row.
- **Past bugs in the brief.** Before Layer 1, the bugs CCA fixed at the code a change touches go to
  every auditor as context. A bug at a symbol the change touches ranks above one elsewhere in the
  file, and a bug whose fix later caused a regression is marked.
- **A false alarm is not raised twice.** A finding the verifier rejected and the architect accepted
  as false becomes a record. When the same finding returns on the same code, it is marked
  `PRIOR_FP` and listed in the report with its citation, never dropped. Suppression needs an exact
  code match, the same category and no higher priority; a high-stakes finding is never suppressed,
  and the gate derives every suppression again rather than trusting the model. `CCA_PRIOR_FP=off`
  sends each match to the verifier instead.
- **Lessons the owner accepted.** When three or more past bugs share a pattern, `bugs distill`
  drafts a lesson. It reaches auditors only after `bugs lesson accept --by NAME`, at most three
  per run, worded as something to check and never as something to ignore.

All of it is optional. Without a `recall-cca` server, audits run as before with an empty block. A
hunt never reads memory from your own projects, because it audits someone else's code.

Whether memory raises the number of real findings is not established yet. A pre-registered replay
of eight past runs with the first version of the brief (2026-10-20 and 21) found a difference within
run to run noise
([record](https://github.com/GiulioDER/cca-audit/blob/master/docs/preregistrations/2026-10-20-prior-bugs-rule-and-noise.md)).
The anchored brief, `PRIOR_FP` and lessons are not measured.

## The auditors

Scopes do not overlap, so findings do not duplicate.
[Auditor scopes](https://github.com/GiulioDER/cca-audit/blob/master/docs/auditor-scopes.md) has the
boundaries.

**Core** (FAST runs the first three):

| Auditor | Checks |
|---------|--------|
| **Security** | OWASP Top 10, injection, auth, secrets, CVEs |
| **Bug** | null refs, error handling, races, resource leaks |
| **Code Quality** | type safety, duplication, complexity, dead code |
| **Performance** | slow queries, hot paths, memory, pooling |
| **Documentation** | missing docs, comments the new code contradicts |
| **Environment** | config completeness and format |

**Conditional**, dispatched when the diff touches their concern:

| Auditor | Runs when |
|---------|-----------|
| **High-Stakes / Safety** | money, auth, delete or irreversible paths |
| **Numerical / Units** | non-trivial arithmetic |
| **Data-Integrity** | migrations, SQL, schema |
| **Dependency** | a manifest or lockfile changed |
| **Deployability** | deployable code, units or migrations |

Plus the verification agents: **fp-check**, **differential-review** and the read-only
**architect-reviewer**.

## Install

```bash
pip install cca-audit
cca-audit install-codex    # Codex: installs the skill; then say "audit+fix" in a task
cca-audit install          # Claude Code: run from the root of the project to audit
```

For Claude Code, `cca-audit install --print-hook` prints the settings.json fragment for the commit
and Stop guards. They do nothing until you add it.

The deterministic layer needs its tools on `PATH`:

```bash
pip install 'cca-audit[verify]'    # pyright, pytest
pipx install semgrep               # taint; kept apart because semgrep pins unrelated packages
pip install 'cca-audit[numeric]'   # hypothesis, mpmath
pip install 'cca-audit[rust]'      # tree-sitter Rust grammar; cargo and clippy come from your toolchain
```

Missing tools fall back to LLM verification, with one exception: **without the `numeric` extra,
arithmetic findings on high-stakes code cannot be auto-fixed.** To see what your machine can settle:

```bash
python -m cca_checks capabilities --file <source file>
```

Recording needs no server: `pipeline close` saves each run that fixed something, and
`python -m cca_checks bugs resolve --write` writes its rows once the pull request merges (by hand or
on a schedule). To give the record back to auditors, serve the memos in `~/.claude/cca-bugs/memos/`
with RE-call and register it as an MCP server named `recall-cca`.

## Usage

```
/audit-fix                 # audit and fix uncommitted changes
/audit-fix no-fix          # audit and verify only
/audit-fix p1-only         # fix only critical findings
/audit-fix fast | deep     # override the tier
/audit-fix commit 3        # audit the last 3 commits
/audit-fix files src/app.py
/audit-fix hunt src/       # find existing bugs in code you did not write
/audit-fix deferred        # gated pass over what the last run deferred
```

In Codex, drop the slash: `audit+fix`, `audit+fix hunt src/`.

| Tier | Selected for | Verification |
|------|--------------|--------------|
| **FAST** | trivial, low-stakes diffs | 3 auditors; P1 findings verified |
| **STANDARD** | normal diffs | all auditors, regression diff, red-state proof |
| **DEEP** | money, auth, numeric, hunt | STANDARD plus 2-of-3 adversarial review |

P1 findings are fixed with a red→green test, P2 are fixed now, P3 are deferred to an optional gated
pass. A long run can stop with `pipeline pause --reason` and continue later with `pipeline resume`.

Hunt mode executes the audited repository's tests and imports, so run it on third-party code in a
sandbox. See the [Security Policy](https://github.com/GiulioDER/cca-audit/blob/master/SECURITY.md).

## Limits

- Deterministic coverage exists for Python and Rust only. Other languages get LLM review, and an
  uncovered claim returns `UNCERTAIN` rather than being settled by the wrong tool.
- The numeric substrate check cannot see a sign or formula error that both arithmetics compute alike.
- A crate that does not build gives clippy nothing to check, so it is `UNCERTAIN` everywhere.
- The commit guard does not inspect command substitution or `$'...'` quoting.

The full list, with the test that asserts each one, is in
[docs/limits.md](https://github.com/GiulioDER/cca-audit/blob/master/docs/limits.md).

## Engineering

| | |
|---|---|
| Tests | 1114 passed, in CI on Python 3.10 to 3.13 |
| Lint and types | `ruff`, `pyright` |
| Design of record | [`docs/v3-design.md`](https://github.com/GiulioDER/cca-audit/blob/master/docs/v3-design.md) |

## Documentation

- [Documentation map](https://github.com/GiulioDER/cca-audit/blob/master/docs/README.md): pipeline, auditor scopes, configuration, extension, design evidence
- [Changelog](https://github.com/GiulioDER/cca-audit/blob/master/CHANGELOG.md) · [Contributing](https://github.com/GiulioDER/cca-audit/blob/master/CONTRIBUTING.md)
- Articles: [Fluency isn't evidence](https://github.com/GiulioDER/cca-audit/blob/master/docs/blog-fluency-isnt-evidence.md) · [Why AI code review hallucinates](https://github.com/GiulioDER/cca-audit/blob/master/docs/blog-why-ai-review-hallucinates.md) · [Your AI code reviewer's benchmark is memorized](https://github.com/GiulioDER/cca-audit/blob/master/docs/blog-benchmark-memorization-gap.md)

## Acknowledgements

The substrate-differential check in
[`cca_checks/substrate.py`](https://github.com/GiulioDER/cca-audit/blob/master/cca_checks/substrate.py)
began as a suggestion from Erik Hill ([@egnaro9](https://github.com/egnaro9)) in a public review
thread: run the same numeric rule under two arithmetics and let them disagree where an author cannot.
The idea is his; the implementation is mine.

## License

[MIT](https://github.com/GiulioDER/cca-audit/blob/master/LICENSE)
