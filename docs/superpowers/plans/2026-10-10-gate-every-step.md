# Gate Every Step Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every step of `/audit-fix` enforceable: the commit gate, `pipeline close` and a new Stop hook refuse a run that skipped, reordered, or went stale on any layer, and the gate learns that code changed from the tree, not from the agent's report.

**Architecture:** One stdlib-only rules module, `cca_checks/plugin/hooks/cca_gate_core.py`, holds the fingerprint, the required-layer table, the order table and the completeness check. `pipeline_state.py`, the commit guard and the new stop guard all load it by file path, so there is one copy of the rules instead of two pinned by a drift test. Runs started by 0.10.x (no `schema` key) keep the 0.10.x rules verbatim.

**Tech Stack:** Python 3.10+, stdlib only in the hooks, `git` 2.31+ on PATH, pytest.

**Spec:** `docs/superpowers/specs/2026-10-10-gate-every-step-design.md` (commits `e13b07f`, `c05214e`).

## Global Constraints

- Hooks import nothing outside the stdlib and their own directory. A hook that fails to import must never block a commit when no run is open.
- Run state, ledger and snapshots live under `.claude/audits/`; that path is excluded from the fingerprint whether or not `.claude/` is gitignored.
- `SCHEMA = 2` marks a run started by this code. A state without `"schema": 2` is legacy and is judged by the 0.10.x rules, verbatim.
- A required layer is satisfied only by `status == "done"`.
- `STOP_REFUSAL_CAP = 3` per run.
- Every new behaviour test is shown red before it counts: against baseline `d2317de` where the behaviour already exists to be wrong, otherwise against a named mutation. The receipt (node id, baseline or mutation, failure line) goes in the test's docstring. ImportError, collection errors and skips are not red.
- No dash used as punctuation in commit messages, docs or the changelog (user rule). Hyphens inside words and identifiers are fine.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Never `git stash` bare in this repo (shared stash across worktrees). Use the baseline worktree for red proofs.

## Review Focus

1. **A user repo where `.claude/` is NOT gitignored.** Every `pipeline record` writes `run-state.json`; if that file entered the fingerprint, every verdict would be stale the moment it was written and no run could ever commit. Pinned by `test_fingerprint_ignores_the_run_state_when_claude_is_not_ignored` (Task 1).
2. **`pipeline close` right after the commit in Step 7.** HEAD has moved and the diff against HEAD is empty, so a fingerprint built from `git diff HEAD` would differ from the verdicts' and refuse the close of a perfect run. The fingerprint is therefore the tree id of the working tree content, independent of HEAD. Pinned by `test_fingerprint_survives_committing_the_same_content` (Task 1) and `test_close_after_the_commit_succeeds` (Task 2).
3. **Stop firing while auditors run as background subagents.** Claude Code fires Stop with a non-empty `background_tasks` while the orchestrator waits; blocking there would burn the 3 refusals before L1 even lands. Pinned by `test_stop_guard_allows_while_background_tasks_run` (Task 5).
4. **Outcome rows in the same ledger file.** Step 6 appends `{"run_id", "fix_id", "result", ...}` rows to `AUDITOR_SCORECARD.jsonl`; counting them as dispositions would make the L2.5 ledger check refuse every second run. Only rows with a `verdict` key count. Pinned by `test_ledger_count_ignores_outcome_rows` (Task 2).
5. **The guard's hook timeout.** If the fingerprint outlives the PreToolUse timeout, Claude Code treats the hook as a non-blocking error and the commit proceeds: fail open. The snippet's timeout rises from 15 s to 60 s, above the fingerprint's 30 s bound. Pinned by `test_hook_snippet_timeouts_exceed_the_fingerprint_bound` (Task 5).

---

## File map

| File | Status | Responsibility |
|---|---|---|
| `cca_checks/plugin/hooks/cca_gate_core.py` | create | fingerprint, tables, `outstanding`, `commit_reasons`, legacy rules, state IO |
| `cca_checks/pipeline_state.py` | modify | run API on top of the core: start, record (order, source, counts, tree), close, verify |
| `cca_checks/__main__.py` | modify | `pipeline record` count flags, exit 2 on refusal, `status --require-complete` |
| `cca_checks/plugin/hooks/cca_pipeline_guard.py` | modify | commit gate delegating to the core |
| `cca_checks/plugin/hooks/cca_stop_guard.py` | create | Stop hook |
| `cca_checks/plugin/__init__.py` | modify | snippet with both hooks, registration warning |
| `cca_checks/plugin/tools/cca_scorecard.py` | modify | `--record` |
| `cca_checks/plugin/tools/cca_tautology_check.py` | modify | `verify --record`, `--no-proofs` |
| `cca_checks/plugin/commands/audit-fix.md` | modify | Contract table and record commands |
| `cca_checks/plugin/codex/cca-audit/SKILL.md` | modify | `status --require-complete` before reporting |
| `CHANGELOG.md` | modify | Unreleased entry |
| `tests/conftest.py` | create | `git_repo`, `tree_of`, `load_hook` |
| `tests/test_gate_core.py` | create | core rules |
| `tests/test_gate_holes.py` | create | the five holes, red against `d2317de` |
| `tests/test_pipeline_state.py` | modify | new API and rules |
| `tests/test_pipeline_cli.py` | create | CLI flags and exit codes |
| `tests/test_pipeline_guard.py` | modify | guard on the core |
| `tests/test_stop_guard.py` | create | Stop hook |
| `tests/test_plugin_install.py` | modify | snippet and warning |
| `cca_checks/plugin/tools/test_cca_scorecard.py`, `test_cca_tautology_check.py` | modify | `--record` |
| `tests/test_pipeline_prose.py` | create | prose matches the gate |

---

### Task 0: Baseline worktree for red proofs

**Files:** none in the branch.

- [ ] **Step 1: Create the baseline checkout**

```bash
git worktree add ../cca-baseline d2317de
```

Expected: `HEAD is now at d2317de`. It lives at `.claude/worktrees/cca-baseline`, beside this worktree. Every red proof below copies the new test file (and `tests/conftest.py`) there and runs it with `python -m pytest`, which puts the baseline directory first on `sys.path`, so the baseline `cca_checks` is the one imported.

---

### Task 1: The gate core

**Files:**
- Create: `cca_checks/plugin/hooks/cca_gate_core.py`
- Create: `tests/conftest.py`
- Test: `tests/test_gate_core.py`

**Interfaces:**
- Produces (all in `cca_gate_core`):
  - `SCHEMA: int = 2`, `LAYERS: tuple[str, ...]`, `TIERS: tuple[str, ...]`, `APPROVING_VERDICT = "APPROVED"`, `TOOL_LAYERS: dict[str, str]` (layer to the command that records it), `FIX_VERDICT_LAYERS: tuple[str, ...]`, `HEADINGS: dict[str, str]`, `STOP_REFUSAL_CAP = 3`, `EXCLUDED = ".claude/audits"`
  - `state_file(root) -> pathlib.Path`, `now() -> str`, `read_state(root) -> dict | None` (raises `ValueError` if unreadable), `write_state(root, state: dict) -> None`, `append_log(root, line: str) -> pathlib.Path`
  - `fingerprint(root) -> str | None`
  - `is_legacy(state: dict) -> bool`, `fixes_owed(state: dict, tree: str | None) -> bool`
  - `required_layers(state: dict, owed: bool) -> tuple[str, ...]`, `predecessors(layer: str, state: dict, owed: bool) -> tuple[str, ...]`
  - `outstanding(state: dict, tree: str | None) -> list[str]` (schema 2 completeness)
  - `commit_reasons(state: dict, tree: str | None) -> list[str]` (legacy or schema 2)
  - `legacy_commit_reasons(state: dict) -> list[str]`
- `tests/conftest.py` produces fixtures `git_repo` (a committed repo with `.claude/` ignored) and helpers `tree_of(repo) -> str`, `load_hook(name) -> module`.

- [ ] **Step 1: Write `tests/conftest.py`**

```python
"""Shared fixtures for the pipeline gate tests.

`tree_of` deliberately re-implements the fingerprint with plain git commands
instead of importing it: the hole tests in test_gate_holes.py must run unchanged
against baseline d2317de, where the core module does not exist.
"""

from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import subprocess
import tempfile

import pytest

_HOOKS = pathlib.Path(__file__).resolve().parents[1] / "cca_checks" / "plugin" / "hooks"


def _git(root, *args, env=None) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, env=env
    ).stdout.decode("utf-8", "replace").strip()


def tree_of(repo) -> str:
    """Tree id of the working tree content, excluding .claude/audits."""
    index = _git(repo, "rev-parse", "--path-format=absolute", "--git-path", "index")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_index = os.path.join(tmp, "index")
        if os.path.exists(index):
            shutil.copyfile(index, tmp_index)
        env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
        _git(repo, "add", "-A", "--", ".", ":(exclude).claude/audits", env=env)
        return _git(repo, "write-tree", env=env)


def load_hook(name: str):
    path = _HOOKS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _init(root: pathlib.Path, gitignore: str) -> pathlib.Path:
    if shutil.which("git") is None:
        pytest.skip("git is required for tree fingerprints")
    _git(root, "init", "-q")
    _git(root, "config", "user.email", "t@example.invalid")
    _git(root, "config", "user.name", "t")
    _git(root, "config", "core.autocrlf", "false")
    (root / ".gitignore").write_text(gitignore, encoding="utf-8")
    (root / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-q", "-m", "init")
    return root


@pytest.fixture
def git_repo(tmp_path) -> pathlib.Path:
    return _init(tmp_path, ".claude/\n")


@pytest.fixture
def git_repo_tracking_claude(tmp_path) -> pathlib.Path:
    """A user repository that does NOT ignore .claude/ (Review Focus 1)."""
    return _init(tmp_path, "")
```

- [ ] **Step 2: Write the failing core tests**

`tests/test_gate_core.py`:

