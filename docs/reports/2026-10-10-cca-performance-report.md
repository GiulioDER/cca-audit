# CCA performance report: faster and cheaper without losing quality

> **Note added 2026-10-11.** The findings below are unedited and describe `d2317de`. Four of the
> gate defects in §4 are fixed by PR GiulioDER/cca-audit#60 (`feat/gate-every-step`, open on that
> date): `pipeline close` checks nothing (defect 1), `--status n/a` satisfies a required layer
> (defect 2), a verdict from before an L6 REVISE still passes (defect 3), and `no-fix` runs never
> close (defect 4). Until that PR merges, they remain true of `origin/master`. The two helper
> scripts named below are committed verbatim beside this file, in
> `2026-10-10-cca-performance-report/`. Both read `origin/master`, so a re-run measures the branch
> as it is then, not `d2317de`; substitute the sha to reproduce these numbers.

Source of truth: `origin/master` at `d2317de` (release 0.10.1), fetched 2026-10-10. Nothing was read
from the stale main checkout. Every number carries one of three labels:

* **MEASURED**: taken from a results file in the repository; the path is cited.
* **COUNTED**: derived from file contents or a deterministic replay; the command is given.
* **ESTIMATED**: an inference; the assumption is stated next to it.

Helper scripts used for the COUNTED numbers (read-only, both in this scratchpad):
`count.sh` (prompt bytes per tier) and `tier_replay.py` (replays the Step 0.5 content regexes over
real commits).

## 0. The short version

1. **The benchmark has never measured the shipped pipeline.** `benchmarks/harness/audit_workflow.js`
   runs only `bug-auditor` and `numeric-auditor` plus a custom fp-check prompt that says *"Default
   to DROPPING when you cannot prove the defect"*, which contradicts the shipped `cca-fp-check.md`
   (*"When the evidence is not clear, the verdict is UNCERTAIN, not FALSE_POSITIVE"*). So every
   per-layer quality claim beyond "bug plus numeric auditors find 4 of 7 clean bugs raw" is
   unmeasured. Nine of thirteen agents (code, doc, env, perf, security, dep, deploy, differential,
   architect) and four deterministic layers (L2.6, L5.6, the guard, the tier selector) have no
   measured value at all.
2. **The biggest cost lever is the tier selector, not prompt size.** The Step 0.5 content regexes
   route 44 of the last 60 commits of this repository (a tooling repo with no money path) to DEEP;
   9 of 17 small diffs that should be FAST go DEEP instead (COUNTED). DEEP adds two domain auditors
   and three skeptics per high-stakes P1.
3. **Agent count and tool turns dominate cost; system prompt bytes are a small share.** MEASURED:
   about 63K to 70K tokens per agent in the benchmark, while the largest agent prompt is 20,976
   bytes (about 5K tokens, ESTIMATED). Cutting agents and turns beats trimming prose.
4. **Several layers do LLM work that is deterministic**: L2 consolidation, the L2.5b ledger append,
   the tier selection, most of `code-auditor`, the missing-docstring half of `doc-auditor`, the
   name comparison in `env-validator`, and the scope test (b) of fp-check.
5. **Deterministic checkers re-run per claim**: pyright runs up to twice per claim; clippy does a
   **cold build into a fresh temp target directory for every Rust claim** (up to 600 s each).
   Memoising per file or crate per run is safe and large on Rust.
6. **Two parallelisation ideas are unsafe as written.** L5.6 reverts the working tree to pre-fix
   content while it runs, so running it beside L5.5 (which reads the tree) is a race. Reordering
   L5.6 before L5.5 is the safe version of that idea.
7. **Gate defects**: `pipeline close` never checks gates and logs COMPLETE regardless;
   `--status n/a` satisfies any required layer; the guard checks presence, not recency, so a stale
   L5.5 from before an L6 REVISE still passes; `no-fix` and zero-fix runs are never closed, so they
   block the user's next commit; L5.6 is pytest-only, so a Rust P1 can never get a RED proof.

## 1. Baseline

### 1.1 Subagent calls per run (COUNTED from `audit-fix.md` prose)

`k` = high-stakes P1 findings whose fp-check verdict is `llm`-sourced (they go to the 2-of-3 panel).
`r` = repair cycles that send work back through L5.5 and L6.

| Tier | L1 auditors | L2.5 | L5.5 | L6 | First-pass total | Loops |
|---|---|---|---|---|---|---|
| FAST | 3 (code, bug, security) | 0 or 1 (P1 only) | 0 | 1 | **4 to 5** | none budgeted |
| STANDARD | 6, plus DAT, DEP, DEPLOY if flagged (6 to 9) | 1 | 1 if fixes | 1 | **8 to 12** | +1 L5.5 per non-SAFE verdict, +2 per REVISE |
| DEEP | 6 + STAKES and/or NUM (always, since that is what makes DEEP) + DAT, DEP, DEPLOY (7 to 11) | 1 + 3k | 1 if fixes | 1 | **10 + 3k to 14 + 3k** | same, capped at 3 cycles per finding |
| HUNT | forced DEEP | same | same | same | same | same |

