# Changelog

All notable changes to `cca_checks` — the deterministic verification layer behind CCA-Audit's
`fp-check` gate — are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); versions are the `cca_checks` package
version from `pyproject.toml`, not the surrounding agent-prompt tooling (which is unversioned).
Dates and content are sourced from `git log` and `docs/v3-design.md` §7 — nothing here is invented.

## [Unreleased]

### Added
- The bug ledger now grows by itself. `pipeline close` saves a run that fixed something to `~/.claude/cca-bugs/pending/<run_id>__<commit>/` (its L2, L2.5, L4 and L5.6 files, its scorecard rows and its summary), outside the worktree, so deleting the worktree loses nothing. A failure to save only warns; it never refuses the close.
- `python -m cca_checks bugs resolve --write` asks GitHub, through `gh`, whether the pull request holding the run's commit merged into the default branch. A merged run becomes ledger rows keyed by the merge commit; a record moves to `abandoned/` only when every pull request listing its commit has been closed unmerged for more than 24 hours, so a pull request closed and reopened within a day is not lost; anything else waits. A commit GitHub never received (rebased or amended before the first push) waits rather than failing. A record kept in pending longer than 14 days, whether waiting, unknown or incomplete, is listed as stale. A record is built in a `.tmp-` folder and renamed into place, so no reader sees half of one. Squash merges work because GitHub keeps a pull request's commits. Two runs of one merge keep separate rows, and a rerun appends nothing new. A bug a backfill already recorded under the same merge commit (by the finding id, or by the fix id's number) is skipped and listed under `already_backfilled`, so it is not counted twice.
- A record captured without a GitHub origin (a Codex close on VPS2) is matched by a pull request search for its commit (`is:pr user:<owner>`), once per owner listed in `CCA_BUGS_OWNERS` or, when that is unset, for the `gh` login. GitHub's commit search covers only default branches, which a squash merge never puts the run's own commit on.
- A run closed before its fixes were committed (HEAD at close equals the base commit) is marked `commit_is_base` and reported under `incomplete`, never attributed to the earlier pull request that merged that commit; a pull request merged before the run closed is ignored.
- A record whose `meta.json` is missing or malformed, or whose run files cannot be read, is reported under `incomplete` and left in pending. A finding id that is not a plain identifier gets no row, because it would name a memo file; GitHub's merge sha must be 40 hex characters. `resolve` exits 3 only when `gh` could not answer.
- `cca_tautology_check.py verify --record` writes `runs/<run_id>/L5.6.json`, each proof's verdict by finding. The L5.6 entry holds only counts, so without this file no run could ever be recorded as proved.

## [0.11.0] - 2026-10-12

### Changed
- Every step of `/audit-fix` is now gated, not only L1, L2, L2.5 and L6. A HUNT owes L0.4 PASS; STANDARD and DEEP owe L2.6; every fixing run owes L3; L4 onward is owed whenever the working tree changed, whether or not L4 was recorded. In 0.10.x an unrecorded L4 switched off L5, L5.5 and L5.6.
- `pipeline record` checks order and refuses a layer whose predecessors are missing (exit 2, with the section to re-read). L2, L2.5 and L3 take counts (`--unique`, `--confirmed`, `--fp`, `--uncertain`, `--duplicate`, `--to-fix`) that must agree with each other and with the disposition ledger.
- Verdicts from L5 onward store the tree they judged; an edit after a verdict makes it stale.
- `--status n/a` no longer satisfies a required layer.
- `pipeline close` refuses an incomplete run; `no-fix` runs now close after verification instead of blocking the next commit.
- L2.6 and L5.6 are recorded by their tools (`--record`), never by hand.
- A second `pipeline start` is refused (exit 2, `{"refused": "start"}`) while a run is open, instead of replacing it. Replacing re-baselined the tree over the run's edits, dropped L0.4, and left no trail. Retire a run with `pipeline close` or `pipeline abort --reason`.
- `pipeline record` of L1, L2, L2.5, L3 and L4 requires `--output <file>` on a schema 2 run: a JSON file under `.claude/audits/` (a file elsewhere would change the tree fingerprint, so it is refused) holding that layer's work, copied to `.claude/audits/runs/<run_id>/<layer>.json` and named in the layer entry. Any other layer given `--output` is refused rather than ignored.
- The red-state proof (`cca_tautology_check.py verify`) runs pytest with `PYTHONDONTWRITEBYTECODE=1`. It used to leave `.pyc` files compiled from the pre-fix source, which changed the tree fingerprint after verify and left L5 stale in repositories that do not ignore `__pycache__`.
- The L2.5 file must give every L2 finding exactly one verdict. A finding passed on unverified (P3, and P2 on FAST), counted with `--unverified` (see Fixed in the pre-release audit), is written `DEFERRED` there and gets no ledger row; such findings used to be filed under a verdict they never received.
- L3 must plan or defer every confirmed finding (`--to-fix` plus `--deferred` equals L2.5's confirmed); `--deferred` replaces the free text `--detail "<D> deferred"`.
- `record` checks the L2, L2.5 and L3 files against their counts and against each other, and refuses a file that disagrees.
- `pipeline start --base <rev>` stores the commit the audited diff is taken against (default `HEAD`); `--mode` is validated.
- The scorecard no longer counts `STALE` rows as disposed.
- `pipeline record` and `pipeline status` fingerprint the working tree once per call. Each fingerprint is a `git add -A` over a copy of the index; `record` ran two and `status` ran three. `pipeline_state.required_layers`, `outstanding`, `verify_commit_allowed` and `record` accept a precomputed `tree=` from the new `current_tree()`.
- The `pipeline` subcommand moved from `cca_checks/__main__.py` to `cca_checks/pipeline_cli.py`. The command line is unchanged.
- The installers moved from `cca_checks/plugin/__init__.py` to `cca_checks/plugin/installer.py`. `cca_checks.plugin` still exports the same public names; code that patched a private helper on the package (`plugin._write`) must patch `cca_checks.plugin.installer` instead.
- Every GitHub Action in CI and in the release workflow is pinned to a commit SHA, starting with the step that holds the PyPI token; `tests/test_ci_contract.py` fails on any `uses:` that is not a SHA.
- The README, which is also the PyPI project page, is cut from 521 lines to 181.

### Added
- `CCA_REMOTE_SEMGREP=<ssh destination>` runs semgrep on a remote host when it cannot execute locally (for example under Windows Smart App Control), so `taint` claims are settled instead of escalated. Local semgrep is always tried first. On the host, a forced-command runner (`cca_checks/remote/runner.py`, stdlib only) accepts one capped tar archive with fixed member names and scans under a memory and CPU cap. `capabilities` reports the remote, and remote verdicts name the host in their evidence. A remote "no sink" refutes a taint finding, so the host joins the trust base; setup and the host key check are in `docs/remote-semgrep.md`.
- `cca_stop_guard.py`, a Stop hook that refuses to end the turn while a run is open, three times per run, then logs `STOPPED_INCOMPLETE`. `install --print-hook` now prints both hooks; re-run it and update settings.json.
- `pipeline status --require-complete` for Codex, which has no hooks.
- `pipeline pause --reason` and `pipeline resume`. A run paused right after a `record` can end the session (the Stop hook allows it without spending a refusal) and continue later from the next layer: `resume` prints that layer, its heading, the stored outputs, whether the tree changed while paused, and what the run still owes. While paused, `record`, `close` and `git commit` are refused; `abort` still works. `resume` resets the Stop hook's refusal count, and names L4 next after an L3 that planned fixes. `pipeline start --run-id` accepts only letters, digits, `.`, `_` and `-`, since the run id names a directory.
- `pipeline close` writes `.claude/audits/runs/<run_id>/summary.json`: tier, mode, base and final commit, and the deferred pool.
- `pipeline deferred [--run <id>] [--write <file>]`: what the last completed run deferred, whether a second pass is offered, and RUN or SKIP with reasons; `--write` produces the deferred run's L1 input, and writes only under `.claude/audits/out/`. While any summary is unreadable the report says `offered: false`, since the deferred start refuses then.
- `pipeline start --mode DEFERRED --from <run_id>` (or `--from commit:<sha>`): a deferred pass that owes every layer, refuses FAST, and cannot defer again. `/audit-fix` offers it after a run closes.
- The `STALE` verdict, for deferred runs only: the issue was present in the tree the parent run audited (its `base_tree`, which `pipeline close` now writes into the summary) and is gone now, because later work moved, rewrote or fixed it. `pipeline record L2.5` takes it as `--stale <S>` (a deferred run can use neither `--unverified` at L2.5 nor `--deferred` at L3), the L2.5 file of any other run refuses it, and its ledger row says `STALE`, never `FALSE_POSITIVE`.
- `python -m cca_checks bugs backfill`: a cross-project ledger of the bugs CCA fixed, read from CCA fix commits and the per-project `AUDITOR_SCORECARD.jsonl` files. A bug is `proved` only when it was confirmed at L2.5, landed, approved at L6 and red at L5.6; everything else is `historical`. Ledger evidence applies only through an explicit `--bind SHA=RUN`. Read-only unless `--write`, which appends to `~/.claude/cca-bugs/bugs.jsonl`, outside any repository.
- An architecture diagram (`docs/diagrams/`), light and dark, with an interactive version that links each box to the source lines it describes.
- `docs/limits.md`, the full list of where the verifiers are blind, each with the test that asserts it.
- `fp-check` has a `MODE=DEFERRED`: it verifies every deferred finding, P2 and P3 alike, checks that the cited code lies inside the diff of the run that deferred it, and returns `STALE` for an issue that later work removed. Its status block gained `mode: DEFERRED` and a `stale:` count.

### Removed
- `docs/banner.svg`; the README shows the architecture diagram instead.
- `Claim.predicted_impact`, which nothing read or wrote.
- `pyright_check.verdict_for_definedness`; call `verdict_for_claim(claim, diags, DEFINEDNESS_RULES)`.
- `semgrep_check.SINKS_RULES` and `TAINT_RULES`; the CI packaging job checks every backend's `semgrep_catalog`, and the comment claiming it read these was stale.
- `cca_pipeline_guard.evaluate`, which only a test called.

### Compatibility
- A run started by 0.10.x finishes under 0.10.x rules.
- A run already in flight when this version lands, whose L2, L2.5 or L3 file lacks the new shape, is refused at that `record`, and the refusal names the shape. The execution ledger is append-only, so the run cannot be repaired in place: `pipeline abort --reason` it and start again.
- Upgrading from 0.10.x: run `cca-audit install` first, so `.claude/hooks/` holds `cca_gate_core.py` and `cca_stop_guard.py`, and only then replace BOTH hook entries in settings.json with the output of `cca-audit install --print-hook`. A Stop hook registered before its script is installed exits 2, which refuses every turn end. The commit guard's timeout rose from 15 s to 60 s: it now fingerprints the tree for up to 30 s, and a hook that times out lets the commit through. `install` warns about a guard entry still registered below 60 s, and `--print-hook` warns when the hook files are missing.

### Fixed in the pre-release audit
- `record L2.5` takes `--unverified <N>` for findings Step 2.5 passes on without a verdict (P3, and P2 on FAST); verdicts plus unverified must equal L2's `--unique`. Before, any run with a P3 was refused at L2.5. On STANDARD and DEEP the ledger is tallied per verdict (`STALE` included, for a deferred run), and a row with an unknown verdict is refused.
- An L3 that planned fixes owes L4 onward before the tree changes; L6 could follow L3 directly and the run close COMPLETE with its fixes never applied.
- A fix verdict cannot be shown current when the tree cannot be fingerprinted: `record` refuses L5 to L6 then, and a stored or current unknown fingerprint is stale. An unknown compared equal to an unknown, so every verdict read fresh after any edit.
- The commit guard refuses a commit chained with other commands (`; && || | &` or a newline outside quotes), since it judges the tree before the command runs, and a commit that stages a file whose content differs from its working tree copy, since the commit ships the index. Both checks share the fingerprint's single 30 s deadline.
- `cca_tautology_check.py verify --no-proofs --record` refuses when the ledger holds a CONFIRMED P1 for the run and L3 planned fixes, unless `--parked <N>` declares that many CONFIRMED P1s parked; the count is written into L5.6.
- `pipeline start` refuses a run id already used by an earlier run's outputs or ledger rows.
- `pipeline record` and `pipeline status` fingerprint the tree once per command; the Stop hook skips the fingerprint once its refusals are spent and logged.
- Follow-up audit of those fixes: the commit guard's command scan models `#` comments (literal inside `${...}` and `((...))`; a comment holding a separator or a substitution is refused), and refuses an unterminated quote; it refuses a commit that names another index or repository; the staged check also refuses a staged deletion of a file still on disk, does not strip `-z` path lists, and skips rename detection. An L3 that planned fixes keeps L4 onward owed after `L4 --fixes 0`. `--no-proofs` is checked whenever fixes landed, refuses when the ledger is missing or short of L2.5's confirmed rows on a STANDARD or DEEP run, and bounds `--parked` by the ledger's CONFIRMED P1 count.

### Known gaps (parked to a human by the follow-up audit)
- The commit guard does not inspect command substitution: `git commit -am "$(<edit>)msg"` runs the edit before git and commits it unjudged. Refusing substitution while allowing the heredoc message form took three repair cycles and was parked; the last attempt was defeated on Git Bash, which drops carriage returns before parsing. Suggested start: refuse any command holding `\r`, then refuse substitution except an exact quoted-heredoc message.
- The commit guard reads `$'...'` as a plain single quote, so `: $'\''; <edit>; git commit -am x #'` runs the edit unjudged (the trailing `'` balances the quote count); a spelling with an odd number of quotes is refused as an unterminated quote. Modelling the escapes was parked after three repair cycles, because bash lexes `$$'` as `$$` followed by a plain quote and the attempt hid edits HEAD refused. Suggested start: refuse `\'` inside `$'...'`.
- `--parked N` can over-claim up to the ledger's CONFIRMED P1 count; the count is written into L5.6 for the architect. A tighter bound needs pinned L3/L4 output shapes.

### Fixed
- The benchmark write-up's 40-point memorization gap (83% memorized against 43% novel) is withdrawn. The 95% interval on the difference is [-2.2, +70.0] points (Newcombe) and Fisher exact gives p = 0.129, so the pilot cannot tell the gap from zero; the two arms also come from different corpora. What stands is the contamination measurement: BugsInPy came back 12/12 recognized. `benchmarks/harness/interval.py` recomputes every figure from the counts, and a test refuses any document that quotes 83 and 43 without the retraction.
- A stale project copy of the pipeline no longer runs silently. `/audit-fix` ran its two tools as `.claude/tools/<tool>.py` when that file existed, so a gitignored `.claude/` left by an older install ran months-old tools while the agent followed the new prose, and nothing said so. The tools now run from the installed package as `python -m cca_checks scorecard` and `python -m cca_checks tautology`, which pass their arguments and exit code through. `pipeline start` adds a `"warnings"` list to its output (and prints each on stderr) naming every installed `audit-fix.md`, tool or hook, in the project's or the user's `.claude/`, that differs from the package's copy, with the `cca-audit install --target <dir>` that refreshes it. It never refuses.
- A run opened in a worktree below the session's directory is now enforced. Both hooks looked for the run from the session's directory, so the Stop hook never blocked and `cd <worktree> && git commit` passed the commit guard. `pipeline start` now records each open run in `~/.claude/cca/open-runs.json` (or the file `CCA_RUN_REGISTRY` names) and `close` and `abort` remove it; the Stop hook also judges every registered run at or below the session's directory, naming its root; and the commit guard looks from the directory the commit runs in, following leading `cd <dir>` and `git -C <dir>`. A run in another checkout stays out of scope.
- The Codex skill keeps at most six subagents open, Codex's default `agents.max_threads`. A finished subagent holds its slot until `close_agent`, so the skill now records and closes each one before spawning the next queued role. A DEEP run used to launch every auditor at once and die on `agent thread limit reached`.

## [0.10.1] - 2026-08-31

- **`pip install 'cca-audit[verify]'` no longer rewrites unrelated packages.** semgrep hard-pins
  `mcp==1.29.0`, `ruamel.yaml.clib` and `pywin32`, so installing the verification layer silently
  downgraded a user's `mcp` from 2.1.0 and broke unrelated MCP tooling. `cca_checks` never imports
  semgrep: it resolves it on PATH and spawns it, exactly like `cargo`, so it was never a library
  dependency in the first place. It moves to its own `taint` extra, with `pipx install semgrep` as
  the recommended install. `verify` now resolves to 15 packages instead of 68.
- **`capabilities` probes tools by running them, not by finding them.** Availability was
  `resolve_tool(name) is not None`, which answers whether a file is on PATH. On a machine where
  semgrep resolved fine and then died with `OSError: [WinError 4551] An Application Control policy
  has blocked this file`, `capabilities` reported `taint` fully available while every taint claim
  escalated. A coverage report that overstates coverage is worse than none, because its specificity
  is what makes it trusted. Each tool is now probed with `--version`, and a non-zero exit counts as
  unavailable too: in that failure the `OSError` was raised inside the tool's own process, so an
  exception check alone would have reproduced the bug.

## [0.10.0] - 2026-08-31

- **The audit pipeline's layers are now enforced rather than requested.** Every layer of
  `/audit-fix` was gated by prose alone: the command file said "This is a DETERMINISTIC workflow"
  and nothing checked that it had been followed, so a run that skipped L2.5 (findings verification)
  or L6 (the architect gate) produced a commit indistinguishable from a fully verified one, with
  the same message shape, file list and claimed provenance. `python -m cca_checks pipeline` records
  which layers a run reached, and a new `PreToolUse` guard refuses `git commit` until they carry
  verdicts and L6 reads `APPROVED`. It fails closed only while a run is open, so it is invisible to
  every commit that is not part of an audit, and the escape hatch (`pipeline abort --reason`)
  appends to `EXECUTION_LOG.md`, so a skip that used to be silent now leaves a dated trail. FAST is
  not asked for the layers its own tier table does not run.
- **The installer ships the guard, and says so when it is not armed.** `cca-audit install` now
  writes `.claude/hooks/` alongside the agents, commands and checkers, and warns when the guard is
  installed but named by no settings.json, because copying it into place does not arm it.
  `cca-audit install --print-hook` emits the settings fragment instead of editing anyone's
  configuration. An install that reported success while the layers stayed advisory would fail in
  exactly the silent way the guard exists to end.
- **The pipeline re-anchors itself mid-run.** `audit-fix.md` gains a resident checklist, a record
  call at the end of every step, and an explicit instruction to re-read the next sections at the
  two points where a long run drifts: after the auditor fan-out, and after Layer 4's tests go
  green. It also states that general-purpose "no further review passes" guidance does not apply
  inside an audit, which is by construction a sequence of review passes over already-passing code.
- **Tightened the package description and recorded the differential oracle's origin.** The PyPI
  project page is frozen at upload time, so the description change reaches it with this release
  rather than when it merged. `cca_checks/substrate.py` now credits the two-arithmetic approach to
  Erik Hill, who suggested it in public review.

## [0.9.0] - 2026-08-04

- **CCA now runs natively inside Codex tasks.** `cca-audit install-codex` installs an `audit+fix`
  skill that maps the canonical pipeline to Codex subagents. The installer projects the existing
  pipeline, auditor prompts, and checker scripts into the skill instead of maintaining a second
  copy of the audit contract.
- **The scalar differential oracle now fails closed on hidden precision loss and state.** A
  temporary provenance hook detects native conversion of an `mpf`, including conversion inside an
  unpatched helper whose float result is later re-promoted. A float64 replay around the reference
  call prevents a stateful target from manufacturing a binding `CONFIRMED`. Both conditions return
  deterministic `UNCERTAIN` evidence. The supported contract remains synchronous, pure,
  deterministic scalar-real functions.
- **CI type-checks again.** `pyright` was named as the verification contract but no workflow step
  ran it, so a type regression in the checkers themselves merged green; the step is restored and
  `tests/test_ci_contract.py` now fails if it is removed again. The eight type errors it had been
  silently accumulating are fixed with it.
- **The Codex install path was hardened against its own self-audit.** Six confirmed findings:
  untrusted hunt execution is gated, the install is transactional (staged, then swapped, with
  rollback), link and reparse-point redirects under the skill tree are rejected, every required
  agent prompt must be present or the install refuses, and a Codex install failure is reported
  rather than raised as a bare `OSError`.

## [0.8.1] - 2026-07-24

- **Dropped two hardcoded figures that nothing keeps current.** The banner
  (`docs/banner.svg`) carried `v0.6.0` as static SVG text and the README carried a
  `tests-613 passing` shields.io badge; no release step touched either, so the version had already
  drifted two minors behind the published package. Neither is replaced: the version lives in
  `pyproject.toml` and on the PyPI page, and the CI badge already reports whether the suite is
  green. This release exists mainly to re-upload the description — the PyPI project page is frozen
  at upload time, so the stale banner and badge stayed visible on 0.8.0 after they left master.

## [0.8.0] - 2026-07-24

- **Corrected three stale figures in the README**, which is also the PyPI project description.
  The tests badge said 610 while the Engineering table said 378 — the same fact stated twice, in
  disagreement, on the published page. The intro said "Ten specialised auditors" while the Core and
  Conditional tables listed eleven (Deployability was added and the prose was not). The design of
  record was described as "v3.0 → v3.5" though `docs/v3-design.md` documents through v3.6.
- **`tests/test_readme_counts_are_consistent.py`** so those two counts cannot silently diverge
  again. It asserts internal consistency, not correctness: the true test count depends on
  collection and the auditor set is defined by prompt files, so neither is checkable against ground
  truth from a test — but "the page must not contradict itself" is, and that is exactly what
  failed. Both checks were confirmed red against the pre-fix README and green after, and a third
  test exercises the detection path so a regex that stopped matching cannot leave them green on a
  self-contradicting page.

- **The pipeline checkers moved into the repo and now ship.** `cca_scorecard.py` (Step 2.6) and
  `cca_tautology_check.py` (Step 5.6) lived in a separate private repository and reached
  `~/.claude/tools/` only by a hand-run `cp`. They are now package data under
  `cca_checks/plugin/tools/`, alongside the agents and commands, and both installers copy them.
  **This fixes a silent shipped failure:** the installers wrote agents and commands but no tools,
  while `audit-fix.md` referenced `$HOME/.claude/tools/cca_*.py` — paths nothing in the install
  path ever created. On every machine but the author's, Steps 2.6 and 5.6 were `command not
  found`, and the pipeline reported no scorecard and no red-state proof without saying why. The
  call sites now resolve `.claude/tools/` first and fall back to `$HOME/.claude/tools/`.

- **`resolve_tool` launches analyzers by the path found, not its `realpath`.** Resolving the
  symlink breaks every multi-call binary — the ones that dispatch on `argv[0]` rather than on
  their own inode. `~/.cargo/bin/cargo` is a symlink to `rustup`, so the resolved launch ran
  rustup with cargo's arguments (`error: unexpected argument '--manifest-path'`) and 9 Rust tests
  failed in CI. It passed locally only because Windows `cargo.EXE` is a real binary. In
  production the failure would have been quieter than a red suite: every Rust claim escalating to
  `UNCERTAIN` for a reason unrelated to the code under audit.

  The same change closes a second hole. The old check tested only `dirname(realpath(found))`, so
  a symlink **planted in the audited repo root** and pointed at a genuine system binary was
  accepted — and whoever controls the repo controls where it points next. Both the link's own
  location and its target are now disqualifying.

  `resolve_tool` had no direct coverage at all — every caller monkeypatches it away, which is how
  both defects shipped. `tests/test_toolpath.py` is new, and its two regression tests were
  confirmed red against the previous implementation.

- **`audit-fix.md` reconciled.** The deployed prompt and the repo copy had each gained changes the
  other lacked, so the orchestrator — where the L2.5 gate semantics live — was versioned nowhere
  current. Merged three-way against master with no conflicts, purely additive.

- **v3.3 — the language backend layer, and Rust on it.** `cca_checks/languages/` resolves a backend
  by file extension **once**, before any checker runs. An uncovered language, or a claim type the
  covering backend does not declare, returns `UNCERTAIN` — it can no longer reach a checker whose
  silence would be read as evidence.

  **This closed a live defect.** The Python-only assumption was enforced in two of the five claim
  types: `clock_check` and `semgrep_check` tested `.endswith(".py")`, `type`/`nullability` failed
  closed only by accident (the blindness probe escalates when `ast.parse` chokes on non-Python), and
  `definedness` — exempt from that probe by `TYPE_DEPENDENT_CLAIMS` — had no guard at all. pyright
  parses a `.rs` file as Python, reports nothing under `DEFINEDNESS_RULES`, and
  `verdict_for_claim` fell through to a confident `FALSE_POSITIVE` carrying `source: pyright`. That
  artifact may not be overturned downstream, so a real defect was dropped and the file closed on it.

- **Rust claim types, chosen for Rust rather than ported from Python.** `definedness`, `type` and
  `nullability` are deliberately absent: the code compiled, so they would refute by construction.
  Shipped instead:

  | claim | settler | confirms? |
  |---|---|---|
  | `clock_leak` | tree-sitter | yes — a dead strong clock parameter beside a wall-clock read |
  | `overflow` | clippy | yes — the lint fires on the defect itself |
  | `error_swallow` | clippy | yes |
  | `panic_path` | clippy | **no** — a lint sees the construct, not its reachability |
  | `unsafe_op` | clippy | **no** |
  | `taint` | semgrep + a Rust catalog | **no** (as for Python) |

- **clippy is the analogue of pyright, not of `ast`, and its blindness is different.** Never
  type-blind (the crate compiled), but **lint**-blind: every lint used is allow-by-default. They are
  force-enabled with `--force-warn`, which overrides the crate's own `#![allow]`, `clippy.toml` and
  `[lints]` table — the direct analogue of `enableTypeIgnoreComments: false`. Cargo **freshness** is
  the analogue of `summary.filesAnalyzed`: a dedicated target directory (never the audited crate's)
  plus a `build-finished` assertion, so a build that reported nothing because it did nothing cannot
  read as a clean crate.

- **A parse control for the Rust sink catalog.** semgrep's Rust support is younger than its Python
  support and a refutation rests on its silence; a file it failed to parse is scanned, reported
  without errors, and matches nothing — indistinguishable from a file with no sinks. `rust_sinks.yaml`
  ships a `parse-control` rule that must match any file containing a function; if it does not fire,
  the checker escalates instead of refuting. Opt-in per catalog, so Python behaviour is unchanged.

- **A Rust repro runner** (`cargo_repro.py`), which is the only way `panic_path` can be CONFIRMED at
  all — clippy sees the construct, not its reachability. `python -m cca_checks repro --test
  <crate>/tests/t_<ID>.rs --expect-error "<panic message>"` builds with `--no-run` **first** and
  escalates if the crate does not compile, so a build failure cannot be read as a reproduction:
  `cargo test` exits non-zero for both, and confirmation only requires the predicted string to
  appear in the output. `repro` now dispatches on the test file's extension, and an unrecognised one
  escalates rather than defaulting to pytest.

- **`python -m cca_checks capabilities --file <F>`** reports which claim types can be settled about a
  file *on this machine*, and names the tool missing for any that cannot. `cca-fp-check.md` and
  `audit-fix.md` now ask it rather than carrying a routing table that drifts from the package.

- **New optional extra `[rust]`** (`tree-sitter`, `tree-sitter-rust`), also in `[verify]` and
  `[dev]`. The grammar is the *parser* only — clippy belongs to the target's toolchain and cannot be
  pip-installed — so `clock_leak` and span resolution keep working on a crate that does not build.
  New knob `CCA_RUST_TIMEOUT_S` (default 600): a cold cargo build routinely exceeds `CCA_TIMEOUT_S`.

- Three defects the fixtures caught in this code, all failing safe and therefore silent: the trailing
  segment of a scoped path counted as a use of a same-named parameter (making `CONFIRMED` unreachable
  for `fn f(now) { Utc::now() }`); cargo span paths are relative to the manifest, not to cwd, so every
  diagnostic was unplaceable; and a sibling module's diagnostic was read as "unlocatable", which made
  one `unwrap` anywhere in a crate block every refutation in every file.

- Python behaviour is unchanged. The existing suite passes with no assertion edited; the patch targets
  in `test_cli.py` and `test_selfaudit_hardening.py` follow the dispatch from `__main__` to the
  backend module.

## [0.7.1] - 2026-07-24

- **Fixed the PyPI project page: every relative link and image in the README is now absolute.**
  0.7.0's page shipped a **broken banner image** and **15 links returning 404**. GitHub resolves a
  relative path against the repository; PyPI renders the same markdown standalone, so
  `docs/banner.svg` and `docs/v3-design.md` resolved against `pypi.org` and did not exist. Verified
  against the live page: `https://pypi.org/project/cca-audit/LICENSE` → 404,
  `.../docs/v3-design.md` → 404, `.../examples/sign-trap/` → 404, and the banner `<img>` reported
  `naturalWidth == 0`. Dead links covered LICENSE, CHANGELOG, CONTRIBUTING, SECURITY, all six
  `docs/` pages and the `examples/sign-trap` worked example the README cites as proof.
- **Why this needed a version bump at all:** PyPI freezes a release's description at upload, so a
  project page can only be repaired by publishing a new version. 0.7.0 is unfixable in place. This
  is the same defect class that cost `recall-rag` a release.
- **Added `tests/test_readme_urls_are_absolute.py` so it cannot recur.** It asserts the invariant
  ("no relative reference anywhere in README.md") rather than a list of today's known-bad paths, so
  a link added next month is covered without anyone remembering the test exists. In-page anchors
  (`#section`) stay exempt — they resolve correctly on both hosts. A second test exercises the
  detection path against a known-bad sample, because a regex that silently stopped matching would
  leave the guard green on a broken README — a check that verifies nothing.
- Nothing in the installed package changed: same modules, same agents, same console script. 0.7.1
  is a documentation-metadata release.

## [0.7.0] - 2026-07-24

- **Published to PyPI as `cca-audit`** — the first release installable with `pip`. The distribution
  was renamed from `cca_checks` because the *product* is the auditor agents and the `/audit-fix`
  orchestrator, not the verification helpers they shell out to; a PyPI page named after the helper
  would have advertised a tool you could not install from it. **The import name is unchanged**
  (`python -m cca_checks` still works, and every agent prompt that invokes it is untouched).
  Nothing was ever published under `cca_checks`, so there is no migration.
- **New `cca-audit install` console script.** `pip install cca-audit && cca-audit install` installs
  the whole plugin into `<target>/.claude/`, replacing `curl … | bash` as the primary path — piping
  a network fetch into a shell is an install step a large share of developers decline outright, and
  that refusal is invisible. It mirrors the shell installer: customized files are preserved as
  `<name>.md.bak` before being replaced, backups happen only when content actually differs, and a
  pre-existing agent whose frontmatter `name:` collides with one we dispatch is reported rather
  than silently shadowed.
- **The agent and command markdown moved into the package**, from `claude-code/{agents,commands}/`
  to `cca_checks/plugin/{agents,commands}/`. A wheel can only carry package data, so this is the
  only location both install paths can serve from — one copy on disk, so the two cannot drift.
  `claude-code/install.sh` and `install.ps1` read from the new location and behave as before.
- **The wheel is now checked for the markdown, not just for imports.** A wheel missing the plugin
  files installs cleanly, imports cleanly, and `cca-audit install` exits 0 having written an empty
  `.claude/` — a total silent failure the previous import-only smoke test could not see. CI installs
  the built wheel into a clean venv, runs the console script against a scratch project, and asserts
  every agent in the source tree arrived; `tests/test_plugin_install.py` asserts the same invariant
  through `importlib.resources`.
- **The shell installer falls back to PyPI.** Under Git Bash on Windows `$REPO_ROOT` is an MSYS path
  (`/c/Users/…`) that pip rejects as a requirement, which silently downgraded the install to
  LLM-only verification. There is now a published source that always parses.
- Fixed a stale README badge: the test count read 306 against an actual 376.

## [0.6.1] - 2026-07-24

- **Second field result, merged upstream** (docs only — no `cca_checks` change). Hunt mode on
  `scipy/scipy` found a copy-paste defect in `signal.decimate`'s complex-coefficient guard
  (`system.poles` tested twice, `system.zeros` never), which crashed valid `dlti` filters with real
  poles and complex zeros and had been latent since gh-17881 in April 2023. Fix and regression test
  merged as [scipy/scipy#25654](https://github.com/scipy/scipy/pull/25654) on 2026-07-23. README's
  "Verified in the field" now carries both this and the Polymarket py-sdk result.

## [0.6.0] - 2026-07-23

- **v3.6 — `clock_leak` claims** (2026-07-22, PR #29). A new claim type for "code that should run
  on injected time reads the wall clock anyway" — the defect that passes review because both
  halves look correct in isolation, and only misbehaves once simulated and real time diverge.
  Settled from the syntax tree by `cca_checks/clock_check.py`, through import aliases, with no
  external tool and no new dependency. `CONFIRMED` needs a *dead parameter*: a STRONG
  injected-time parameter (`CCA_CLOCK_STRONG_PARAMS`) never referenced in the scope **and** a
  real wall-clock read — mere co-occurrence of a clock parameter and a `datetime.now()` is
  `UNCERTAIN`, since stamping an audit log with real time while the logic runs on `as_of` is
  correct code. `FALSE_POSITIVE` only on an absence the file cannot have hidden, so a
  `from x import *` or a clock function named but never called blocks refutation.

## [0.5.0] - 2026-07-22

- **v3.5 — substrate-differential checks** (2026-07-22, PR #21). Adds `assert_substrate_agrees`,
  a seventh property helper that runs a numeric target twice — once at float64, once against a
  50-digit `mpmath` reference via `substrate.py` — and confirms only when the two disagree
  beyond a tolerance. Unlike the `v3.4` property helpers, no relation is authored by the finding's
  own agent, so it is decorrelated on *evaluation* (catches cancellation/accumulation/rounding
  defects the property vocabulary can't) but not on *transcription* (a flipped sign or wrong term
  survives into both substrates identically — that class stays with the property helpers).
  Requires the `numeric` or `verify` extra (`mpmath>=1.3`); absent → `UNCERTAIN`, never a silent
  pass. See `docs/superpowers/specs/2026-07-21-substrate-differential-design.md`.
- **Self-audit of v3.5** (2026-07-22, PR #22 — `fix/cca-self-audit-substrate`). CCA's own DEEP
  pipeline run against the substrate feature found two P1s, both reproduced by execution:
  - The `dev` extra — the only extra CI's install step uses — was missing `mpmath`, so both
    `tests/test_substrate.py` and `tests/acceptance/test_substrate_suite.py` silently skipped
    via `pytest.importorskip("mpmath")` on every CI run, on every Python version: ~25 tests,
    including the integrity gate that is this feature's central safety guarantee. Fixed by
    adding `mpmath>=1.3` to `dev`; a follow-up test now asserts the invariant (every
    module-scope `importorskip` name under `tests/` must appear in `dev`) so the class can't
    silently recur with a future optional dependency.
  - `SUBSTRATE_TOL` had no floor or ceiling, unlike `SUBSTRATE_DPS`. An override as small as
    `1e-20` turned ordinary float64 noise (~8.3e-18) into a `CONFIRMED` violation against
    *correct* code; an override large enough made the check permanently vacuous. Bounded to
    `[1e-15, 1.0]` in `config.py`.
  - Also fixed: asymmetric non-finite handling in `assert_substrate_agrees` (only `observed`
    was checked, so a non-finite `reference` against a finite `observed` produced `NaN >
    SUBSTRATE_TOL == False` — silently passing a genuine unbounded divergence), and a P2 where
    `properties.py` re-exported `assert_substrate_agrees` but not `SUBSTRATE_TOL`, contradicting
    the design spec.

## [0.4.0] - 2026-07-21

- **v3.4 — `numeric` claims via metamorphic properties** (PR #16). Settles arithmetic defects —
  wrong sign, mixed units, bad scaling — by running auditor-declared properties
  (`assert_bounded`, `assert_monotonic_in`, `assert_limit`, `assert_scale_invariant`,
  `assert_sign_symmetric`, `assert_round_trips`) under Hypothesis. A violated property yields a
  concrete falsifying example (`CONFIRMED`); properties holding across a bounded search is never
  treated as proof of correctness, so this checker can never emit `FALSE_POSITIVE` — the mirror
  image of the taint checker's asymmetry. `hypothesis` is an optional `[numeric]` extra;
  without it, every numeric claim escalates to `UNCERTAIN` rather than silently passing. On the
  DEEP tier, a `NUM-*` P1 cannot enter the fix plan without a Hypothesis artifact — a hard stop,
  not a graceful degradation, because a sign error reads fluently and a second LLM opinion isn't
  evidence. See `docs/superpowers/blog-fluency-isnt-evidence.md` and
  `docs/superpowers/specs/2026-07-21-numeric-differential-oracle-design.md`.
- **Self-audit of the deterministic layer (DEEP tier), 21 fixes** (PRs #18 → #19 → #20, all
  2026-07-22). CCA-Audit run against its own verification layer in hunt mode. Highlights:
  - `SEC-001`: `pyright`/`semgrep` were launched by bare name while `cwd` is the audited repo;
    on Windows, `CreateProcess` resolves `argv[0]` against the current directory *before* `PATH`,
    so a repo shipping a same-named binary in its root got code execution on the auditor's
    machine. Fixed with an explicit `PATH`-only resolver (`cca_checks/toolpath.py`) that refuses
    a binary planted in the audit root.
  - `SEC-003`/`STAKES-002`: `pyright` inherited the audited repo's own config, so
    `typeCheckingMode: off` or a stray `# type: ignore` silenced findings and the silence read
    as `FALSE_POSITIVE`. Both passes now run under a generated config that ignores the target's
    own type-ignore comments.
  - `SEC-004`: `semgrep` honored the audited repo's `# nosemgrep` and `.gitignore`; added
    `--disable-nosem`/`--no-git-ignore`.
  - Several `NUM-*`/`BUG-*` fixes where the harness's own perturbation, not the target code,
    produced the "violation" (e.g. `assert_round_trips` falsifying a correct quantizing
    converter; a scale-invariance probe pushing outside float64 range and blaming the target).
  - `ENV-018`/`DOC-003`: neither installer passed the `[numeric]` extra by default, so a fresh
    install silently ran with every numeric claim escalating to `UNCERTAIN`. Fixed at install
    time, plus a same-PR fix (`cbf1f18`) resolving contradictions between `audit-fix.md`'s
    high-stakes escalation rule and `cca-fp-check.md`'s "any tool artifact cannot be overturned"
    rule.
  - `NUM-003`: two independent regexes could pair one violation's shrunk input with a *different*
    violation's property-name line; ambiguous output now escalates instead of guessing.
  - CI updated (`cb934e4`) to run the suite across Python 3.10–3.13 plus a job that builds the
    wheel and installs it into a clean venv, so a module missing from the packaged wheel is
    caught in CI rather than surfacing only for a user who installs it.

## [0.3.0] - 2026-07-10

- **v3.2 — `taint` claims via semgrep.** A bundled two-tier sink catalog backs the `taint` claim
  type. Semgrep can refute a false premise (flags a genuinely parameterized query as safe) and
  inform adjudication, but never confirms on its own — a hit is evidence, not proof. See
  `docs/superpowers/specs/2026-07-10-v3.2-taint-semgrep-design.md`.

## [0.2.0] - 2026-07-10

- **v3.1 — `type` and `nullability` claims via `pyright`,** routed through the same deterministic
  mechanism as v3.0-min's `definedness` claim, plus a blindness probe so a refutation is only
  issued when `pyright` actually had type information in the claim's enclosing scope — a silent
  gap here would otherwise read as "no type error" rather than "couldn't check." See
  `docs/superpowers/specs/2026-07-10-v3.1-type-nullability-design.md`.

## [0.1.0] - 2026-07-09

- **v3.0-min — the deterministic verification layer, initial slice.** Claim/Verdict schema
  (`cca_checks.claim`, artifact-or-`UNCERTAIN` rule), the `fp-check` router, a `pyright` adapter
  for `definedness` claims, and a `pytest`-based repro runner (confirm-on-fail, escalate-on-pass).
  Falls back to LLM-only verification when `pyright`/`pytest` are absent — no crash, no silent
  regression. See `docs/v3-design.md` and
  `docs/superpowers/plans/2026-07-09-v3-deterministic-verification.md`.

## Unclaimed

- **v3.3 — other languages** (TypeScript via `tsc`, Go, Rust) is reserved in the roadmap
  (`docs/v3-design.md` §7) but not implemented. Also the design's stated point at which the
  mechanical checks should move behind their own layer.