```python
"""Rules of the CCA gates. Pure functions over state dicts, plus the fingerprint.

Red proofs (the core is a new symbol, so every proof here is a mutation of the
implementation, applied, observed failing in its assertion, then reverted):
recorded per test.
"""

from __future__ import annotations

import pytest

from conftest import load_hook, tree_of

core = load_hook("cca_gate_core.py")


def _state(tier="STANDARD", *, mode="DIFF", no_fix=False, base="T0", fixes=0, layers=None):
    layers = layers or {}
    return {"schema": core.SCHEMA, "run_id": "r1", "tier": tier, "mode": mode,
            "no_fix": no_fix, "base_tree": base, "fixes_applied": fixes,
            "seq": len(layers), "layers": layers}


def _done(seq, detail="done", **extra):
    return {"status": "done", "detail": detail, "seq": seq, **extra}


def _full_standard(tree="T1"):
    return {
        "L1": _done(1), "L2": _done(2), "L2.5": _done(3),
        "L2.6": _done(4, source="tool"), "L3": _done(5), "L4": _done(6),
        "L5": _done(7, tree=tree), "L5.5": _done(8, tree=tree),
        "L5.6": _done(9, source="tool", tree=tree), "L6": _done(10, "APPROVED", tree=tree),
    }


# --- fingerprint -------------------------------------------------------------

def test_fingerprint_matches_the_git_tree_of_the_working_tree(git_repo):
    """Mutation: return a hash of HEAD only. Fails: fingerprint != tree_of after an edit."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert core.fingerprint(git_repo) == tree_of(git_repo)


def test_fingerprint_changes_on_edit_and_on_untracked_file(git_repo):
    """Mutation: drop `-A` from git add (tracked only). Fails on the untracked assertion."""
    t0 = core.fingerprint(git_repo)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    t1 = core.fingerprint(git_repo)
    (git_repo / "test_new.py").write_text("def test(): pass\n", encoding="utf-8")
    t2 = core.fingerprint(git_repo)
    assert len({t0, t1, t2}) == 3


def test_fingerprint_survives_committing_the_same_content(git_repo):
    """Review Focus 2. Mutation: hash `git diff HEAD` instead of write-tree. Fails: values differ."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    before = core.fingerprint(git_repo)
    import subprocess
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "fix"], check=True)
    assert core.fingerprint(git_repo) == before


def test_fingerprint_ignores_the_run_state_when_claude_is_not_ignored(git_repo_tracking_claude):
    """Review Focus 1. Mutation: drop the `:(exclude)` pathspec. Fails: values differ."""
    repo = git_repo_tracking_claude
    before = core.fingerprint(repo)
    state = repo / ".claude" / "audits" / "run-state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    assert core.fingerprint(repo) == before


def test_fingerprint_is_none_outside_a_repository(tmp_path):
    """Mutation: return "" on failure. Fails: "" is not None."""
    assert core.fingerprint(tmp_path) is None


# --- required layers and fixes owed -------------------------------------------

def test_fixes_are_owed_when_the_tree_changed_without_any_l4_record():
    """THE hole: 0.10.x keyed this on fixes_applied alone. Mutation: return fixes > 0. Fails."""
    assert core.fixes_owed(_state(base="T0"), "T1")
    assert not core.fixes_owed(_state(base="T0"), "T0")


def test_fixes_are_owed_when_either_fingerprint_is_unknown():
    """Fail closed. Mutation: treat None as unchanged. Fails on both asserts."""
    assert core.fixes_owed(_state(base=None), "T0")
    assert core.fixes_owed(_state(base="T0"), None)


@pytest.mark.parametrize("tier,mode,no_fix,owed,expected", [
    ("FAST", "DIFF", False, False, ("L1", "L2", "L2.5", "L3", "L6")),
    ("FAST", "DIFF", False, True, ("L1", "L2", "L2.5", "L3", "L4", "L5", "L6")),
    ("STANDARD", "DIFF", False, True,
     ("L1", "L2", "L2.5", "L2.6", "L3", "L4", "L5", "L5.5", "L5.6", "L6")),
    ("DEEP", "HUNT", False, False, ("L0.4", "L1", "L2", "L2.5", "L2.6", "L3", "L6")),
    ("DEEP", "DIFF", True, False, ("L1", "L2", "L2.5", "L2.6")),
])
def test_required_layers_table(tier, mode, no_fix, owed, expected):
    """Mutation: drop "L3" from the table. Fails on every row but the no-fix one."""
    state = _state(tier, mode=mode, no_fix=no_fix)
    assert core.required_layers(state, owed) == expected


# --- outstanding -----------------------------------------------------------------

def test_a_complete_standard_run_has_nothing_outstanding():
    """Control for over-blocking. Mutation: require L0.4 always. Fails: L0.4 reported."""
    assert core.outstanding(_state(layers=_full_standard("T1")), "T1") == []


def test_n_a_does_not_satisfy_a_required_layer():
    """Mutation: accept status in {"done", "n/a"}. Fails: no reason for L2.5."""
    layers = _full_standard("T1")
    layers["L2.5"]["status"] = "n/a"
    assert any(r.startswith("L2.5 is recorded as n/a") for r in
               core.outstanding(_state(layers=layers), "T1"))


def test_a_verdict_from_before_the_latest_edit_is_stale():
    """Mutation: skip the tree comparison. Fails: no 'before the latest edit' reason."""
    reasons = core.outstanding(_state(layers=_full_standard("T1")), "T2")
    assert {r.split()[0] for r in reasons if "latest edit" in r} == {"L5", "L5.5", "L5.6", "L6"}


def test_re_recording_l4_makes_every_later_verdict_older_than_it():
    """Mutation: delete the `for prior in predecessors(...)` loop in outstanding(). Fails."""
    layers = _full_standard("T1")
    layers["L4"] = _done(11)
    reasons = core.outstanding(_state(layers=layers), "T1")
    assert any(r.startswith("L5 is older than L4") for r in reasons)


def test_tool_layers_recorded_by_hand_are_refused():
    """Mutation: drop the source check. Fails: no 'must be recorded by its tool' reason."""
    layers = _full_standard("T1")
    layers["L2.6"]["source"] = "cli"
    assert any("L2.6 must be recorded by its tool" in r
               for r in core.outstanding(_state(layers=layers), "T1"))


def test_hunt_needs_l0_4_pass():
    """Mutation: drop the PASS check. Fails: REJECT accepted."""
    layers = {"L0.4": _done(1, "REJECT"), "L1": _done(2), "L2": _done(3), "L2.5": _done(4),
              "L2.6": _done(5, source="tool"), "L3": _done(6), "L6": _done(7, "APPROVED")}
    reasons = core.outstanding(_state("DEEP", mode="HUNT", layers=layers), "T0")
    assert any("L0.4 did not read PASS" in r for r in reasons)


def test_l6_must_approve_for_completeness():
    """Mutation: drop the verdict check from outstanding. Fails: REVISE accepted."""
    layers = _full_standard("T1")
    layers["L6"]["detail"] = "REVISE"
    assert any("returned REVISE" in r for r in core.outstanding(_state(layers=layers), "T1"))


def test_a_no_fix_run_is_complete_after_verification():
    """Mutation: require L3 for no-fix runs too. Fails: L3 reported."""
    layers = {"L1": _done(1), "L2": _done(2), "L2.5": _done(3), "L2.6": _done(4, source="tool")}
    assert core.outstanding(_state("DEEP", no_fix=True, layers=layers), "T0") == []


def test_a_no_fix_run_that_changed_the_tree_is_not_complete():
    """Mutation: drop the no-fix tree comparison. Fails: empty list."""
    layers = {"L1": _done(1), "L2": _done(2), "L2.5": _done(3), "L2.6": _done(4, source="tool")}
    assert core.outstanding(_state("DEEP", no_fix=True, layers=layers), "T9")


# --- commit_reasons and legacy -------------------------------------------------

def test_no_fix_run_may_never_commit_even_when_complete():
    layers = {"L1": _done(1), "L2": _done(2), "L2.5": _done(3), "L2.6": _done(4, source="tool")}
    reasons = core.commit_reasons(_state("DEEP", no_fix=True, layers=layers), "T0")
    assert any("no-fix" in r for r in reasons)


def test_legacy_state_is_judged_by_the_0_10_rules():
    """A 0.10.x run (no schema) with only the old four layers may still commit.

    Mutation: route legacy states to `outstanding`. Fails: L3 reported missing.
    """
    legacy = {"run_id": "t", "tier": "FAST", "mode": "DIFF", "no_fix": False,
              "fixes_applied": 0,
              "layers": {k: {"status": "done", "detail": v} for k, v in
                         {"L1": "d", "L2": "d", "L2.5": "d", "L6": "APPROVED"}.items()}}
    assert core.is_legacy(legacy)
    assert core.commit_reasons(legacy, None) == []
```

- [ ] **Step 3: Run the tests to confirm they cannot run yet**

Run: `python -m pytest tests/test_gate_core.py -q`
Expected: collection ERROR (`FileNotFoundError` loading `cca_gate_core.py`). This is not a red proof; the mutations in Step 6 are.

- [ ] **Step 4: Write `cca_checks/plugin/hooks/cca_gate_core.py`**

```python
#!/usr/bin/env python
"""The rules of the CCA commit and stop gates, in one stdlib-only module.

WHY ONE MODULE. 0.10.x kept the required-layer table twice, once in
`cca_checks/pipeline_state.py` and once in the standalone commit guard, pinned by
a drift test. A second hook would have made it three copies. Instead the hooks
and `pipeline_state` all load this file by path from their own directory, so the
rules exist once and the hooks still import nothing outside the stdlib.

WHAT CHANGED FROM 0.10.x, and why each change exists:
  * fixes are detected from the TREE, not from `fixes_applied`. In 0.10.x an
    agent that edited code and never recorded L4 left `fixes_applied == 0`, and
    the guard stopped requiring L5, L5.5 and L5.6.
  * every layer, L0.4 to L6, is required when its condition holds.
  * order is checked by a monotonic `seq`, and every verdict from L5 onward
    carries the fingerprint it judged, so an edit after a verdict makes it stale.
  * only `done` satisfies a required layer; 0.10.x let `--status n/a` pass.
  * L2.6 and L5.6 must be recorded by their tools.

A run whose state lacks `"schema": 2` was started by 0.10.x and is judged by the
0.10.x rules, verbatim (`legacy_commit_reasons`).

THE BOUND, stated as 0.10.x stated it: an agent with a shell can write the state
file. These rules make forgetting a layer impossible and forging one a
deliberate act. They do not prove a layer was performed well.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone

SCHEMA = 2
LAYERS = ("L0.4", "L1", "L2", "L2.5", "L2.6", "L3", "L4", "L5", "L5.5", "L5.6", "L6")
TIERS = ("FAST", "STANDARD", "DEEP")
APPROVING_VERDICT = "APPROVED"
TOOL_LAYERS = {
    "L2.6": "python .claude/tools/cca_scorecard.py --json --record",
    "L5.6": "python .claude/tools/cca_tautology_check.py verify --proof ... --record",
}
FIX_VERDICT_LAYERS = ("L5", "L5.5", "L5.6", "L6")
HEADINGS = {
    "L0.4": "Step 0.4", "L1": "Step 1", "L2": "Step 2", "L2.5": "Step 2.5",
    "L2.6": "Step 2.6", "L3": "Step 3", "L4": "Step 4", "L5": "Step 5",
    "L5.5": "Step 5.5", "L5.6": "Step 5.6", "L6": "Step 6",
}
STOP_REFUSAL_CAP = 3
EXCLUDED = ".claude/audits"
#: Bound on the whole fingerprint. The hook timeouts in the settings snippet sit
#: above it: a hook that times out is a non-blocking error, which fails OPEN.
FINGERPRINT_TIMEOUT_S = 30

# 0.10.x tables, verbatim, for runs that started under them.
LEGACY_CORE_REQUIRED = ("L1", "L2", "L2.5", "L6")
LEGACY_FIX_REQUIRED = {
    "FAST": ("L5",),
    "STANDARD": ("L5", "L5.5", "L5.6"),
    "DEEP": ("L5", "L5.5", "L5.6"),
}
LEGACY_BAD_STATUS = frozenset({"skipped", "failed"})

_NO_FIX_REASON = (
    "this run was started with `no-fix`, which forbids editing and committing; "
    "abort the run first if this commit is unrelated"
)


# --- state IO ------------------------------------------------------------------

def state_file(root) -> pathlib.Path:
    return pathlib.Path(root) / ".claude" / "audits" / "run-state.json"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state(root) -> dict | None:
    path = state_file(root)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"run state at {path} is unreadable ({exc.__class__.__name__})") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"run state at {path} is not a JSON object")
    return raw


def write_state(root, state: dict) -> None:
    path = state_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_log(root, line: str) -> pathlib.Path:
    log = state_file(root).parent / "EXECUTION_LOG.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line if line.endswith("\n") else line + "\n")
    return log


# --- fingerprint -----------------------------------------------------------------

def _git_exe(root: str) -> str | None:
    # Explicit PATH: without it Windows searches the current directory first, and
    # the current directory is the repository under audit (see cca_checks/toolpath.py).
    found = shutil.which("git", path=os.environ.get("PATH"))
    if not found:
        return None
    exe = os.path.abspath(found)
    if os.path.realpath(os.path.dirname(exe)) == os.path.realpath(root):
        return None
    return exe


def fingerprint(root) -> str | None:
    """Tree id of the working tree content, or None when it cannot be computed.

    A tree id rather than a hash of `git diff HEAD`, so the value survives the
    commit at Step 7: committing the same content moves HEAD but not the tree.
    Built in a COPY of the index so the user's staging area is never touched, and
    `.claude/audits` is excluded by pathspec so the run's own state file cannot
    perturb it in a repository that does not ignore `.claude/`.
    """
    root = str(pathlib.Path(root).resolve())
    git = _git_exe(root)
    if git is None:
        return None

    def run(*args, env=None) -> str:
        return subprocess.run(
            [git, "-C", root, *args], capture_output=True, check=True,
            timeout=FINGERPRINT_TIMEOUT_S, env=env,
        ).stdout.decode("utf-8", "replace").strip()

    try:
        index = run("rev-parse", "--path-format=absolute", "--git-path", "index")
        with tempfile.TemporaryDirectory(prefix="cca-fp-") as tmp:
            tmp_index = os.path.join(tmp, "index")
            if os.path.exists(index):
                shutil.copyfile(index, tmp_index)
            env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
            run("add", "-A", "--", ".", f":(exclude){EXCLUDED}", env=env)
            return run("write-tree", env=env) or None
    except (OSError, subprocess.SubprocessError):
        return None


# --- rules -----------------------------------------------------------------------

def is_legacy(state: dict) -> bool:
    return state.get("schema") != SCHEMA


def _std(state: dict) -> bool:
    return str(state.get("tier", "")).upper() != "FAST"


def _hunt(state: dict) -> bool:
    return str(state.get("mode", "DIFF")).upper() == "HUNT"


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def fixes_owed(state: dict, tree: str | None) -> bool:
    """True when L4 onward is owed. Unknown fingerprints count as changed."""
    base = state.get("base_tree")
    return _int(state.get("fixes_applied")) > 0 or tree is None or base is None or tree != base


def required_layers(state: dict, owed: bool) -> tuple[str, ...]:
    req: list[str] = []
    if _hunt(state):
        req.append("L0.4")
    req += ["L1", "L2", "L2.5"]
    if _std(state):
        req.append("L2.6")
    if not state.get("no_fix"):
        req.append("L3")
        if owed:
            req += ["L4", "L5"]
            if _std(state):
                req += ["L5.5", "L5.6"]
        req.append("L6")
    return tuple(req)


def predecessors(layer: str, state: dict, owed: bool) -> tuple[str, ...]:
    std = _std(state)
    if layer == "L6":
        if owed:
            return ("L5", "L5.5", "L5.6") if std else ("L5",)
        return ("L3",)
    table = {
        "L0.4": (),
        "L1": ("L0.4",) if _hunt(state) else (),
        "L2": ("L1",),
        "L2.5": ("L2",),
        "L2.6": ("L2.5",),
        "L3": ("L2.5", "L2.6") if std else ("L2.5",),
        "L4": ("L3",),
        "L5": ("L4",),
        "L5.5": ("L5",),
        "L5.6": ("L5",),
    }
    return table[layer]


def outstanding(state: dict, tree: str | None) -> list[str]:
    """Why this schema 2 run is not complete. Empty means complete."""
    layers = state.get("layers") if isinstance(state.get("layers"), dict) else {}
    owed = fixes_owed(state, tree)
    reasons: list[str] = []
    for layer in required_layers(state, owed):
        entry = layers.get(layer)
        where = HEADINGS[layer]
        if not isinstance(entry, dict):
            reasons.append(f"{layer} has no recorded verdict (re-read {where})")
            continue
        status = str(entry.get("status", "")).lower()
        if status != "done":
            reasons.append(
                f"{layer} is recorded as {status or 'empty'}; a required layer must be "
                f"done (re-read {where})"
            )
            continue
        if layer in TOOL_LAYERS and entry.get("source") != "tool":
            reasons.append(f"{layer} must be recorded by its tool: {TOOL_LAYERS[layer]}")
        if layer in FIX_VERDICT_LAYERS and owed and entry.get("tree") != tree:
            reasons.append(
                f"{layer} was recorded before the latest edit to the tree; run it again "
                f"(re-read {where})"
            )
        for prior in predecessors(layer, state, owed):
            before = layers.get(prior)
            if isinstance(before, dict) and _int(before.get("seq")) >= _int(entry.get("seq")):
                reasons.append(
                    f"{layer} is older than {prior}, which was recorded again after it; "
                    f"run {layer} again (re-read {where})"
                )
    gate04 = layers.get("L0.4")
    if _hunt(state) and isinstance(gate04, dict) and \
            str(gate04.get("detail", "")).strip().upper() != "PASS":
        reasons.append(
            "L0.4 did not read PASS; a hunt that fails viability must be aborted, not continued"
        )
    if state.get("no_fix"):
        base = state.get("base_tree")
        if base and tree and tree != base:
            reasons.append(
                "this run was started with `no-fix`, but the working tree changed after it started"
            )
    else:
        gate = layers.get("L6")
        if isinstance(gate, dict):
            verdict = str(gate.get("detail", "")).strip().upper()
            if not verdict:
                reasons.append(
                    "L6 was recorded without a verdict; record it as "
                    "--detail APPROVED, REVISE or BLOCKED"
                )
            elif verdict != APPROVING_VERDICT:
                reasons.append(
                    f"the L6 architect gate returned {verdict}, not {APPROVING_VERDICT}"
                )
    return reasons


def legacy_commit_reasons(state: dict) -> list[str]:
    """The 0.10.x guard's `evaluate`, verbatim."""
    reasons: list[str] = []
    tier = str(state.get("tier", "")).upper()
    if tier not in LEGACY_FIX_REQUIRED:
        return [f"run state names an unknown tier {tier!r}"]
    layers = state.get("layers")
    if not isinstance(layers, dict):
        return ["run state has no 'layers' object"]
    if state.get("no_fix"):
        reasons.append(_NO_FIX_REASON)
    required = list(LEGACY_CORE_REQUIRED)
    if _int(state.get("fixes_applied")) > 0:
        required.extend(LEGACY_FIX_REQUIRED[tier])
    for layer in required:
        entry = layers.get(layer)
        if not entry:
            reasons.append(f"{layer} has no recorded verdict (tier {tier})")
        elif str(entry.get("status", "")).lower() in LEGACY_BAD_STATUS:
            reasons.append(f"{layer} is recorded as {entry.get('status')}")
    gate = layers.get("L6")
    if isinstance(gate, dict):
        verdict = str(gate.get("detail", "")).strip().upper()
        if not verdict:
            reasons.append(
                "L6 was recorded without a verdict; record it as "
                "--detail APPROVED, REVISE or BLOCKED"
            )
        elif verdict != APPROVING_VERDICT:
            reasons.append(f"the L6 architect gate returned {verdict}, not {APPROVING_VERDICT}")
    return reasons


def commit_reasons(state: dict, tree: str | None) -> list[str]:
    """Why a commit may not proceed against this open run. Empty means allowed."""
    if is_legacy(state):
        return legacy_commit_reasons(state)
    tier = str(state.get("tier", "")).upper()
    if tier not in TIERS:
        return [f"run state names an unknown tier {tier!r}"]
    if not isinstance(state.get("layers"), dict):
        return ["run state has no 'layers' object"]
    reasons = [_NO_FIX_REASON] if state.get("no_fix") else []
    return reasons + outstanding(state, tree)
```