Notes that change the count:

* STAKES and NUM can never run on STANDARD: `HIGH_STAKES = RUN_STAKES OR RUN_NUM` forces DEEP, so
  the tier table's "STANDARD: if flag set" for STAKES and NUM is unreachable.
* On DEEP the panel needs the fp-check verdict `source` first (the artifact exemption), so L2.5 is
  two **sequential** agent rounds: fp-check, then panels.
* L6 has no skip condition. The architect runs even when zero fixes were made.
* `fix-planner` ships and is installed but no step dispatches it (`git grep fix-planner origin/master`
  hits only the installers and `plugin/__init__.py`).

### 1.2 Agent system prompt bytes per tier (COUNTED)

Command: `bash count.sh` (reads `git show origin/master:cca_checks/plugin/agents/cca-*.md | wc -c`).

| Agent file | Bytes | Agent file | Bytes |
|---|---:|---|---:|
| fp-check | 20,976 | deploy-auditor | 5,178 |
| numeric-auditor | 11,575 | architect-reviewer | 5,014 |
| security-auditor | 6,644 | bug-auditor (also STAKES, DAT) | 4,869 |
| fix-planner (never dispatched) | 6,003 | perf-auditor | 4,291 |
| code-auditor | 4,038 | differential-review | 3,772 |
| env-validator | 3,771 | dep-auditor | 3,450 |
| doc-auditor | 3,317 | | |

| Configuration | System prompt bytes |
|---|---:|
| FAST (3 auditors, fp-check, architect) | 41,541 |
| STANDARD minimum (6 + fp-check + differential + architect) | 56,692 |
| STANDARD maximum (+ DAT, DEP, DEPLOY) | 70,189 |
| DEEP typical, k = 0 | 73,136 |
| DEEP, all 11 auditors, k = 2 | 212,489, of which **69% is fp-check** (7 copies) |

Inside `cca-fp-check.md`, the numeric templates are 5,668 bytes and the Rust section 3,664 bytes
(COUNTED with `sed -n` over those headings): **44% of the fp-check prompt is conditional content**
that most runs never use, and the panel pays it three more times per finding.

Bytes to tokens: about 4 bytes per token (ESTIMATED; markdown with code runs closer to 3.5). The
Claude Code harness adds its own subagent system prompt on top; its size is not visible here.

### 1.3 Orchestrator: the pipeline file (COUNTED)

`audit-fix.md` is **49,341 bytes**, not the "~28KB" that `pipeline_state.py`'s docstring cites. It
grew 32,014 → 49,341 bytes (+54%) in `c95cee7`, the commit that added the anti-drift re-read rules
(`git log --follow` over the file, sizes via `git show <sha>:<path> | wc -c`).

Section sizes (COUNTED, `awk '/^## /'` byte sums):

| Section | Bytes | Section | Bytes |
|---|---:|---|---:|
| Contract | 3,109 | Step 2.6 | 2,555 |
| Step 0 | 1,501 | Step 3 | 291 |
| Step 0.4 (HUNT only) | 2,432 | Fix budget and journal | 1,448 |
| Step 0.5 | 3,529 | Step 4 | 1,858 |
| Step 0.6 | 3,676 | Step 5 | 1,348 |
| Findings schema | 1,219 | Step 5.5 | 1,549 |
| Step 1 | 7,619 | Step 5.6 | 2,011 |
| Step 2 | 1,116 | Step 6 | 2,220 |
| Step 2.5 | 6,886 | Step 7 | 890 |

Re-read cost:

* The three **explicit** re-read triggers (after L1: §2 + §2.5; after L2.5: §3 + §4; after L4: §5,
  §5.5, §5.6) add **15,059 bytes** (16,507 with the budget section).