- [ ] **Step 5: Run the core tests green**

Run: `python -m pytest tests/test_gate_core.py -q`
Expected: all pass.

- [ ] **Step 6: Red proofs by mutation**

For each test, apply the mutation named in its docstring to `cca_gate_core.py`, run that single test, confirm it fails **in its assertion** (an `AssertionError` line pointing into the test body), revert. Example for the central one:

```bash
python - <<'EOF'
import pathlib
p = pathlib.Path("cca_checks/plugin/hooks/cca_gate_core.py")
s = p.read_text(encoding="utf-8")
p.write_text(s.replace(
    "return _int(state.get(\"fixes_applied\")) > 0 or tree is None or base is None or tree != base",
    "return _int(state.get(\"fixes_applied\")) > 0"), encoding="utf-8")
EOF
python -m pytest "tests/test_gate_core.py::test_fixes_are_owed_when_the_tree_changed_without_any_l4_record" -q
git checkout -- cca_checks/plugin/hooks/cca_gate_core.py
```

Expected: `FAILED ... assert False` on `core.fixes_owed(_state(base="T0"), "T1")`. Repeat for each docstring mutation; append `Red: <date>, <failing assert line>` to each docstring.

- [ ] **Step 7: Commit**

```bash
git add tests/conftest.py tests/test_gate_core.py cca_checks/plugin/hooks/cca_gate_core.py
git commit -m "feat(gate): one rules module for the CCA gates, fixes detected from the tree

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: `pipeline_state` on the core

**Files:**
- Modify: `cca_checks/pipeline_state.py` (whole module body below the docstring)
- Test: `tests/test_gate_holes.py` (create), `tests/test_pipeline_state.py` (modify)

**Interfaces:**
- Consumes: everything Task 1 produces.
- Produces:
  - `class RecordRefused(ValueError)`
  - `start(tier, mode="DIFF", *, no_fix=False, run_id="", root=".") -> PipelineState` (stores `schema=2`, `base_tree`, `seq=0`)
  - `record(layer, *, status="done", detail="", fixes=None, counts=None, source="cli", root=".") -> PipelineState`; `counts` keys: `unique`, `confirmed`, `fp`, `uncertain`, `duplicate`, `to_fix`
  - `verify_commit_allowed(state, root=".") -> CommitDecision`
  - `required_layers(state, root=".") -> tuple[str, ...]`
  - `outstanding(state, root=".") -> list[str]`
  - `close(root=".") -> pathlib.Path | None` (raises `RecordRefused` when incomplete)
  - `PipelineState` gains fields `schema: int`, `base_tree: str | None`, `seq: int`, `stop_refusals: int`, `stop_logged: bool`

- [ ] **Step 1: Write the hole tests (they must pass on nothing but hand-written state)**

`tests/test_gate_holes.py`:

```python
"""The holes the 2026-10-10 spec closes, written so they run UNCHANGED on d2317de.

They build the run state by hand and drive the commit guard and `close()`
directly, using only APIs that exist at d2317de, so the red proof is against the
real baseline rather than a mutation. Each must fail there in its assertion.
"""

from __future__ import annotations

import io
import json

import pytest

from cca_checks import pipeline_state as ps
from conftest import load_hook, tree_of

guard = load_hook("cca_pipeline_guard.py")


def _write(repo, state):
    path = repo / ".claude" / "audits" / "run-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state), encoding="utf-8")


def _commit_exit(monkeypatch, repo) -> int:
    monkeypatch.chdir(repo)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": "git commit -m x"}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    return guard.main()


def _d(seq, detail="done", **extra):
    return {"status": "done", "detail": detail, "seq": seq, **extra}


def _run(repo, layers, *, tier="STANDARD", mode="DIFF", base="stale-base", fixes=0):
    return {"schema": 2, "run_id": "r1", "tier": tier, "mode": mode, "no_fix": False,
            "started_utc": "2026-10-10T00:00:00Z", "base_tree": base,
            "fixes_applied": fixes, "seq": 20, "layers": layers}


def _complete(tree):
    return {"L1": _d(1), "L2": _d(2), "L2.5": _d(3), "L2.6": _d(4, source="tool"),
            "L3": _d(5), "L4": _d(6), "L5": _d(7, tree=tree), "L5.5": _d(8, tree=tree),
            "L5.6": _d(9, source="tool", tree=tree), "L6": _d(10, "APPROVED", tree=tree)}


def test_control_a_complete_run_may_commit(monkeypatch, git_repo):
    """Control against over-blocking: passes on baseline and after. Not a red proof."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _write(git_repo, _run(git_repo, _complete(tree_of(git_repo)), fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 0


def test_hole1_an_edit_without_an_l4_record_still_owes_the_fix_gates(monkeypatch, git_repo):
    """Baseline d2317de: guard returns 0 because fixes_applied == 0."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = {"L1": _d(1), "L2": _d(2), "L2.5": _d(3), "L2.6": _d(4, source="tool"),
              "L3": _d(5), "L6": _d(6, "APPROVED")}
    _write(git_repo, _run(git_repo, layers))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole2_n_a_does_not_satisfy_a_required_layer(monkeypatch, git_repo):
    """Baseline: `n/a` is not in the guard's BAD_STATUS, so it passes."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = _complete(tree_of(git_repo))
    layers["L2.5"]["status"] = "n/a"
    _write(git_repo, _run(git_repo, layers, fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole3_an_edit_after_approval_makes_the_verdicts_stale(monkeypatch, git_repo):
    """Baseline: presence is all it checks, so the stale APPROVED passes."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = _complete(tree_of(git_repo))
    (git_repo / "app.py").write_text("x = 3\n", encoding="utf-8")
    _write(git_repo, _run(git_repo, layers, fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole4_l4_recorded_again_after_l6_invalidates_it(monkeypatch, git_repo):
    """Baseline: order is never read, so it passes."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = _complete(tree_of(git_repo))
    layers["L4"] = _d(11)
    _write(git_repo, _run(git_repo, layers, fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole5_a_hunt_without_l0_4_cannot_commit(monkeypatch, git_repo):
    """Baseline: L0.4 is not a layer, so the hunt commits."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _write(git_repo, _run(git_repo, _complete(tree_of(git_repo)), tier="DEEP",
                          mode="HUNT", fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole6_a_tool_layer_recorded_by_hand_is_refused(monkeypatch, git_repo):
    """Baseline: source is ignored, so it passes."""
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = _complete(tree_of(git_repo))
    layers["L5.6"]["source"] = "cli"
    _write(git_repo, _run(git_repo, layers, fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole7_close_refuses_an_incomplete_run(git_repo):
    """Baseline: close() logs COMPLETE and returns the log path; DID NOT RAISE."""
    _write(git_repo, _run(git_repo, {"L1": _d(1)}))
    with pytest.raises(ValueError):
        ps.close(root=git_repo)
    assert (git_repo / ".claude" / "audits" / "run-state.json").exists()
```

- [ ] **Step 2: Red proof against the baseline**

```bash
cp tests/conftest.py tests/test_gate_holes.py ../cca-baseline/tests/
(cd ../cca-baseline && python -m pytest tests/test_gate_holes.py -q -p no:cacheprovider)
```

Expected: `test_control_a_complete_run_may_commit` PASSED; the seven `hole` tests FAILED, six with `assert 0 == 2` and hole7 with `Failed: DID NOT RAISE <class 'ValueError'>`. Any other failure shape (ImportError, fixture error) is not a proof: stop and fix the test. Append `Red at d2317de 2026-10-10: <line>` to each docstring.

- [ ] **Step 3: Run the same file here, still red**

Run: `python -m pytest tests/test_gate_holes.py -q`
Expected: the same seven failures (the guard has not changed yet). Hole 1 to 6 go green in Task 4; hole 7 goes green in this task.

- [ ] **Step 4: Rewrite `tests/test_pipeline_state.py` for the new API**

Keep the module docstring. Replace the body with:

```python
from __future__ import annotations

import json
import subprocess

import pytest

from cca_checks import pipeline_state as ps


def _ledger(repo, run_id, n, *, outcome_rows=0):
    path = repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"run_id": run_id, "auditor": "cca-bug-auditor",
                                 "category": "x", "verdict": "CONFIRMED",
                                 "fix_id": f"FIX-{i}"}) + "\n")
        for i in range(outcome_rows):
            fh.write(json.dumps({"run_id": run_id, "fix_id": f"FIX-{i}",
                                 "result": "fixed", "architect_verdict": "APPROVED"}) + "\n")


def _through_l3(repo, tier, *, confirmed=1, no_fix=False):
    state = ps.start(tier, no_fix=no_fix, root=repo)
    ps.record("L1", detail="6 auditors", root=repo)
    ps.record("L2", counts={"unique": confirmed}, root=repo)
    if tier != "FAST":
        _ledger(repo, state.run_id, confirmed)
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": 0,
                              "duplicate": 0}, root=repo)
    if tier != "FAST":
        ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    if not no_fix:
        ps.record("L3", counts={"to_fix": confirmed}, root=repo)
    return state


def _fix_through_l6(repo, tier, verdict="APPROVED"):
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, root=repo)
    ps.record("L5", detail="green", root=repo)
    if tier != "FAST":
        ps.record("L5.5", detail="all SAFE", root=repo)
        ps.record("L5.6", detail="1 RED", source="tool", root=repo)
    return ps.record("L6", detail=verdict, root=repo)


def test_no_run_open_allows_any_commit(git_repo):
    assert ps.load(git_repo) is None
    assert ps.verify_commit_allowed(None).allowed


def test_start_records_schema_and_the_base_tree(git_repo):
    state = ps.start("DEEP", root=git_repo)
    raw = json.loads(ps.state_path(git_repo).read_text(encoding="utf-8"))
    assert raw["schema"] == 2 and raw["base_tree"] and raw["seq"] == 0
    assert state.base_tree == raw["base_tree"]


@pytest.mark.parametrize("tier", ["FAST", "STANDARD", "DEEP"])
def test_a_full_run_with_fixes_may_commit_and_close(git_repo, tier):
    _through_l3(git_repo, tier)
    state = _fix_through_l6(git_repo, tier)
    assert ps.verify_commit_allowed(state, root=git_repo).allowed
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "fix"], check=True)
    assert ps.close(root=git_repo) is not None


def test_close_after_the_commit_succeeds(git_repo):
    """Review Focus 2. Mutation: fingerprint from `git diff HEAD`. Fails: close refuses."""
    _through_l3(git_repo, "STANDARD")
    _fix_through_l6(git_repo, "STANDARD")
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "fix"], check=True)
    assert ps.close(root=git_repo) is not None
    assert ps.load(git_repo) is None


def test_a_zero_fix_run_needs_l3_and_l6_but_not_the_fix_layers(git_repo):
    _through_l3(git_repo, "STANDARD", confirmed=0)
    state = ps.record("L6", detail="APPROVED", root=git_repo)
    assert set(ps.required_layers(state, root=git_repo)) == {"L1", "L2", "L2.5", "L2.6",
                                                            "L3", "L6"}
    assert ps.close(root=git_repo) is not None


def test_a_no_fix_run_can_close_after_verification(git_repo):
    """Mutation: require L3 for no-fix in the core. Fails: close raises."""
    _through_l3(git_repo, "DEEP", no_fix=True)
    assert ps.close(root=git_repo) is not None


def test_l6_before_its_predecessors_is_refused_at_record_time(git_repo):
    """Baseline d2317de: record accepts any order. Here: RecordRefused naming L3."""
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="L3"):
        ps.record("L6", detail="APPROVED", root=git_repo)


def test_tool_layers_cannot_be_recorded_from_the_cli_source(git_repo):
    _through_l3(git_repo, "STANDARD")
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, root=git_repo)
    ps.record("L5", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="cca_tautology_check"):
        ps.record("L5.6", detail="1 RED", root=git_repo)


def test_l2_5_counts_must_account_for_every_unique_finding(git_repo):
    """Mutation: skip the sum comparison. Fails: no RecordRefused."""
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 3}, root=git_repo)
    with pytest.raises(ps.RecordRefused, match="3 unique"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 1, "uncertain": 0, "duplicate": 0},
                  root=git_repo)


def test_ledger_must_hold_one_row_per_verdict(git_repo):
    """Mutation: skip the ledger comparison. Fails: no RecordRefused."""
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 2}, root=git_repo)
    _ledger(git_repo, state.run_id, 1)
    with pytest.raises(ps.RecordRefused, match="ledger holds 1"):
        ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
                  root=git_repo)


def test_ledger_count_ignores_outcome_rows(git_repo):
    """Review Focus 4. Mutation: count every row with the run_id. Fails: RecordRefused."""
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 2}, root=git_repo)
    _ledger(git_repo, state.run_id, 2, outcome_rows=2)
    ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)


def test_missing_count_flags_are_refused(git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="--unique"):
        ps.record("L2", root=git_repo)


def test_l3_cannot_plan_more_fixes_than_were_confirmed(git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 1}, root=git_repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)
    with pytest.raises(ps.RecordRefused, match="--to-fix"):
        ps.record("L3", counts={"to_fix": 2}, root=git_repo)


def test_a_hunt_that_failed_viability_cannot_start_auditing(git_repo):
    ps.start("DEEP", mode="HUNT", root=git_repo)
    ps.record("L0.4", detail="REJECT", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="PASS"):
        ps.record("L1", root=git_repo)


def test_layers_alone_do_not_pass_without_an_approving_verdict(git_repo):
    _through_l3(git_repo, "FAST")
    for verdict in ("REVISE", "BLOCKED"):
        state = _fix_through_l6(git_repo, "FAST", verdict)
        decision = ps.verify_commit_allowed(state, root=git_repo)
        assert not decision.allowed and verdict in " ".join(decision.reasons)


def test_no_fix_run_may_never_commit(git_repo):
    state = _through_l3(git_repo, "DEEP", no_fix=True)
    decision = ps.verify_commit_allowed(ps.load(git_repo), root=git_repo)
    assert not decision.allowed and any("no-fix" in r for r in decision.reasons)
    assert state.no_fix


def test_unreadable_state_raises_rather_than_reading_as_no_run(tmp_path):
    path = ps.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        ps.load(tmp_path)


def test_unknown_tier_and_layer_are_refused(git_repo):
    with pytest.raises(ValueError):
        ps.start("THOROUGH", root=git_repo)
    ps.start("DEEP", root=git_repo)
    with pytest.raises(ValueError):
        ps.record("L2_5", root=git_repo)


def test_recording_without_an_open_run_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        ps.record("L1", root=tmp_path)


def test_abort_clears_the_block_and_leaves_a_dated_trail(git_repo):
    ps.start("DEEP", root=git_repo)
    log = ps.abort("unrelated hotfix", root=git_repo)
    assert ps.load(git_repo) is None
    text = log.read_text(encoding="utf-8")
    assert "ABORTED" in text and "unrelated hotfix" in text