* Full compliance with rule 1 ("re-read this file's section for a step before you perform that
  step") re-reads every step section once: **about 42,100 bytes** (39,700 in DIFF mode).
* So the pipeline text resident in the orchestrator context is **49 KB injected + 15 to 42 KB
  re-read = 64 to 91 KB, about 16K to 23K tokens** (ESTIMATED at 4 bytes per token).
* That text is re-sent on every later orchestrator turn. At an ESTIMATED 40 to 60 orchestrator turns
  per STANDARD run, that is 0.6M to 1.4M cached input tokens for the instructions alone, which at the
  usual cache read discount (about a tenth of base input) is roughly 60K to 140K full-price-equivalent
  tokens: comparable to one subagent.
* The Read tool takes line offsets, not section names, so "re-read § Step 2.5" either costs a search
  for the line range or a whole-file re-read (49 KB each). The cost is real and so is the fragility.

On Codex the cost doubles: `codex/cca-audit/SKILL.md` tells the orchestrator to read every
applicable role prompt "completely before dispatching that role", so each prompt is paid once in the
primary context and again in the subagent.

### 1.4 Measured cost per agent (MEASURED)

| Run | Agents | Tokens | Wall clock | Tokens per agent | Source |
|---|---:|---:|---:|---:|---|
| BugsInPy pilot, 12 bugs | 74 | 4.69M | about 18 min | about 63K | `benchmarks/results/PILOT_FINDINGS.md` |
| Fresh corpus, 10 bugs | 61 | 4.27M | about 34 min | about 70K | `benchmarks/results/RESULTS.md` |

Caveats: these are whole-file HUNT-style runs (one file was 318 KB) with a 500-byte task prompt, so
they are an upper-ish proxy for a DIFF run on a small change. Even so they set the ratio that
matters: **a 5K-token system prompt is under a tenth of what an agent costs**; the rest is code
reading and per-turn context re-sends. No file records per-agent or per-layer tokens, so no layer
can be costed individually from evidence today.

ESTIMATED per run using 63K to 70K tokens per agent as an upper proxy: FAST about 0.3M, STANDARD
about 0.6M to 0.8M, DEEP with k = 2 about 1.0M to 1.3M, before orchestrator and loops.

### 1.5 Deterministic checker cost (COUNTED from code)

* `pyright_check.py`: `run_pyright` per claim, plus `run_pyright_strict` (a second full pyright run)
  for the blindness probe on type-dependent claims. Each is bounded by `CCA_TIMEOUT_S` = 120 s. Two
  findings in the same file pay four pyright runs.
* `clippy_check.run_clippy`: `tempfile.TemporaryDirectory(prefix="cca-clippy-")` as `--target-dir`
  on **every call**, so every Rust claim is a cold build of the crate and all dependencies, bounded by
  `CCA_RUST_TIMEOUT_S` = 600 s. Ten Rust claims in one crate pay ten cold builds.
* Each check is a separate Bash call made by the fp-check LLM, so each also costs one model turn that
  re-sends that agent's whole context.
* `cca_tautology_check._run_one`: `subprocess.run([... "pytest", nodeid ...])` with **no timeout**.

### 1.6 Tier distribution (COUNTED by replay)

Command: `python -I tier_replay.py C:/Users/gde00/Documents/cca-audit 60` (applies the Step 0.5
`RUN_STAKES` and `RUN_NUM` content regexes to `git show` of the last 60 non-merge commits; path
globs ignored, so this is a lower bound on DEEP).

| Measure | Result |
|---|---|
| DEEP by regex over the full diff text (as the prose specifies) | **44 / 60** |
| DEEP by regex over changed lines only | 33 / 60 |
| Small diffs (≤ 40 lines, ≤ 2 files) routed DEEP instead of FAST | **9 / 17** |
| Most frequent triggers | settle 88, sign 78, drop 72, amount 42, rate 29, scale 27, decimal 24 |

Caveat: this repository's own vocabulary ("settle a claim", "sign error") is unusually rich in
trigger words, so the rate on a typical repository is unknown. The structural point survives the
caveat: the regex matches context lines and English words (`round`, `scale`, `rate`, `execute`,
`submit`, `delete`, `drop`) that appear in most diffs, and HIGH_STAKES is checked **before** size.

## 2. Layer by layer

Format per layer: cost driver; quality contribution and its evidence; improvement.

### L0.4 Target viability (HUNT only)

* **Cost**: four shell commands, no agents. Negligible.
* **Value**: prevents a whole wasted DEEP run on a dead repository. Anecdotal (the prose cites a
  real archived target); no measured rate.
* **Improvement**: make it a script (`cca_checks preflight --repo owner/name`) returning the
  five-gate table as JSON. Saves orchestrator turns and makes the REJECT mechanical. Note the
  Language gate rejects any language without a deterministic backend, so HUNT is impossible on
  TypeScript or Go; `docs/pipeline-diagram.md` describes the gate differently (see §4).

### L0.5 Language, tooling, domain detection

* **Cost**: orchestrator reasoning over regexes and path globs, plus one `capabilities` call per
  language. The real cost is downstream: it sets the flags that pick the tier.
* **Value**: fail-toward-coverage is a deliberate policy, but its over-trigger rate has never been
  measured. The replay above suggests most diffs flag.
* **Improvement**: move the whole of 0.5 and 0.6 into `python -m cca_checks tier --diff-cmd "<cmd>"`.
  Same rules, deterministic, unit-testable, and the orchestrator stops interpreting regexes from
  prose. Then, separately and under an A/B, match on **changed lines only** and on identifiers rather
  than English words.

### L0.6 Tier selection

* **Cost driver of the whole run.** DEEP vs STANDARD is +1 to +2 auditors (STAKES, NUM), +3 agents per
  `llm`-sourced high-stakes P1, an extra sequential verification round, and the NUM artifact rule.
* **Value**: unmeasured. No log records which tier ran how often or what DEEP found that STANDARD
  would not. `pipeline close` writes only the layer names and tier, and deletes `run-state.json`,
  which held per-layer timestamps.
* **Improvement**: log the tier and per-layer timestamps at close (see S1), then tune the regex.

### L1 Parallel auditors (per auditor)

Cross-cutting cost drivers for every auditor:

* Each auditor independently runs `{DIFF_CMD}` and reads the files with its own tool turns. Nothing
  is shared, so N auditors read the same diff N times.
* Every auditor prompt requires a YAML status block, an optional trail file under
  `.claude/audits/`, and an append to `EXECUTION_LOG.md`. The orchestrator reads none of it ("NOT
  read back"). That is one to three wasted tool turns per agent, and up to eleven agents appending
  concurrently to the same log file.
* The prompts say the status block must start every output **and** that the JSON array must be the
  first thing in the reply. Those cannot both hold.

| Auditor | Cost driver | Evidence of value | Overlap and determinism | Improvement |
|---|---|---|---|---|
| **bug** (BUG) | full diff read | **MEASURED, the only one**: with numeric, raw clean recall 4/7, confirmed 3/7 on the fresh corpus (`RESULTS.md`); findings carry no auditor field, so bug and numeric cannot be separated | overlaps code (Rust `.unwrap()`), perf (blocking in async, unclosed connections) | keep on strongest model |
| **security** (SEC) | runs `pip audit` / `npm audit` / `cargo audit` and a repo-wide secrets `grep -rn` on every run | none | CVEs duplicate dep-auditor's ground when a manifest changed | gate the CVE scan on `RUN_DEP`; replace secrets grep and CVE scan with deterministic tools run once by the orchestrator, results fed in as tool-sourced findings |
| **code** (CODE) | full diff read | none; output is mostly P3, which is never verified or fixed (deferred) | function length, nesting, cyclomatic complexity, parameter count, file size, unused imports, debug prints, TODO count are all linter rules (ruff `C901`, `PLR09xx`, `F401`, `T20`; eslint equivalents). Missing type hints duplicates doc-auditor | run the linter deterministically; keep a slim LLM pass for DRY, naming, consistency, or merge that remainder with doc |
| **perf** (PERF) | full diff read | none | overlaps bug on resource and connection leaks and blocking in async | candidate for a cheaper model (A/B) |
| **doc** (DOC) | full diff read | none; mostly P2/P3 | missing docstrings are deterministic (ruff `D1xx`); stale comments need an LLM; missing type annotations duplicates code | split: deterministic docstring check plus an LLM stale-comment check; merge with code's remainder (A/B) |
| **env** (ENV) | full diff read | none; its own prompt flags "missing config" as a common false positive (Step 2 tags these for L2.5) | `.env.example` vs `.env` name comparison is a script; "hardcoded values" overlaps code's magic numbers | make the name comparison deterministic; run the LLM part only when config files are in the diff |
| **STAKES** (bug-auditor) | full bug-auditor prompt plus a scope block | none | inherits the full generic bug scope, so it re-does BUG's job by construction | give it a dedicated slim prompt that excludes the generic bug list |
| **NUM** (numeric) | 11,575-byte prompt; must emit `properties:` | example only (`examples/sign-trap`, acceptance tests of the checker, not of the auditor) | none | keep; make sure `properties` survives L2 (see §4) |
| **DAT** (bug-auditor) | full bug-auditor prompt | none | GRANT checks duplicate DEPLOY (acknowledged in its prompt) | slim dedicated prompt; drop the GRANT item from one of the two |
| **DEP** (dep-auditor) | network tools (`pip list --outdated`, `npm outdated`, `cargo outdated`) | none | almost entirely deterministic tooling | replace with a script; LLM only to judge "lighter alternative" |
| **DEPLOY** (deploy-auditor) | full diff read | none | its own checks are listed as shell greps | run the greps deterministically, LLM only for deploy-target assumptions |

### L2 Consolidation

* **Cost**: done by the orchestrator in context, although the prose calls it "deterministic". It
  also requires all six to eleven full reports to land in the orchestrator context first.
* **Value**: real (dedup prevents duplicate fp-check work), but the dedup key `(file, line,
  category)` will not merge the same defect reported by two auditors under different category slugs,
  which the overlaps above make likely.
* **Improvement**: `python -m cca_checks consolidate` implementing exactly rules 1 to 3. Auditors
  return JSON; the orchestrator writes each array to a temp file (or the agent returns it via a
  structured output) and runs the script. Optional: a normalised category vocabulary so the
  cross-auditor duplicates merge.

### L2.5 Findings verification

* **Cost**: one fp-check over all P1/P2 (already batched), plus on DEEP three skeptics per
  `llm`-sourced high-stakes P1, run after fp-check. Each skeptic loads the 20,976-byte prompt and,
  because its system prompt says so, re-runs Phase 0 and Phase 1 tools that fp-check already ran.
  Each claim is one or two pyright runs or a cold clippy build, each driven by an LLM turn.
* **Value**: the only MEASURED gate, and only in a different form. On the fresh corpus the benchmark
  gate dropped 8 of 19 raw findings, 2 drops landed on ground truth, 1 cost a bug outright (satpy
  #3367), 1 prevented a false alarm; it moved clean recall from 4/7 raw to 3/7 confirmed
  (`RESULTS.md`, `summary_fresh.json`). That gate was told to default to dropping; the shipped gate
  defaults to UNCERTAIN, and both stored runs predate `drop_reason`. **The shipped gate, the panel,
  and the deterministic checkers inside it have no measured precision or recall.** The adversarial
  panel has never been run on a benchmark.
* **Improvements**: memoise tool runs per file and crate per run (S3); add a batch check command so
  one Bash call settles all claims (S4); hand skeptics the deterministic artifacts so they skip
  Phase 1 (S5); load the numeric and Rust sections only when `capabilities` or the finding prefix
  calls for them (A4); have auditors emit `claim_type` so tools run before the LLM (A3, with a risk
  analysis); batch the panel per skeptic rather than per finding (A2).

### L2.5b Disposition ledger

* **Cost**: the orchestrator hand-writes one JSON line per finding. Small, but error-prone, which is
  why the scorecard has to count malformed rows.
* **Improvement**: emit the rows from the consolidate and verify outputs by script.

### L2.6 Auditor scorecard

* **Cost**: one deterministic script plus a record call, every STANDARD/DEEP run.
* **Value**: none yet and none possible yet. Routing is OFF by default; cells need n ≥ 10. More
  importantly it **runs after L2.5**, so even with routing ON its `route_up` cannot raise the
  verification of the run it is computed in; and its documented example ("gets an fp-check even on
  FAST") cannot happen because L2.6 does not run on FAST.
* **Improvement**: move it to Step 0.6 (it reads only the historical ledger), so `route_up` can
  actually steer this run's L2.5, on every tier. Until routing is on, it can run after L6 with no
  loss. This is also the instrument that would answer "which auditor is pulling its weight", so it
  should log per-auditor yield, not only precision.

### L3 Fix plan

* **Cost**: trivial. **Value**: bookkeeping. No change. The guard does not require it; fine.

### L4 Implement

* **Cost**: orchestrator edits plus a red test per P1, plus the snapshot.
* **Value**: the red→green discipline is the substance of the pipeline's fix-side claim; unmeasured
  but structurally sound.
* **Improvement**: none on cost. The snapshot covers only files listed up front; a file first touched
  by an L5 repair is not in the manifest, so L5.6 cannot revert it (a correctness gap, not a cost).

### L5 Re-verify

* **Cost**: test and lint commands. Deterministic.
* **Value**: necessary. Note the architect at L6 is told to run tests and linter again, duplicating
  L5 with an LLM driving it.
* **Improvement**: pass L5's command output to L6 and drop the re-run from the architect prompt.

### L5.5 Regression diff

* **Cost**: one agent, plus one more per non-SAFE verdict and per REVISE cycle.
* **Value**: none measured. Its fix→finding mapping and phantom-change detection are **also** asked
  of the architect at L6, so the two overlap on their central check.
* **Improvement**: run L5.6 **before** L5.5 (S6): L5.6 is a cheap deterministic gate and a
  TAUTOLOGICAL verdict forces a rework that would invalidate the L5.5 review anyway. Then consider
  folding L5.5's hunk table into the architect (A6).

### L5.6 Red-state proof

* **Cost**: one pytest run per claimed proof against the reverted tree. Seconds, normally.
* **Value**: no benchmark, but it is exactly the kind of deterministic check the project argues for,
  and it costs almost nothing.
* **Defects that affect cost**: pytest only, so a Rust (or TS, Go) P1 can never get a RED proof;
  the architect must then REVISE, and the finding loops three times and parks. No timeout: a hanging
  test hangs the run **with pre-fix code in the working tree**. It mutates the working tree in place,
  so nothing may run beside it.
* **Improvement**: add a timeout; run the proofs in a temporary worktree or copy rather than in
  place, which is what makes any parallelism with L5.5 or L6 safe; add a cargo runner (the repo
  already has `cargo_repro.py`).

### L6 Architect gate

* **Cost**: one agent per pass, even with zero fixes; re-runs tests and lint; re-derives the
  fix→finding map L5.5 already produced.
* **Value**: none measured. It is the only gate that reads the whole diff after fixes, which is a
  real role. Its APPROVED is what the guard keys on.
* **Improvements**: skip when zero fixes were made (S2); hand it L5 and L5.5 output instead of
  re-deriving (S7); consider running it beside L5.5 once L5.6 no longer mutates the tree (A7).

### L7 Commit and close

* **Cost**: trivial. **Defects**: `close` checks nothing (see §4), and the per-layer timestamps that
  would make cost measurable are deleted with the state file.

## 3. Ranked improvements

Gains are ESTIMATED unless marked. Every "needs an A/B" item needs a pre-registration committed
before the run, per the user's standing rule; the predictions below are written to be copied into
one.

### Safe now (no quality-bearing behaviour changes, or strictly additive checks)

| # | Change | Expected gain | Quality risk | How to verify before shipping (falsifiable) | Effort |
|---|---|---|---|---|---|
| **S1** | **Instrument first.** `pipeline close` (and `abort`) append the full `layers` dict with timestamps, tier, agent count, and finding counts as one JSONL row; `pipeline record` accepts optional `--agents N --tokens N`. | none directly; it is what makes every other row measurable | none | unit test: after start, two records, close, the log row contains both timestamps. Red proof: delete the field write, test fails | S |
| **S2** | **Early exit.** Zero P1/P2 after L2 (or zero CONFIRMED after L2.5): skip L3 to L6 and close. | 1 agent (architect) plus 5 to 10 orchestrator turns on clean runs | low: the architect's criteria are about fixes; with none it reviews the feature diff, which is not its contract | prediction: in the S1 log, runs with zero fixes never record L6 REVISE or BLOCKED. If any does, the architect is acting as an auditor and this item moves to A/B | S |
| **S3** | **Memoise deterministic tools per run**: one pyright (and one strict pyright) per file, one clippy per crate with the union of force-warn lints, cached for the run in a temp dir outside the repo. | Rust: k cold builds become 1 (up to 600 s each saved per extra claim). Python: halves to quarters pyright time on multi-finding files | none if the cache is keyed on file content hash and scoped to one run; the freshness assertion stays on the one real build | test: two claims on one crate invoke `cargo` once (mock `subprocess.run`, count calls). Red proof: remove the cache, count is 2. Verdicts identical on the existing acceptance suites | M |
| **S4** | **Batch check command**: `cca_checks check-batch --claims claims.json` returns all verdicts in one call. | one LLM turn per finding saved inside fp-check (each turn re-sends the full agent context) | none: same checkers, same verdicts | prediction: verdict JSON byte-identical to per-claim calls over the acceptance fixtures | S |
| **S5** | **Skeptics receive the deterministic artifacts** and skip Phase 0 and 1. Tool outputs are evidence, not another verifier's conclusion, so the independence rule is kept. | 3 × (tool turns per finding) per panel | low; must not pass fp-check's `llm` reasoning, only tool JSON | check on a fixture with an UNCERTAIN semgrep artifact that each skeptic cites it and runs no `cca_checks` command | S |
| **S6** | **Reorder L5.6 before L5.5.** | avoids an L5.5 agent run whenever L5.6 forces a rework | none: same gates, cheaper first | count, from the S1 log, L5.5 runs followed by a TAUTOLOGICAL L5.6; each is a saved agent | S |
| **S7** | **Hand L5 output and the L5.5 table to the architect**; remove its test and lint re-run. | one LLM-driven test run per L6 pass | low: L5 is deterministic and already a required layer | architect still REVISEs a planted orphan P1 and a planted phantom fix (two fixtures) | S |
| **S8** | **Deterministic tier selector** `cca_checks tier`, same rules as the prose. | a few orchestrator turns; mainly reproducibility | none: same policy | tests reproduce the prose table; replay over 60 commits yields the 44/60 above | S |
| **S9** | **Deterministic consolidation and ledger** (`cca_checks consolidate`, rows emitted by script). | orchestrator reasoning over 6 to 11 reports; fewer malformed ledger rows | none: the prose already defines the rule as deterministic | golden-file test on a fixture of overlapping findings | M |
| **S10** | **Strip trail-file writes, status blocks and `EXECUTION_LOG.md` appends from agent prompts**; the orchestrator logs. Fixes the "status block first" vs "JSON first" contradiction. | 1 to 3 tool turns per agent, times up to 14 + 3k agents | none: nothing reads them | check the S1 log or a transcript: tool calls per auditor drop; JSON parse failures do not rise | S |
| **S11** | **Gate the security CVE scan on `RUN_DEP`** and run it once deterministically. | one slow, networked command per run when no manifest changed | none: DIFF mode is scoped to changed code, and an unchanged manifest has no changed CVE surface | count runs where SEC reported a CVE with no manifest in the diff; prediction: zero | S |
| **S12** | **Split the pipeline file**: a lean core (Contract, tier table, schema, about 8 KB) injected at invocation, one file per step read on demand. | 49 KB injected becomes about 8 KB; each re-read becomes one exact whole-file Read of 1 to 7 KB | low, but it changes the mechanism the anti-drift rule relies on; the guard still enforces the bold layers | prediction: orchestrator input tokens on a fixed diff fall by at least 20% (S1 tokens), and the guard refuses no more runs than before. Codex projector must ship the same split | M |
| **S13** | **Delete `fix-planner`** from the shipped agents (never dispatched). | one fewer installed agent description in every session's agent list | none | `git grep` shows no dispatch | S |
| **S14** | **Add a timeout to `_run_one`** and run proofs in a temporary copy. | bounds a worst case that is currently unbounded | removes a hazard (pre-fix code left in the tree on hang) | test with a sleeping test node: verdict INCONCLUSIVE within the timeout, tree restored | S |

### Needs an A/B on the benchmark first

The benchmark today exercises only bug and numeric auditors on whole files with a non-shipped
fp-check prompt. **Prerequisite A0**: make `audit_workflow.js` drive the shipped prompts (the real
`cca-fp-check.md` semantics, UNCERTAIN as its own outcome, the 2-of-3 panel, and the other auditors),
record `drop_reason`, and log tokens and wall time per agent label. Without A0, none of the rows
below can be decided on evidence.

| # | Change | Expected gain | Quality risk | Falsifiable prediction to pre-register | Effort |
|---|---|---|---|---|---|
| **A0** | Benchmark the shipped pipeline (above). | none; enables the rest | none | n/a (instrument) | M |
| **A1** | **Tighten the tier regex**: changed lines only, identifiers rather than English words, size checked before content for diffs under 10 lines. | the largest single cost lever: DEEP share falls (44/60 → 33/60 from the changed-lines change alone on this repo) | lost recall on a real money-path diff routed to STANDARD | on a seeded corpus of diffs labelled high-stakes by hand: tightened selector misses **0** labelled diffs while reducing DEEP by at least 25% relative. One miss falsifies | S |
| **A2** | **Batch the panel per skeptic**: three skeptics each judge all k findings, instead of 3k agents. | 3k → 3 agents on DEEP, for k ≥ 2 | anchoring across findings; attention dilution | on a fixed set of high-stakes P1s with known truth, per-finding verdicts agree with the unbatched panel on at least 90%, and no ground-truth bug moves from CONFIRMED or UNCERTAIN to FALSE_POSITIVE | S |
| **A3** | **Auditors emit `claim_type` (and `sink_class`)**, the orchestrator runs `check-batch` before fp-check, and only `semantic`, UNCERTAIN, `crash_impact` and `numeric` reach the LLM. This is v3-design §3.1, deferred since v3.0-min. | fp-check sees fewer findings; its prompt can shrink | **material**: today a separate agent classifies, independent of the generator. If the generator misclassifies, a tool FALSE_POSITIVE is **binding** under the no-overturn rule. Mitigation: a pre-run FALSE_POSITIVE is advisory until fp-check agrees with the classification; only CONFIRMED with an artifact skips the LLM, and even then criteria (b) and (c) still need a judgement | wrong-drop count on ground truth does not rise (0 increase allowed), and fp-check calls per run fall by at least 30% | M |
| **A4** | **Split the fp-check prompt**: core plus numeric and Rust appendices loaded only when the finding prefix or `capabilities` needs them. | 44% of 20,976 bytes per fp-check and per skeptic | the agent might not load an appendix it needs | verdicts identical on the numeric, substrate, taint and trap acceptance suites driven through the agent, not only through the package | S |
| **A5** | **Cheaper model for doc, code-remainder, env, perf, dep**; strongest model kept for bug, security, NUM, STAKES, fp-check, architect. Optionally a different model family for one of the three skeptics, which also serves v3-design's decorrelation goal. | those auditors are 5 of the 6 to 11 L1 agents | lower recall in low-stakes categories; their findings are mostly P2/P3 | per-auditor CONFIRMED P1/P2 count over the same N runs falls by no more than 10%, and the scorecard precision for those cells does not fall below 0.5 | S |
| **A6** | **Merge overlapping auditors**: code remainder + doc into one quality auditor; STAKES and DAT as slim prompts that do not inherit the generic bug scope; GRANT owned by one auditor. | 1 to 2 agents per run, fewer duplicate findings into fp-check | fewer independent looks | raw unique findings at P1/P2 per run do not fall by more than 5%; duplicate findings merged at L2 fall | M |
| **A7** | **Run L6 beside L5.5** once L5.6 runs in a copy. | one agent of wall time in the clean case | a wasted L6 when L5.5 sends work back | from the S1 log: if L5.5 is all SAFE in at least 80% of runs, parallel wins on expected wall time; below that, keep sequential | S |
| **A8** | **Inline the diff with function context** (`git diff -W`) into one shared prompt prefix with the schema, role scope **last**, one generic auditor system prompt. This is the only arrangement under which prompt caching can share a prefix across auditors. | removes N independent diff reads; cache reads at about a tenth of input price for auditors 2 to N | auditors may skip callers outside the hunk; whether the Agent tool shares cache across concurrently launched subagents is unverified here | measure cache-read tokens per auditor with S1; recall on A0 within 1 bug of baseline | M |
| **A9** | **Shard fp-check by file in parallel** for runs with many findings. | wall clock, at the price of more agents | inconsistency across shards on cross-file mitigations | only worth it if S1 shows L2.5 is the wall-clock bottleneck; prediction: L2.5 duration > 40% of run wall time on DEEP | S |

### Ideas evaluated and not recommended as stated

* **"Batch fp-check over many findings per call"**: already done for the non-high-stakes path
  (one agent over the list). The remaining per-finding cost is the panel; see A2.
* **"Parallelise L5.5 and L5.6"**: unsafe today, because L5.6 copies the pre-fix snapshot over the
  working tree while L5.5 reads it. The gain is also small (L5.6 takes seconds). S6 plus S14 capture
  the value without the race.
* **"Diff-scoped context instead of whole files"** in HUNT mode: HUNT has no diff by definition, and
  the benchmark's misses were on very large files (black/7, about 3k lines), which argues for more
  context there, not less. Only A8 for DIFF mode.

## 4. Defects and inconsistencies

Ordered by how much they matter.

1. **`pipeline close` verifies nothing.** `pipeline_state.close()` appends `COMPLETE` and deletes the
   state file without calling `verify_commit_allowed`; the CLI help says "retire a run that passed
   its gates". A run missing L2.5 or L6 can be closed and logged as COMPLETE, after which commits
   are unguarded. Fix: refuse unless allowed or `fixes_applied == 0`.
2. **`--status n/a` satisfies any required layer.** The guard blocks only `skipped` and `failed`; the
   CLI accepts `n/a`. Conversely, the prose says L2.5 is skipped when there are no P1/P2 and rule 3
   says a skipped gate must render differently, but recording `skipped` blocks the commit. The status
   vocabulary contradicts itself.
3. **The guard checks presence, not recency.** After an L6 REVISE the prose requires L5 and L5.5
   again, but an L5.5 record from before the re-fix still satisfies the guard. Fix: record a hash of
   `git diff` at L5, L5.5, L5.6 and L6; the guard requires all four to equal the hash at commit time.
   Deterministic and cheap.
4. **`no-fix` runs and zero-fix runs are never closed.** Line 555 says "If `no-fix`: STOP here"
   before the L2.5 record call, and `close` appears only in Step 7 after a commit. The run state
   stays open and the guard refuses the user's next unrelated commit, so every such run ends as an
   ABORTED entry.
5. **FAST and L2.5 contradict each other.** Step 2.5's heading says "STANDARD/DEEP" and its text says
   "FAST tier skips this layer"; the tier table says FAST runs a P1-only fp-check, the paragraph
   under it says no finding is ever fixed unverified "including on FAST", the Contract and the guard
   require L2.5 on every tier, and `pipeline-diagram.md` says "FAST verifies P1 only".
6. **L5.6 is pytest-only** while Rust is a supported backend. A Rust P1 can never obtain a RED proof,
   so the architect must REVISE it and it parks after three cycles: maximum loop cost, zero fixes.
   No timeout on the pytest subprocess either.
7. **The benchmark does not test the shipped gate.** Its fp-check prompt defaults to dropping; the
   shipped one defaults to UNCERTAIN. Its findings schema lacks `auditor`, `category`, `priority`,
   so per-auditor attribution is impossible. Public quality claims about the gate rest on the
   benchmark variant.
8. **L2.6 cannot do what it documents.** It runs after L2.5, so `route_up` cannot affect the current
   run, and its example (extra fp-check on FAST) is unreachable because L2.6 does not run on FAST.
9. **Numeric `properties` may not survive L2.** `cca-numeric-auditor.md` says they may travel "in the
   prose immediately following the array"; Step 2's output table and Step 2.5's fp-check prompt do
   not carry them; the DEEP NUM P1 rule depends on them. Loss fails safe (UNCERTAIN, to a human) but
   costs recall and human time.
10. **"Non-overlapping scopes" is not true of the prompts.** Overlaps: Rust `.unwrap()` (code, bug);
    missing type hints (code, doc); magic numbers (code, doc "cross-reference", env "hardcoded
    values"); blocking calls in async and connection leaks (bug, perf); GRANT in migrations (DAT,
    DEPLOY, acknowledged); STAKES and DAT inherit the whole bug-auditor scope. Combined with a dedup
    key that includes `category`, cross-auditor duplicates reach fp-check.
11. **Agent output contract contradiction**: "Every output MUST start with" a status block vs "JSON
    array as the FIRST thing in your reply" in bug, code, security, perf, doc, env, dep, numeric.
12. **Stale size claim**: `pipeline_state.py` docstring says the steps are "~28KB"; the file is
    49,341 bytes.
13. **`docs/pipeline-diagram.md` is behind the prose**: no L2.6, no L5.6, the REVISE arrow goes to
    Step 4 alone (prose: L5 then L5.5), "max 3 iterations" (prose: 3 per finding, shared across
    gates), and the Language gate described as "transpiled / generated output" whereas the prose
    rejects any file whose `capabilities` language is null (so no HUNT on TS or Go).
14. **Output Summary template** omits the L2.6 and L5.6 rows, which conflicts with rule 3 ("never
    report a layer you did not run" and render skipped and passed differently).
15. **Unreachable tier table cells**: STAKES and NUM "if flag set" on STANDARD (any such flag forces
    DEEP).
16. **Architect "has NO write tools"** (agent file, diagram doc) while its tool list includes Bash.
17. **`fix-planner` and `FIXES.md`**: shipped, listed in `docs/configuration.md` output table, read
    "if present" by the architect, produced by nothing.
18. **L5.6 snapshot scope**: files first edited by an L5 repair are not in the snapshot manifest.

## 5. What to do first

1. S1 (instrument), then S2, S3, S4, S6, S10, S14 and defects 1 to 4: all small, none trades quality.
2. A0, so the benchmark measures the pipeline that ships.
3. Pre-register and run A1 (tier regex) and A2 (panel batching): the two largest agent-count levers.
4. Only then A3 and A8, which change where evidence comes from and carry the real quality risk.