def test_abort_works_even_on_an_unparseable_state(tmp_path):
    path = ps.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{{{", encoding="utf-8")
    assert ps.abort("corrupt state", root=tmp_path) is not None
    assert not path.exists()


def test_a_legacy_state_round_trips_without_gaining_a_schema(git_repo):
    """A 0.10.x run must stay legacy through record(). Mutation: default schema to 2. Fails."""
    path = ps.state_path(git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "old", "tier": "FAST", "mode": "DIFF",
                                "no_fix": False, "fixes_applied": 0, "layers": {}}),
                    encoding="utf-8")
    ps.record("L1", root=git_repo)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw.get("schema", 1) != 2
```

- [ ] **Step 5: Run to confirm red**

Run: `python -m pytest tests/test_pipeline_state.py -q`
Expected: failures with `TypeError: record() got an unexpected keyword argument 'counts'` and `AttributeError: ... RecordRefused`. These are not red proofs (missing symbols); the proofs are the hole tests and the docstring mutations.

- [ ] **Step 6: Implement**

Replace everything in `cca_checks/pipeline_state.py` below the module docstring with the following, and in the docstring replace `the pipeline's steps are ~28KB of text` with `the pipeline's steps are ~49KB of text (measured 2026-10-10: git show origin/master:cca_checks/plugin/commands/audit-fix.md | wc -c)`.

```python
from __future__ import annotations

import importlib.util
import json
import pathlib
from dataclasses import dataclass, field

__all__ = [
    "CommitDecision", "LAYERS", "PipelineState", "RecordRefused", "TIERS", "abort",
    "close", "load", "outstanding", "record", "required_layers", "start", "state_path",
    "verify_commit_allowed",
]


def _load_core():
    # By path, not by import: the core also has to load from a user's
    # .claude/hooks/ directory where no package exists, and one loader for both
    # places means one copy of the rules.
    path = pathlib.Path(__file__).resolve().parent / "plugin" / "hooks" / "cca_gate_core.py"
    spec = importlib.util.spec_from_file_location("cca_gate_core", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = _load_core()
LAYERS = core.LAYERS
TIERS = core.TIERS
_COUNT_KEYS = ("unique", "confirmed", "fp", "uncertain", "duplicate", "to_fix")


class RecordRefused(ValueError):
    """A layer that may not be recorded yet, or not with these numbers."""


def state_path(root: str | pathlib.Path = ".") -> pathlib.Path:
    return core.state_file(root)


@dataclass
class PipelineState:
    run_id: str
    tier: str
    mode: str = "DIFF"
    no_fix: bool = False
    started_utc: str = ""
    fixes_applied: int = 0
    layers: dict = field(default_factory=dict)
    schema: int = 1
    base_tree: str | None = None
    seq: int = 0
    stop_refusals: int = 0
    stop_logged: bool = False

    def to_json(self) -> dict:
        return {
            "schema": self.schema, "run_id": self.run_id, "tier": self.tier,
            "mode": self.mode, "no_fix": self.no_fix, "started_utc": self.started_utc,
            "fixes_applied": self.fixes_applied, "base_tree": self.base_tree,
            "seq": self.seq, "stop_refusals": self.stop_refusals,
            "stop_logged": self.stop_logged, "layers": self.layers,
        }

    @classmethod
    def from_json(cls, raw: dict) -> PipelineState:
        tier = str(raw.get("tier", "")).upper()
        if tier not in TIERS:
            raise ValueError(
                f"unknown tier {tier!r} in run state (expected one of {', '.join(TIERS)})"
            )
        layers = raw.get("layers")
        if not isinstance(layers, dict):
            raise ValueError("run state 'layers' is missing or is not an object")
        return cls(
            run_id=str(raw.get("run_id", "")), tier=tier,
            mode=str(raw.get("mode", "DIFF")).upper(),
            no_fix=bool(raw.get("no_fix", False)),
            started_utc=str(raw.get("started_utc", "")),
            fixes_applied=int(raw.get("fixes_applied", 0) or 0),
            layers=layers,
            schema=int(raw.get("schema", 1) or 1),
            base_tree=raw.get("base_tree"),
            seq=int(raw.get("seq", 0) or 0),
            stop_refusals=int(raw.get("stop_refusals", 0) or 0),
            stop_logged=bool(raw.get("stop_logged", False)),
        )

    @property
    def legacy(self) -> bool:
        return self.schema != core.SCHEMA


@dataclass
class CommitDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    run_id: str = ""


def load(root: str | pathlib.Path = ".") -> PipelineState | None:
    raw = core.read_state(root)
    return None if raw is None else PipelineState.from_json(raw)


def _write(state: PipelineState, root) -> None:
    core.write_state(root, state.to_json())


def start(tier: str, mode: str = "DIFF", *, no_fix: bool = False, run_id: str = "",
          root: str | pathlib.Path = ".") -> PipelineState:
    """Open a run: at Step 0.6, or at Step 0.4 for a hunt."""
    tier = tier.upper()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r} (expected one of {', '.join(TIERS)})")
    stamp = core.now()
    state = PipelineState(
        run_id=run_id or stamp.replace(":", "").replace("-", ""), tier=tier,
        mode=mode.upper(), no_fix=no_fix, started_utc=stamp,
        schema=core.SCHEMA, base_tree=core.fingerprint(root),
    )
    _write(state, root)
    return state


def _ledger_rows(root, run_id: str) -> tuple[int, int]:
    """(disposition rows for run_id, malformed lines). Outcome rows carry no
    `verdict` key and are not dispositions (Review Focus 4)."""
    path = pathlib.Path(root) / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    rows = malformed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(row, dict) and "verdict" in row and str(row.get("run_id")) == run_id:
            rows += 1
    return rows, malformed


def _need(counts: dict, key: str, layer: str) -> int:
    value = counts.get(key)
    if value is None:
        raise RecordRefused(f"{layer} needs --{key.replace('_', '-')} <N>")
    if int(value) < 0:
        raise RecordRefused(f"{layer} --{key.replace('_', '-')} cannot be negative")
    return int(value)


def _layer_count(state: PipelineState, layer: str, key: str) -> int | None:
    entry = state.layers.get(layer)
    if not isinstance(entry, dict):
        return None
    value = (entry.get("counts") or {}).get(key)
    return None if value is None else int(value)


def _check_counts(layer: str, state: PipelineState, counts: dict, root) -> None:
    if layer == "L2":
        _need(counts, "unique", layer)
    elif layer == "L2.5":
        total = sum(_need(counts, k, layer) for k in ("confirmed", "fp", "uncertain", "duplicate"))
        unique = _layer_count(state, "L2", "unique")
        if unique is not None and total != unique:
            raise RecordRefused(
                f"L2.5 accounts for {total} findings but L2 recorded {unique} unique; "
                "every consolidated finding needs exactly one verdict"
            )
        if state.tier != "FAST":
            rows, malformed = _ledger_rows(root, state.run_id)
            if rows != total:
                note = f" ({malformed} malformed line(s) skipped)" if malformed else ""
                raise RecordRefused(
                    f"the disposition ledger holds {rows} row(s) for run {state.run_id} but "
                    f"L2.5 recorded {total} verdicts{note}; append one row per verified "
                    "finding to .claude/audits/AUDITOR_SCORECARD.jsonl (Step 2.5b) with "
                    f'"run_id": "{state.run_id}"'
                )
    elif layer == "L3":
        to_fix = _need(counts, "to_fix", layer)
        confirmed = _layer_count(state, "L2.5", "confirmed")
        if confirmed is not None and to_fix > confirmed:
            raise RecordRefused(
                f"L3 --to-fix {to_fix} exceeds the {confirmed} findings L2.5 confirmed"
            )


def record(layer: str, *, status: str = "done", detail: str = "", fixes: int | None = None,
           counts: dict | None = None, source: str = "cli",
           root: str | pathlib.Path = ".") -> PipelineState:
    """Record that `layer` was reached and given a verdict.

    Raises RecordRefused when the layer is out of order, is a tool layer recorded
    by hand, or carries counts that do not agree with the run.
    """
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r} (expected one of {', '.join(LAYERS)})")
    state = load(root)
    if state is None:
        raise ValueError(
            "no CCA run is open; run `python -m cca_checks pipeline start --tier <TIER>` "
            "at Step 0.6 (Step 0.4 for a hunt) before recording a layer"
        )
    entry = {"status": status, "detail": detail, "at": core.now()}
    if not state.legacy:
        if layer in core.TOOL_LAYERS and source != "tool":
            raise RecordRefused(
                f"{layer} is recorded by its tool, not by hand: {core.TOOL_LAYERS[layer]}"
            )
        tree = core.fingerprint(root)
        raw = state.to_json()
        owed = core.fixes_owed(raw, tree) or (fixes or 0) > 0
        missing = [p for p in core.predecessors(layer, raw, owed) if p not in state.layers]
        if missing:
            raise RecordRefused(
                f"{layer} cannot be recorded before {', '.join(missing)}; re-read "
                f"{core.HEADINGS[missing[0]]} of audit-fix.md and run it first"
            )
        gate04 = state.layers.get("L0.4")
        if "L0.4" in core.predecessors(layer, raw, owed) and \
                str((gate04 or {}).get("detail", "")).strip().upper() != "PASS":
            raise RecordRefused(
                "L0.4 did not read PASS; a hunt that fails viability is aborted with "
                "`pipeline abort --reason`, never continued"
            )
        counts = {k: v for k, v in (counts or {}).items() if v is not None}
        _check_counts(layer, state, counts, root)
        state.seq += 1
        entry.update({"seq": state.seq, "source": source})
        if counts:
            entry["counts"] = counts
        if layer in core.FIX_VERDICT_LAYERS:
            entry["tree"] = tree
    state.layers[layer] = entry
    if fixes is not None:
        state.fixes_applied = int(fixes)
    _write(state, root)
    return state


def _tree(state: PipelineState, root) -> str | None:
    return None if state.legacy else core.fingerprint(root)


def required_layers(state: PipelineState, root: str | pathlib.Path = ".") -> tuple[str, ...]:
    if state.legacy:
        required = list(core.LEGACY_CORE_REQUIRED)
        if state.fixes_applied > 0:
            required.extend(core.LEGACY_FIX_REQUIRED[state.tier])
        return tuple(required)
    raw = state.to_json()
    return core.required_layers(raw, core.fixes_owed(raw, _tree(state, root)))


def outstanding(state: PipelineState, root: str | pathlib.Path = ".") -> list[str]:
    """What this run still owes before it may close. Legacy runs owe nothing here."""
    if state.legacy:
        return []
    return core.outstanding(state.to_json(), _tree(state, root))


def verify_commit_allowed(state: PipelineState | None,
                          root: str | pathlib.Path = ".") -> CommitDecision:
    if state is None:
        return CommitDecision(allowed=True, reasons=["no CCA run is open"])
    reasons = core.commit_reasons(state.to_json(), _tree(state, root))
    return CommitDecision(allowed=not reasons, reasons=reasons, run_id=state.run_id)


def abort(reason: str, root: str | pathlib.Path = ".") -> pathlib.Path | None:
    """Close an open run WITHOUT satisfying its gates, leaving a trail."""
    path = state_path(root)
    if not path.exists():
        return None
    try:
        state = load(root)
        run_id = state.run_id if state else "unknown"
    except ValueError:
        run_id = "unparseable"
    log = core.append_log(root, f"| {core.now()} | run {run_id} | ABORTED | {reason} |")
    path.unlink()
    return log


def close(root: str | pathlib.Path = ".") -> pathlib.Path | None:
    """Retire a run that is complete. An incomplete run is refused, not logged."""
    path = state_path(root)
    if not path.exists():
        return None
    state = load(root)
    missing = outstanding(state, root)
    if missing:
        raise RecordRefused(
            "this run is not complete: " + "; ".join(missing)
            + ". Finish it, or retire it with `python -m cca_checks pipeline abort --reason`."
        )
    recorded = ", ".join(sorted(state.layers))
    log = core.append_log(
        root,
        f"| {core.now()} | run {state.run_id} | COMPLETE | tier {state.tier} | "
        f"layers: {recorded} |",
    )
    path.unlink()
    return log
```

- [ ] **Step 7: Run green**

Run: `python -m pytest tests/test_pipeline_state.py tests/test_gate_core.py -q` then `python -m pytest tests/test_gate_holes.py::test_hole7_close_refuses_an_incomplete_run -q`
Expected: all pass. Holes 1 to 6 still fail (guard untouched). Run each docstring mutation in `test_pipeline_state.py`, confirm the assertion failure, revert, record the receipt.

- [ ] **Step 8: Commit**

```bash
git add cca_checks/pipeline_state.py tests/test_pipeline_state.py tests/test_gate_holes.py
git commit -m "feat(gate): record order, counts and tree per layer; close refuses an incomplete run

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: CLI

**Files:**
- Modify: `cca_checks/__main__.py` (`_pipeline` and the `pipeline` parsers)
- Test: `tests/test_pipeline_cli.py` (create)

**Interfaces:**
- Consumes: `pipeline_state.record(..., counts=...)`, `RecordRefused`, `outstanding`, `close`.
- Produces: `pipeline record <L> [--unique N] [--confirmed N] [--fp N] [--uncertain N] [--duplicate N] [--to-fix N]`; refusals print `{"refused": <layer|"close">, "reason": ...}` to stderr and exit 2; `pipeline status --require-complete` exits 2 when the open run owes anything.

- [ ] **Step 1: Write the failing tests**

```python
"""The pipeline CLI: count flags, refusals as exit 2, status --require-complete."""

from __future__ import annotations

import json

from cca_checks.__main__ import main


def _cli(capsys, *args) -> tuple[int, dict, dict]:
    code = main(["pipeline", *args])
    out, err = capsys.readouterr()
    return code, json.loads(out) if out.strip() else {}, json.loads(err) if err.strip() else {}


def test_out_of_order_record_exits_2_with_the_reason(capsys, git_repo):
    """Baseline d2317de: exit 0. Red there on `assert 0 == 2`."""
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    code, _, err = _cli(capsys, "record", "L6", "--detail", "APPROVED", "--root", r)
    assert code == 2 and err["refused"] == "L6" and "L3" in err["reason"]


def test_count_flags_reach_the_state(capsys, git_repo):
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    _cli(capsys, "record", "L1", "--root", r)
    code, out, _ = _cli(capsys, "record", "L2", "--unique", "0", "--root", r)
    assert code == 0 and out["recorded"] == "L2"
    raw = json.loads((git_repo / ".claude/audits/run-state.json").read_text(encoding="utf-8"))
    assert raw["layers"]["L2"]["counts"] == {"unique": 0}


def test_status_require_complete_exits_2_while_layers_are_owed(capsys, git_repo):
    """Mutation: drop the require-complete branch. Fails: exit 0."""
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    code, out, _ = _cli(capsys, "status", "--require-complete", "--root", r)
    assert code == 2 and out["outstanding_to_close"]


def test_close_on_an_incomplete_run_exits_2(capsys, git_repo):
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    code, _, err = _cli(capsys, "close", "--root", r)
    assert code == 2 and err["refused"] == "close"


def test_n_a_is_still_accepted_for_a_layer_this_run_does_not_require(capsys, git_repo):
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    _cli(capsys, "record", "L1", "--root", r)
    _cli(capsys, "record", "L2", "--unique", "0", "--root", r)
    code, _, _ = _cli(capsys, "record", "L2.5", "--status", "n/a", "--confirmed", "0",
                      "--fp", "0", "--uncertain", "0", "--duplicate", "0", "--root", r)
    assert code == 0
    code, out, _ = _cli(capsys, "status", "--require-complete", "--root", r)
    assert any("L2.5 is recorded as n/a" in x for x in out["outstanding_to_close"])
```

- [ ] **Step 2: Red proof**

Copy to the baseline and run: `cp tests/test_pipeline_cli.py ../cca-baseline/tests/ && (cd ../cca-baseline && python -m pytest tests/test_pipeline_cli.py::test_out_of_order_record_exits_2_with_the_reason -q -p no:cacheprovider)`.
Expected: FAILED on `assert code == 2` (code 0). The other tests fail here on argparse (`unrecognized arguments: --unique`), which is not a proof; they are proved by the mutation in their docstrings after Step 4.

- [ ] **Step 3: Implement**

In the `pipeline record` parser, after `--fixes`:

```python
    for flag, help_text in (
        ("--unique", "L2: unique findings after consolidation"),
        ("--confirmed", "L2.5: CONFIRMED verdicts"),
        ("--fp", "L2.5: FALSE_POSITIVE verdicts"),
        ("--uncertain", "L2.5: UNCERTAIN verdicts"),
        ("--duplicate", "L2.5: DUPLICATE verdicts"),
        ("--to-fix", "L3: findings the plan will fix"),
    ):
        pl_record.add_argument(flag, type=int, default=None, help=help_text)
```

In the `status` parser:

```python
    pl_status.add_argument("--require-complete", action="store_true",
                           help="exit 2 while the open run still owes a layer (Codex has no Stop hook)")
```

Replace the body of `_pipeline` with:

```python
    try:
        if a.pipeline_cmd == "start":
            state = pipeline_state.start(
                a.tier, a.mode, no_fix=a.no_fix, run_id=a.run_id, root=a.root
            )
            print(json.dumps({"started": state.to_json()}))
            return 0

        if a.pipeline_cmd == "record":
            counts = {"unique": a.unique, "confirmed": a.confirmed, "fp": a.fp,
                      "uncertain": a.uncertain, "duplicate": a.duplicate,
                      "to_fix": a.to_fix}
            try:
                state = pipeline_state.record(
                    a.layer, status=a.status, detail=a.detail, fixes=a.fixes,
                    counts=counts, root=a.root,
                )
            except pipeline_state.RecordRefused as exc:
                print(json.dumps({"refused": a.layer, "reason": str(exc)}), file=sys.stderr)
                return 2
            decision = pipeline_state.verify_commit_allowed(state, root=a.root)
            print(json.dumps({
                "recorded": a.layer,
                "run_id": state.run_id,
                "commit_allowed": decision.allowed,
                "outstanding": decision.reasons if not decision.allowed else [],
            }))
            return 0

        if a.pipeline_cmd == "status":
            state = pipeline_state.load(a.root)
            decision = pipeline_state.verify_commit_allowed(state, root=a.root)
            owed = pipeline_state.outstanding(state, a.root) if state else []
            print(json.dumps({
                "open": state is not None,
                "run": state.to_json() if state else None,
                "required": list(pipeline_state.required_layers(state, a.root)) if state else [],
                "commit_allowed": decision.allowed,
                "outstanding": decision.reasons if not decision.allowed else [],
                "outstanding_to_close": owed,
            }))
            return 2 if (a.require_complete and owed) else 0

        if a.pipeline_cmd == "abort":
            log = pipeline_state.abort(a.reason, root=a.root)
            print(json.dumps({"aborted": log is not None, "logged_to": str(log) if log else None}))
            return 0

        try:
            log = pipeline_state.close(root=a.root)
        except pipeline_state.RecordRefused as exc:
            print(json.dumps({"refused": "close", "reason": str(exc)}), file=sys.stderr)
            return 2
        print(json.dumps({"closed": log is not None, "logged_to": str(log) if log else None}))
        return 0
    except ValueError as exc:
        # A bad tier, an unknown layer, or an unparseable state file. Exit
        # non-zero so a shell chain stops here: a recording step that silently
        # failed would leave the run looking less complete than it is, or more.
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1
```

- [ ] **Step 4: Green, then mutation proofs**

Run: `python -m pytest tests/test_pipeline_cli.py tests/test_cli.py -q`
Expected: pass. Apply the docstring mutation, see it fail in the assertion, revert, record receipts.

- [ ] **Step 5: Commit**

```bash
git add cca_checks/__main__.py tests/test_pipeline_cli.py
git commit -m "feat(cli): count flags, refusals exit 2, status --require-complete

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Commit guard on the core

**Files:**
- Modify: `cca_checks/plugin/hooks/cca_pipeline_guard.py`
- Test: `tests/test_pipeline_guard.py`

**Interfaces:**
- Consumes: `cca_gate_core.commit_reasons`, `fingerprint`, `is_legacy`, `read_state`.
- Produces: `evaluate(state: dict, tree: str | None = None) -> list[str]` (kept for the drift pin), `find_root`, `read_command`, `main`.

- [ ] **Step 1: Update the guard tests**

In `tests/test_pipeline_guard.py`:
- Replace `_repo(tmp_path)` callers in tests that need a run with the `git_repo` fixture, and `test_guard_allows_the_commit_once_the_gates_are_satisfied` with:

```python
def test_guard_allows_the_commit_once_the_gates_are_satisfied(monkeypatch, git_repo):
    state = ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 0}, root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    assert state.run_id
    assert _run(monkeypatch, git_repo, "git commit -m x") == 0
```

- Keep `_MATRIX` and `test_guard_agrees_with_pipeline_state_across_a_matrix` unchanged (the states carry no `schema`, so they now pin the legacy path in both).
- Add:

```python
def test_guard_refuses_when_the_core_is_missing_and_a_run_is_open(monkeypatch, git_repo, tmp_path_factory):
    """A guard copied without its core must not fail open. Mutation: return 0 on ImportError. Fails."""
    import shutil
    hooks = tmp_path_factory.mktemp("hooks")
    shutil.copy(_GUARD_PATH, hooks / "cca_pipeline_guard.py")
    spec = importlib.util.spec_from_file_location("lonely_guard", hooks / "cca_pipeline_guard.py")
    lonely = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lonely)
    ps.start("FAST", root=git_repo)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("sys.stdin", io.StringIO("git commit -m x"))
    assert lonely.main() == 2


def test_guard_without_its_core_is_silent_when_no_run_is_open(monkeypatch, git_repo, tmp_path_factory):
    import shutil
    hooks = tmp_path_factory.mktemp("hooks")
    shutil.copy(_GUARD_PATH, hooks / "cca_pipeline_guard.py")
    spec = importlib.util.spec_from_file_location("lonely_guard2", hooks / "cca_pipeline_guard.py")
    lonely = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(lonely)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("sys.stdin", io.StringIO("git commit -m x"))
    assert lonely.main() == 0
```

- [ ] **Step 2: Run to see red**

Run: `python -m pytest tests/test_pipeline_guard.py tests/test_gate_holes.py -q`
Expected: holes 1 to 6 fail (`assert 0 == 2`). The two "lonely guard" tests may pass already for an unrelated reason (the current guard embeds its tables); they are proved by the mutation in their docstring after Step 3, not here.

- [ ] **Step 3: Implement**

In `cca_pipeline_guard.py`, delete `CORE_REQUIRED`, `FIX_REQUIRED`, `APPROVING_VERDICT`, `BAD_STATUS` and the body of `evaluate`; update the module docstring's "WHY IT IS STANDALONE" paragraph to say the rules live in `cca_gate_core.py` beside it, loaded by path; add:

```python
import importlib.util

_HERE = pathlib.Path(__file__).resolve().parent


def _load_core():
    path = _HERE / "cca_gate_core.py"
    spec = importlib.util.spec_from_file_location("cca_gate_core", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def evaluate(state: dict, tree: str | None = None) -> list[str]:
    """Reasons this run may not commit. Empty list means allowed."""
    return _load_core().commit_reasons(state, tree)
```

and in `main`, replace everything after `if root is None: return 0` with:

```python
    try:
        core = _load_core()
    except (ImportError, OSError, SyntaxError) as exc:
        # A run is open, so failing open here would ship unverified fixes.
        return refuse(root, [
            f"cca_gate_core.py could not be loaded next to this guard ({exc}); reinstall "
            "with `python -m cca_checks.plugin.cli install --target .`"
        ])
    try:
        state = core.read_state(root)
    except ValueError as exc:
        return refuse(root, [f"{exc}; a run was started and cannot be shown to have finished"])
    if state is None:
        return 0
    tree = None if core.is_legacy(state) else core.fingerprint(root)
    reasons = core.commit_reasons(state, tree)
    if reasons:
        return refuse(root, reasons)
    return 0
```

Keep `find_root`, `read_command`, `refuse` and `_COMMIT_RE` as they are. Note the existing "unparseable" test message must still contain the word it asserts on (it asserts exit 2 only).

- [ ] **Step 4: Green**

Run: `python -m pytest tests/test_pipeline_guard.py tests/test_gate_holes.py tests/test_gate_core.py tests/test_pipeline_state.py -q`
Expected: all pass, including holes 1 to 6.

- [ ] **Step 5: Commit**

```bash
git add cca_checks/plugin/hooks/cca_pipeline_guard.py tests/test_pipeline_guard.py
git commit -m "feat(guard): the commit gate reads the shared rules and the tree

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Stop hook, snippet and install warning

**Files:**
- Create: `cca_checks/plugin/hooks/cca_stop_guard.py`
- Modify: `cca_checks/plugin/__init__.py` (`_GUARD_NAME` block, `_guard_is_registered`, `hook_snippet`, the warning in `install`)
- Test: `tests/test_stop_guard.py` (create), `tests/test_plugin_install.py`

**Interfaces:**
- Consumes: `cca_gate_core.read_state`, `write_state`, `append_log`, `outstanding`, `fingerprint`, `is_legacy`, `STOP_REFUSAL_CAP`, `now`.
- Produces: `cca_stop_guard.main() -> int` (always 0; blocks by printing `{"decision": "block", "reason": ...}`), `plugin._STOP_GUARD_NAME = "cca_stop_guard.py"`.

Contract, verified 2026-10-10 against https://code.claude.com/docs/en/hooks: Stop has no matcher; stdin carries `cwd`, `stop_hook_active`, `background_tasks`; exit 0 with `{"decision": "block", "reason": "..."}` keeps Claude working and shows it the reason; Stop does not fire on a user interrupt; Claude Code itself overrides a block after eight consecutive continuations.

- [ ] **Step 1: Write the failing tests**

```python
"""The Stop hook: no ending the turn in the middle of a CCA run, three times at most."""

from __future__ import annotations

import io
import json

from cca_checks import pipeline_state as ps
from conftest import load_hook

stop = load_hook("cca_stop_guard.py")


def _stop(monkeypatch, capsys, repo, **payload) -> dict | None:
    body = {"hook_event_name": "Stop", "cwd": str(repo), "stop_hook_active": False, **payload}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(body)))
    assert stop.main() == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_no_open_run_never_blocks(monkeypatch, capsys, git_repo):
    assert _stop(monkeypatch, capsys, git_repo) is None


def test_an_incomplete_run_blocks_with_the_next_owed_layer(monkeypatch, capsys, git_repo):
    """Mutation: return 0 before the decision. Fails: None is not a block."""
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    out = _stop(monkeypatch, capsys, git_repo)
    assert out["decision"] == "block" and "L2" in out["reason"]


def test_three_refusals_then_it_lets_go_and_logs_once(monkeypatch, capsys, git_repo):
    """Mutation: cap = 0. Fails: first stop is not blocked."""
    ps.start("FAST", root=git_repo)
    blocks = [_stop(monkeypatch, capsys, git_repo) for _ in range(5)]
    assert [b is not None for b in blocks] == [True, True, True, False, False]
    log = (git_repo / ".claude" / "audits" / "EXECUTION_LOG.md").read_text(encoding="utf-8")
    assert log.count("STOPPED_INCOMPLETE") == 1


def test_stop_guard_allows_while_background_tasks_run(monkeypatch, capsys, git_repo):
    """Review Focus 3. Mutation: ignore background_tasks. Fails: blocked, and a refusal is spent."""
    ps.start("FAST", root=git_repo)
    assert _stop(monkeypatch, capsys, git_repo, background_tasks=[{"id": "a1"}]) is None
    assert ps.load(git_repo).stop_refusals == 0


def test_an_architect_block_lets_the_turn_end(monkeypatch, capsys, git_repo):
    """L6 BLOCKED waits for a human. Mutation: drop the BLOCKED branch. Fails: blocked."""
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 0}, root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, root=git_repo)
    ps.record("L6", detail="BLOCKED", root=git_repo)
    assert _stop(monkeypatch, capsys, git_repo) is None


def test_a_complete_run_that_is_still_open_asks_for_close(monkeypatch, capsys, git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 0}, root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    out = _stop(monkeypatch, capsys, git_repo)
    assert "pipeline close" in out["reason"]


def test_a_legacy_run_is_left_alone(monkeypatch, capsys, git_repo):
    path = ps.state_path(git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "old", "tier": "FAST", "layers": {}}), encoding="utf-8")
    assert _stop(monkeypatch, capsys, git_repo) is None
```

Add to `tests/test_plugin_install.py`:

```python
def test_hook_snippet_arms_both_hooks(tmp_path):
    snippet = json.loads(plugin.hook_snippet(tmp_path))["hooks"]
    assert "cca_pipeline_guard.py" in snippet["PreToolUse"][0]["hooks"][0]["command"]
    assert "cca_stop_guard.py" in snippet["Stop"][0]["hooks"][0]["command"]
    assert "matcher" not in snippet["Stop"][0]


def test_hook_snippet_timeouts_exceed_the_fingerprint_bound(tmp_path):
    """Review Focus 5. A hook that times out fails open. Baseline d2317de: 15 s. Red there."""
    from cca_checks import pipeline_state as ps
    snippet = json.loads(plugin.hook_snippet(tmp_path))["hooks"]
    for event in ("PreToolUse", "Stop"):
        assert snippet[event][0]["hooks"][0]["timeout"] > ps.core.FINGERPRINT_TIMEOUT_S
```

(Add `import json` at the top of the file if absent.) Update `test_install_warns_when_the_commit_guard_is_not_registered` so the re-armed settings file is `plugin.hook_snippet(tmp_path)` (already the case) and still expects no warning; add one assertion that a settings file naming only `cca_pipeline_guard.py` still warns:

```python
    settings.write_text('{"x": "cca_pipeline_guard.py"}', encoding="utf-8")
    half = plugin.install(tmp_path)
    assert any("stop guard" in w for w in half.warnings), half.warnings
```

- [ ] **Step 2: Red**

Run: `python -m pytest tests/test_stop_guard.py tests/test_plugin_install.py -q`
Expected: `test_stop_guard.py` errors at collection (file missing; not a proof, the docstring mutations are). `test_hook_snippet_timeouts_exceed_the_fingerprint_bound` fails on `KeyError: 'Stop'`; to get its real red, also run it in the baseline worktree after copying, where `ps.core` does not exist: that is an AttributeError and NOT a proof, so prove it instead by mutation (set the PreToolUse timeout back to 15) after Step 4.

- [ ] **Step 3: Implement the Stop hook**

`cca_checks/plugin/hooks/cca_stop_guard.py`:

```python
#!/usr/bin/env python
"""Stop hook: do not let the agent end its turn in the middle of a CCA run.

The commit guard only fires on `git commit`. A `no-fix` run never commits, and a
run that stops to summarise after L5 goes green never reaches the commit either,
so neither ever met a gate. This hook meets them where they stop.

BEHAVIOUR (always exit 0; a block is the JSON decision on stdout)
  background tasks running         -> allow: the orchestrator is waiting on its
                                      own subagents, which is not stopping
  no run, legacy run, core missing -> allow: a stop guard must never trap a
                                      session over its own installation
  L6 recorded BLOCKED              -> allow: the run is waiting for a human
  incomplete, refusals < 3         -> block with what is owed, count the refusal
  complete but still open          -> block: "run pipeline close" (counted too)
  refusals >= 3                    -> allow, log STOPPED_INCOMPLETE once

The cap lives in the run state so it is per run and visible. Claude Code's own
limit (eight consecutive continuations) is a backstop, not the design.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load_core():
    path = _HERE / "cca_gate_core.py"
    spec = importlib.util.spec_from_file_location("cca_gate_core", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def find_root(start: pathlib.Path) -> pathlib.Path | None:
    """Same boundary rule as the commit guard: stop at the repository root."""
    for candidate in [start, *start.parents]:
        if (candidate / ".claude" / "audits" / "run-state.json").exists():
            return candidate
        if (candidate / ".git").exists():
            return None
    return None


def _block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def main() -> int:
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        return 0
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("background_tasks"):
        return 0

    root = find_root(pathlib.Path(payload.get("cwd") or os.getcwd()).resolve())
    if root is None:
        return 0
    try:
        core = _load_core()
    except (ImportError, OSError, SyntaxError):
        return 0
    try:
        state = core.read_state(root)
    except ValueError as exc:
        return _block(
            f"The CCA run state is unreadable ({exc}). Retire it with "
            '`python -m cca_checks pipeline abort --reason "<why>"`.'
        )
    if state is None or core.is_legacy(state):
        return 0
    gate = state.get("layers", {}).get("L6")
    if isinstance(gate, dict) and str(gate.get("detail", "")).strip().upper() == "BLOCKED":
        return 0

    reasons = core.outstanding(state, core.fingerprint(root))
    refusals = int(state.get("stop_refusals", 0) or 0)
    if refusals >= core.STOP_REFUSAL_CAP:
        if not state.get("stop_logged"):
            summary = "; ".join(reasons) or "complete but not closed"
            core.append_log(
                root,
                f"| {core.now()} | run {state.get('run_id', '?')} | STOPPED_INCOMPLETE | "
                f"{summary} |",
            )
            state["stop_logged"] = True
            core.write_state(root, state)
        return 0

    state["stop_refusals"] = refusals + 1
    core.write_state(root, state)
    if reasons:
        return _block(
            "The CCA run in this checkout is not finished:\n  - "
            + "\n  - ".join(reasons)
            + "\nContinue with the first owed layer, re-reading its section of audit-fix.md. "
            'If the run must end here, retire it: python -m cca_checks pipeline abort --reason "<why>"'
        )
    return _block("The CCA run is complete but still open. Close it: python -m cca_checks pipeline close")


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Implement the snippet and the warning**

In `cca_checks/plugin/__init__.py`:

```python
_GUARD_NAME = "cca_pipeline_guard.py"
_STOP_GUARD_NAME = "cca_stop_guard.py"
#: Above cca_gate_core.FINGERPRINT_TIMEOUT_S (30 s). A hook that times out is a
#: non-blocking error in Claude Code, which would let a commit through.
_HOOK_TIMEOUT_S = 60
```

In `_guard_is_registered`, change the check to require both names in the same file:

```python
            text = path.read_text(encoding="utf-8")
            if _GUARD_NAME in text and _STOP_GUARD_NAME in text:
                return True
```

and its docstring's first line to `Are the commit guard and the stop guard named by a settings file that would run them?`.

Replace `hook_snippet` with:

```python
def hook_snippet(target: str | pathlib.Path = ".") -> str:
    """The settings.json fragment that arms the commit guard and the stop guard."""
    hooks_dir = pathlib.Path(target).resolve() / ".claude" / "hooks"
    guard = hooks_dir / _GUARD_NAME
    stop = hooks_dir / _STOP_GUARD_NAME
    return json.dumps(
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'python "{guard.as_posix()}"',
                                "timeout": _HOOK_TIMEOUT_S,
                                "statusMessage": "Checking the CCA pipeline gate",
                            }
                        ],
                    }
                ],
                "Stop": [
                    {
                        "hooks": [
                            {
                                "type": "command",
                                "command": f'python "{stop.as_posix()}"',
                                "timeout": _HOOK_TIMEOUT_S,
                                "statusMessage": "Checking the CCA run is finished",
                            }
                        ],
                    }
                ],
            }
        },
        indent=2,
    )
```

Change the install warning text to:

```python
            "the commit guard and stop guard are installed but not both registered; add "
            "the hooks from `cca-audit install --print-hook` to your settings.json, or "
            "the pipeline's layers stay advisory"
```

(It still contains "commit guard", which the existing shadowing filter at `tests/test_plugin_install.py:183` relies on.)

- [ ] **Step 5: Green, then mutation proofs**

Run: `python -m pytest tests/test_stop_guard.py tests/test_plugin_install.py tests/test_plugin_cli.py -q`
Expected: pass. Apply each docstring mutation, confirm the assertion failure, revert, record.

- [ ] **Step 6: Commit**

```bash
git add cca_checks/plugin/hooks/cca_stop_guard.py cca_checks/plugin/__init__.py tests/test_stop_guard.py tests/test_plugin_install.py
git commit -m "feat(hooks): a Stop hook that will not let a CCA run end half done

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Tools record their own layers

**Files:**
- Modify: `cca_checks/plugin/tools/cca_scorecard.py` (`main`)
- Modify: `cca_checks/plugin/tools/cca_tautology_check.py` (`main`, new `_record_l5_6`)
- Test: `cca_checks/plugin/tools/test_cca_scorecard.py`, `cca_checks/plugin/tools/test_cca_tautology_check.py`

**Interfaces:**
- Consumes: `cca_checks.pipeline_state.record(layer, ..., source="tool", root=".")`, `RecordRefused`.
- Produces: `cca_scorecard.py --record`; `cca_tautology_check.py verify --record [--no-proofs]`; `cca_tautology_check._l5_6_entry(results) -> tuple[str, str]` returning `(status, detail)`.

- [ ] **Step 1: Write the failing tests**

Append to `test_cca_scorecard.py`:

```python
def test_record_writes_l2_6_as_a_tool_layer(git_repo, monkeypatch, capsys):
    """Mutation: pass source="cli". Fails: RecordRefused, exit 2."""
    from cca_checks import pipeline_state as ps
    import cca_scorecard
    ps.start("STANDARD", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 0}, root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("sys.argv", ["cca_scorecard.py", "--json", "--record"])
    assert cca_scorecard.main() == 0
    entry = ps.load(git_repo).layers["L2.6"]
    assert entry["source"] == "tool" and entry["status"] == "done"
```

Append to `test_cca_tautology_check.py`:

```python
def test_l5_6_entry_fails_on_any_non_proof():
    """Mutation: always "done". Fails on the TAUTOLOGICAL case."""
    import cca_tautology_check as tc
    red = tc.ProofResult("FIX-1", "t::a", tc.RED, "")
    taut = tc.ProofResult("FIX-2", "t::b", tc.TAUTOLOGICAL, "")
    assert tc._l5_6_entry([red]) == ("done", "1 RED, 0 tautological, 0 inconclusive")
    assert tc._l5_6_entry([red, taut])[0] == "failed"


def test_no_proofs_record_needs_no_snapshot(git_repo, monkeypatch):
    from cca_checks import pipeline_state as ps
    import cca_tautology_check as tc
    ps.start("STANDARD", root=git_repo)
    for layer, counts in (("L1", None), ("L2", {"unique": 0})):
        ps.record(layer, counts=counts, root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)
    ps.record("L2.6", source="tool", root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, root=git_repo)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, root=git_repo)
    ps.record("L5", root=git_repo)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("sys.argv", ["cca_tautology_check.py", "verify", "--no-proofs", "--record"])
    assert tc.main() == 0
    assert ps.load(git_repo).layers["L5.6"]["detail"] == "0 proofs claimed (no P1 to prove)"
```

These tool test files live under `cca_checks/plugin/tools/`, so `git_repo` from `tests/conftest.py` is not visible there. Add `cca_checks/plugin/tools/conftest.py` containing `from tests.conftest import git_repo  # noqa: F401` only if `tests` is importable as a package; otherwise copy the `_init` helper and `git_repo` fixture into that conftest verbatim. Check with `python -c "import tests"`; the repo has no `tests/__init__.py`, so copy.

- [ ] **Step 2: Red**

Run: `python -m pytest cca_checks/plugin/tools -q`
Expected: `unrecognized arguments: --record` (SystemExit 2) and `AttributeError: _l5_6_entry`. Not proofs; the docstring mutations are.

- [ ] **Step 3: Implement the scorecard flag**

In `cca_scorecard.main`, add `ap.add_argument("--record", action="store_true", help="record L2.6 in the open CCA run")` and, before `return 0`:

```python
    if a.record:
        try:
            from cca_checks import pipeline_state
        except ImportError:
            print("cannot record L2.6: cca_checks is not importable here "
                  "(pip install cca-audit)", file=sys.stderr)
            return 3
        try:
            pipeline_state.record(
                "L2.6", detail=f"{len(rep.cells)} cells scored, {rep.rows_scored} rows",
                source="tool",
            )
        except ValueError as exc:
            print(f"cannot record L2.6: {exc}", file=sys.stderr)
            return 2
```

(Add `import sys` if absent.)

- [ ] **Step 4: Implement the tautology flags**

In `cca_tautology_check.py` add:

```python
def _l5_6_entry(results: list[ProofResult]) -> tuple[str, str]:
    red = sum(r.verdict == RED for r in results)
    taut = sum(r.verdict == TAUTOLOGICAL for r in results)
    inc = sum(r.verdict == INCONCLUSIVE for r in results)
    status = "done" if taut == 0 and inc == 0 else "failed"
    return status, f"{red} RED, {taut} tautological, {inc} inconclusive"


def _record(status: str, detail: str) -> int:
    try:
        from cca_checks import pipeline_state
    except ImportError:
        print("cannot record L5.6: cca_checks is not importable here", file=sys.stderr)
        return 3
    try:
        pipeline_state.record("L5.6", status=status, detail=detail, source="tool")
    except ValueError as exc:
        print(f"cannot record L5.6: {exc}", file=sys.stderr)
        return 2
    return 0
```

In `main`, on the `verify` parser add `--record` (`store_true`) and `--no-proofs` (`store_true`, help `"no CONFIRMED P1 needs a proof; records L5.6 with zero proofs"`). Before the `proofs` loop:

```python
    if a.no_proofs:
        if a.proof:
            raise SystemExit("--no-proofs and --proof are mutually exclusive")
        return _record("done", "0 proofs claimed (no P1 to prove)") if a.record else 0
```

After the results are printed, replace the final return with:

```python
    exit_code = 0 if all(r.is_proof for r in results) else 1
    if a.record:
        status, detail = _l5_6_entry(results)
        recorded = _record(status, detail)
        if recorded:
            return recorded
    return exit_code
```

Keep the existing exit-code semantics: read the current final lines of `main` first and preserve whatever it returns today when `--record` is absent.

- [ ] **Step 5: Green, mutation proofs, commit**

Run: `python -m pytest cca_checks/plugin/tools tests -q`
Expected: pass. Mutation receipts as docstrings say.

```bash
git add cca_checks/plugin/tools
git commit -m "feat(tools): the scorecard and the red-state proof record their own layers

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Prose, Codex skill, changelog

**Files:**
- Modify: `cca_checks/plugin/commands/audit-fix.md`
- Modify: `cca_checks/plugin/codex/cca-audit/SKILL.md`
- Modify: `CHANGELOG.md`
- Test: `tests/test_pipeline_prose.py` (create)

- [ ] **Step 1: Write the failing prose tests**

```python
"""The pipeline text must ask for exactly what the gate enforces.

Red at d2317de: every test here fails on its assertion against the 0.10.1 text.
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "cca_checks/plugin/commands/audit-fix.md").read_text(encoding="utf-8")
CODEX = (ROOT / "cca_checks/plugin/codex/cca-audit/SKILL.md").read_text(encoding="utf-8")


def test_contract_lists_l0_4():
    assert re.search(r"^\| 0\.4 \|", PIPELINE, re.M)


def test_tool_layers_are_never_recorded_by_hand():
    assert "pipeline record L2.6" not in PIPELINE
    assert "pipeline record L5.6" not in PIPELINE
    assert "--json --record" in PIPELINE
    assert "verify --no-proofs --record" in PIPELINE


def test_count_flags_appear_where_the_gate_requires_them():
    assert "pipeline record L2 --unique" in PIPELINE
    assert "--confirmed" in PIPELINE and "--duplicate" in PIPELINE
    assert "pipeline record L3 --to-fix" in PIPELINE


def test_ledger_run_id_is_the_pipeline_run_id():
    assert '"run_id": <short>' not in PIPELINE
    assert "the `run_id` printed by `pipeline start`" in PIPELINE


def test_no_fix_runs_close_after_recording_l2_5():
    # find(), not index(): at d2317de the red must be this assertion, not a ValueError.
    section = PIPELINE[PIPELINE.find("### Step 2.5b"):PIPELINE.find("## Step 2.6")]
    record_at = section.find("pipeline record L2.5")
    stop_at = section.find("If `no-fix`")
    assert 0 <= record_at < stop_at
    assert "pipeline close" in section[stop_at:]


def test_hunt_opens_the_run_at_step_0_4():
    section = PIPELINE[PIPELINE.find("## Step 0.4"):PIPELINE.find("## Step 0.5")]
    assert "pipeline start --tier DEEP --mode HUNT" in section
    assert "pipeline record L0.4" in section


def test_codex_checks_completeness_before_reporting():
    assert "pipeline status --require-complete" in CODEX
```

- [ ] **Step 2: Red at baseline**

```bash
cp tests/test_pipeline_prose.py ../cca-baseline/tests/ && (cd ../cca-baseline && python -m pytest tests/test_pipeline_prose.py -q -p no:cacheprovider)
```

Expected: 7 failed, each on an `assert`. Record in the module docstring.

- [ ] **Step 3: Edit `audit-fix.md`**

1. Replace the Contract table rows (the `| 0.6 |` row through `| 7 |`) with:

```markdown
| 0.4 | viability *(HUNT only)* | `pipeline start --tier DEEP --mode HUNT`, then `pipeline record L0.4 --detail PASS\|REJECT` |
| 0.6 | tier chosen | `pipeline start --tier <TIER> [--no-fix]` *(DIFF mode; a hunt already started at 0.4)* |
| 1 | parallel auditors | `pipeline record L1 --detail "<N> auditors"` |
| 2 | consolidate | `pipeline record L2 --unique <U> --detail "<raw> raw"` |
| 2.5 | **verify findings** | `pipeline record L2.5 --confirmed <C> --fp <F> --uncertain <U> --duplicate <D>` |
| 2.6 | scorecard *(STD/DEEP)* | the scorecard records it: `cca_scorecard.py --json --record` |
| 3 | fix plan | `pipeline record L3 --to-fix <K> --detail "<D> deferred"` |
| 4 | implement | `pipeline record L4 --fixes <N> --detail "<N> fixes"` |
| 5 | re-verify | `pipeline record L5 --detail "tests green"` |
| 5.5 | **regression diff** *(STD/DEEP)* | `pipeline record L5.5 --detail "all SAFE"` |
| 5.6 | **red-state proof** *(STD/DEEP)* | the checker records it: `cca_tautology_check.py verify ... --record` |
| 6 | **architect gate** | `pipeline record L6 --detail APPROVED\|REVISE\|BLOCKED` |
| 7 | commit | `pipeline close` |
```

and replace the paragraph under it that begins `Every command above is prefixed` with:

```markdown
Every command above is prefixed `python -m cca_checks `. Recording is not paperwork, and since 0.11
it is not optional at any step. `record` refuses a layer whose predecessors are missing and names the
section to re-read; the commit guard refuses `git commit`, and `pipeline close` refuses to retire the
run, until every layer the run owes reads `done`. The gate learns that code changed from the working
tree itself, so an unrecorded L4 does not switch off L5 to L5.6, and every verdict from L5 onward is
stale the moment the tree changes after it. A Stop hook refuses to let the turn end while the run is
open (three times, then it logs `STOPPED_INCOMPLETE`). If the run must be abandoned, say so on the
record with `python -m cca_checks pipeline abort --reason "<why>"`.
```

2. In § Step 0.4, after the gate table and before the `gh repo view` block, insert:

````markdown
**Open the run here, before any check**, so the pre-flight is on the record:

```bash
python -m cca_checks pipeline start --tier DEEP --mode HUNT
# ... the five checks below ...
python -m cca_checks pipeline record L0.4 --detail PASS     # or REJECT, then: pipeline abort --reason "REJECT: <gate>"
```
````

3. At Step 0.6 (`python -m cca_checks pipeline start --tier <TIER> [--mode HUNT] [--no-fix]`), change to `python -m cca_checks pipeline start --tier <TIER> [--no-fix]   # a hunt already started at Step 0.4` and add one line after the block: `Keep the printed run_id: the disposition ledger rows at Step 2.5b carry it.`

4. Step 2 record line becomes `python -m cca_checks pipeline record L2 --unique <U> --detail "<R> raw, <U> unique"`.

5. Step 2.5b: replace `"run_id": <short>` with `"run_id": <run_id>` and add after the schema block: `` `run_id` is the `run_id` printed by `pipeline start`. `pipeline record L2.5` counts these rows and refuses when they do not match the verdicts (outcome rows from Step 6 have no `verdict` key and are not counted). ``

6. Move the line `If `no-fix`: STOP here. Report consolidated findings + verdicts.` to after the L2.5 record block, and expand it to:

````markdown
If `no-fix`: on STANDARD or DEEP run Step 2.6 (it records itself), then close the run and STOP.
Report consolidated findings and verdicts.

```bash
python -m cca_checks pipeline close
```
````

7. Step 2.5 record line becomes `python -m cca_checks pipeline record L2.5 --confirmed <C> --fp <F> --uncertain <U> --duplicate <D>`.

8. Step 2.6: the command becomes `python "$CCA" --json --record          # add --routing on to enable routing`, and the `**→ Record.**` block with `pipeline record L2.6` is replaced by: `**→ Recorded by the command above.** Do not record L2.6 by hand; the gate refuses a hand-written L2.6.`

9. Step 3 record line becomes `python -m cca_checks pipeline record L3 --to-fix <N> --detail "<D> deferred"`.

10. Step 5.6: add `--record` to the verify command; after the verdict table add ``No CONFIRMED P1 in this run? Record that instead: `python "$TC" verify --no-proofs --record`.`` and replace the `**→ Record.**` block with: `**→ Recorded by the command above.** A run with any TAUTOLOGICAL or INCONCLUSIVE proof is recorded as failed, which blocks the commit until it is reworked.`

11. Step 6 outcome row: replace `"run_id": <short>` with `"run_id": <run_id>`.

- [ ] **Step 4: Edit the Codex skill**

In `SKILL.md`, after the `## Apply fixes safely` section, add:

````markdown
## Gate before reporting

Codex has no hooks, so nothing stops a run from ending half done except this check. Before any final
summary, run:

```bash
python -m cca_checks pipeline status --require-complete
```

Exit 2 means the run still owes the layers listed under `outstanding_to_close`. Run them, or retire
the run on the record with `python -m cca_checks pipeline abort --reason "<why>"`. Never report a run
this command refuses as complete. This is weaker than the Claude Code Stop hook, which enforces the
same check without relying on the orchestrator to call it.
````

- [ ] **Step 5: Changelog**

Under `## Unreleased` in `CHANGELOG.md`:

```markdown
### Changed
- Every step of `/audit-fix` is now gated, not only L1, L2, L2.5 and L6. A HUNT owes L0.4 PASS; STANDARD and DEEP owe L2.6; every fixing run owes L3; L4 onward is owed whenever the working tree changed, whether or not L4 was recorded. In 0.10.x an unrecorded L4 switched off L5, L5.5 and L5.6.
- `pipeline record` checks order and refuses a layer whose predecessors are missing (exit 2, with the section to re-read). L2, L2.5 and L3 take counts (`--unique`, `--confirmed`, `--fp`, `--uncertain`, `--duplicate`, `--to-fix`) that must agree with each other and with the disposition ledger.
- Verdicts from L5 onward store the tree they judged; an edit after a verdict makes it stale.
- `--status n/a` no longer satisfies a required layer.
- `pipeline close` refuses an incomplete run; `no-fix` runs now close after verification instead of blocking the next commit.
- L2.6 and L5.6 are recorded by their tools (`--record`), never by hand.

### Added
- `cca_stop_guard.py`, a Stop hook that refuses to end the turn while a run is open, three times per run, then logs `STOPPED_INCOMPLETE`. `install --print-hook` now prints both hooks; re-run it and update settings.json.
- `pipeline status --require-complete` for Codex, which has no hooks.

### Compatibility
- A run started by 0.10.x finishes under 0.10.x rules.
```

- [ ] **Step 6: Green, regenerate local installs, commit**

Run: `python -m pytest tests/test_pipeline_prose.py tests/test_codex_skill.py tests/test_readme_counts_are_consistent.py -q`
Expected: pass.

```bash
git add cca_checks/plugin/commands/audit-fix.md cca_checks/plugin/codex/cca-audit/SKILL.md CHANGELOG.md tests/test_pipeline_prose.py
git commit -m "docs(pipeline): the prose asks for exactly what the gate enforces

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: Whole branch verification

- [ ] **Step 1: Full suite**

Run: `python -m pytest -q`
Expected: everything passes except the semgrep taint tests known to fail on this workstation (`WinError 4551`, memory `semgrep-cannot-run-on-this-workstation`). Compare the failing set with the same command at the baseline worktree; the sets must be identical. Paste both counts into the PR.

- [ ] **Step 2: Lint**

Run: `python -m ruff check cca_checks tests`
Expected: no findings.

- [ ] **Step 3: Red proof receipts**

Run: `python -m pytest tests/test_gate_holes.py -q` here (7 pass) and in `../cca-baseline` (7 fail as recorded). Confirm every new test has a `Red` line in its docstring: `grep -L "Red" tests/test_gate_*.py tests/test_stop_guard.py tests/test_pipeline_cli.py tests/test_pipeline_prose.py` prints nothing.

- [ ] **Step 4: Live smoke in a scratch repo**

```bash
cd "$(mktemp -d)" && git init -q && echo "x = 1" > app.py && git add -A && git -c user.email=t@t -c user.name=t commit -qm init
python -m cca_checks pipeline start --tier FAST
python -m cca_checks pipeline record L6 --detail APPROVED; echo "exit $?"
echo "x = 2" > app.py
python -m cca_checks pipeline status --require-complete; echo "exit $?"
python -m cca_checks pipeline abort --reason smoke
```

Expected: the L6 record exits 2 naming L1; status exits 2 with `L4` among `outstanding_to_close`; abort succeeds.

- [ ] **Step 5: Remove the baseline worktree**

```bash
git worktree remove ../cca-baseline --force
```

- [ ] **Step 6: Hand back for review**

Do not push or open a PR. Report the test counts, the red proof table, and the branch name to the user, and ask how they want it integrated.
