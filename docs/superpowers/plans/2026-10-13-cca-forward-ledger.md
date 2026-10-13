# CCA Forward Ledger Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every CCA run that closes COMPLETE is captured as a pending record at `pipeline close`, and is written to the bug ledger and the `cca` RE-call tenant automatically once its pull request merges, for Claude Code and Codex alike.

**Architecture:** Two stages. Stage 1 lives in the `cca_checks` package: `pipeline close` copies the run's own files into `~/.claude/cca-bugs/pending/<run_id>__<commit12>/` (on whatever host ran the close). Stage 2 is `python -m cca_checks bugs resolve`, which asks GitHub (through `gh`) whether the PR holding the run's commit merged into the default branch, turns a merged run into ledger rows keyed by the squash commit, and moves the record to `recorded/` or `abandoned/`. A new worker in `~/.claude/recall-vps2/` (repository `GiulioDER/dotclaude`) harvests pending records from VPS2, runs `resolve`, and when rows were written syncs the memos and rebuilds the `cca` tenant. The worker joins `refresh-all-vps2.ps1`, which Claude's `/session-close`, Codex's `SessionEnd` hook and the hourly `recall-memory-refresh` task already all call.

**Tech Stack:** Python 3.10+ stdlib (package), `gh` CLI (subprocess, injected in tests), PowerShell 5.1 worker, bash, pytest.

**Spec:** this conversation's design, restated in full here because there is no separate spec file:

1. Record a run's bugs **only once its PR has merged**; never at run close.
2. Nothing may be lost between run close and merge: the run's evidence is captured at `pipeline close`, outside any worktree, so deleting the worktree (Claude) or the VPS2 session copy (Codex) loses nothing.
3. Automatic: no person has to do anything when a PR merges. Checked at every session close (Claude and Codex) and hourly by the scheduled task, so a PR merged on github.com is picked up within the hour.
4. A PR closed without merging marks the record abandoned, never counted.
5. Anything pending longer than 14 days is reported.
6. Codex runs close on VPS2 (its wrapper runs `pipeline close` there), so their pending records are created on VPS2 and harvested from there.

## Global Constraints

- `requires-python = ">=3.10"`; `cca_checks/bugs` stays stdlib only. `gh` is called by subprocess and is never imported.
- The ledger stays append-only (`cca_checks/bugs/store.py`): no row is ever rewritten. A forward row's `bug_id` is `<project>:<merge_sha[:12]>:<finding_id>`, the same key a `backfill` of that squash commit would produce, so the two paths cannot double count.
- Capture is best-effort: a failure prints a warning and never refuses `pipeline close` (same contract as `_registry` in `cca_checks/pipeline_state.py`).
- Every new or changed behavior test carries a red proof in its docstring, in this repository's form: `Red 2026-10-NN: mutation <what>; failed at <assert>`. A test that cannot be shown red is not done (user rule, `~/.claude/CLAUDE.md`, "Prove a new test can go red").
- Focused local runs name files: `python -m pytest tests/test_bugs_pending.py -q`. The full suite runs on VPS2 only: `bash ~/.claude/bin/vps2-pytest -q` (a hook refuses it locally).
- Prose in CHANGELOG, docs and commit messages: no dash used as punctuation (`—`, `–`, or ` - `); hyphens inside words are fine.
- Commits end with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work in the worktree `C:\Users\gde00\Documents\cca-audit\.claude\worktrees\forward-ledger` (branch `feat/cca-forward-ledger`), never in the main checkout. dotclaude changes go in a worktree of `~/.claude` (Task 7).

## Review Focus

1. **One PR, two runs with the same finding id** (a DIFF run and its DEFERRED pass both have `BUG-001`, squashed into one merge commit): both rows must be written, the second with ordinal 2, and a rerun must write nothing. Test in Task 4.
2. **A PR whose head was force-pushed after the run closed**, so GitHub lists no PR for the captured commit: the record stays pending, is never marked abandoned, and is listed as stale after 14 days. Test in Task 5.
3. **`gh` missing, unauthenticated, or failing** (rate limit, network): every record stays pending untouched, the command exits 3, and nothing is written. Test in Task 5.
4. **L4 files from the two harnesses differ**: Claude writes `"finding"`, Codex writes `"finding_id"`. Both must produce rows. Test in Task 4.
5. **A crash after rows are appended but before the record is moved** to `recorded/`: the next resolve must append nothing new (deterministic ordinals plus `store.write`'s `bug_id` check) and finish the move. Test in Task 5.

---

## File Structure

`cca-audit` (this worktree):

| File | Responsibility |
|---|---|
| `cca_checks/plugin/tools/cca_tautology_check.py` (modify) | `verify --record` also writes `runs/<run_id>/L5.6.json`, the per-proof verdicts that are otherwise lost at close |
| `cca_checks/bugs/pending.py` (create) | Stage 1: `capture(root, summary_path)` copies one closed run into `bugs_dir()/pending/` |
| `cca_checks/pipeline_state.py` (modify) | `close` calls `pending.capture` after `_write_summary`, best-effort |
| `cca_checks/bugs/forward.py` (create) | pure: `rows_for(record_dir, merge)` builds `BugRow`s from a captured run plus a `Merge` |
| `cca_checks/bugs/github.py` (create) | `lookup(repo, commit, run=...)` returns `Merged`, `Waiting`, `Abandoned` or `Unknown` via `gh` |
| `cca_checks/bugs/resolve.py` (create) | Stage 2 loop: pending records → lookup → write rows → move dir; report |
| `cca_checks/bugs/__main__.py` (modify) | `bugs resolve [--write] [--json] [--stale-days N]` subcommand |
| `tests/conftest.py` (modify) | autouse fixture pointing `CCA_BUGS_DIR` at a temp dir, so no test touches the real ledger |
| `tests/test_tautology_l5_6_file.py`, `tests/test_bugs_pending.py`, `tests/test_bugs_forward.py`, `tests/test_bugs_github.py`, `tests/test_bugs_resolve.py` (create) | one file per unit |
| `CHANGELOG.md` (modify) | `## [Unreleased]` entries |

`dotclaude` (`~/.claude`, its own worktree):

| File | Responsibility |
|---|---|
| `recall-vps2/refresh-cca-vps2.ps1` (create) | harvest VPS2 pending → `bugs resolve --write --json` → sync + tenant rebuild when rows were written; lock and `last-cca-refresh.json` |
| `recall-vps2/refresh-all-vps2.ps1` (modify) | add the cca worker as the third worker |
| `recall-vps2/test_refresh_cca.py` (create) | runs a copy of the worker with stub `ssh`, `python` and `bash` |
| `commands/session-close.md` (modify) | step 6 names the cca worker and its state file |
| `CLAUDE.md` (modify) | the "Embedding" section's routine-form paragraph names three workers |

Codex (`~/.codex/re-call/SESSION_CLOSE.md`, not a repository): one paragraph, Task 8.

---

### Task 1: Keep the per-proof L5.6 verdicts

The L5.6 entry stores only counts (`_l5_6_entry`), and `run-state.json` is deleted by `close`, so today no file after a run says which finding was proved red. Without this task no forward row can ever reach `proved`.

**Files:**
- Modify: `cca_checks/plugin/tools/cca_tautology_check.py` (the `if a.record:` block at the end of `main`, currently lines 332-336)
- Test: `tests/test_tautology_l5_6_file.py`

**Interfaces:**
- Produces: `.claude/audits/runs/<run_id>/L5.6.json`, a JSON array of `{"finding": str, "nodeid": str, "verdict": "RED"|"TAUTOLOGICAL"|"INCONCLUSIVE"}`. `finding` is exactly the `FINDING` half of `--proof FINDING=NODEID` (a FIX id such as `FIX-001` or an auditor id such as `BUG-001`; Task 3 accepts both).

- [ ] **Step 1: Write the failing test**

```python
"""verify --record keeps each proof's verdict in runs/<run_id>/L5.6.json."""
import json

from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.plugin.tools import cca_tautology_check as tc


def _through_l5(repo):
    state = ps.start("STANDARD", root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(repo), root=repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo), root=repo)
    ps.record("L2.6", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(repo), root=repo)
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(repo), root=repo)
    ps.record("L5", root=repo)
    return state


def test_verify_record_writes_each_proofs_verdict(git_repo, monkeypatch):
    """Invariant: after `verify --record`, runs/<run_id>/L5.6.json names every proof's verdict.

    Red 2026-10-NN: mutation deleting the `_save_proofs(results)` call in `main`; failed at
    `assert path.exists()`.
    """
    state = _through_l5(git_repo)
    results = [tc.ProofResult("FIX-001", "tests/t.py::a", tc.RED, ""),
               tc.ProofResult("FIX-002", "tests/t.py::b", tc.TAUTOLOGICAL, "")]
    monkeypatch.setattr(tc, "verify", lambda proofs, snapdir, extra=None: results)
    monkeypatch.chdir(git_repo)
    tc.main(["verify", "--proof", "FIX-001=tests/t.py::a", "--proof", "FIX-002=tests/t.py::b",
             "--record"])
    path = git_repo / ".claude" / "audits" / "runs" / state.run_id / "L5.6.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"finding": "FIX-001", "nodeid": "tests/t.py::a", "verdict": "RED"},
        {"finding": "FIX-002", "nodeid": "tests/t.py::b", "verdict": "TAUTOLOGICAL"},
    ]


def test_the_file_does_not_make_l5_6_stale(git_repo, monkeypatch):
    """Invariant: the file lives under .claude/audits/, which the fingerprint ignores.

    Red 2026-10-NN: mutation writing the file to `<root>/L5.6.json` instead; failed at
    `assert not ps.outstanding(...)` (L5.6 recorded before the latest edit).
    """
    _through_l5(git_repo)
    monkeypatch.setattr(tc, "verify", lambda proofs, snapdir, extra=None:
                        [tc.ProofResult("FIX-001", "t::a", tc.RED, "")])
    monkeypatch.chdir(git_repo)
    assert tc.main(["verify", "--proof", "FIX-001=t::a", "--record"]) == 0
    ps.record("L5.5", root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    assert not ps.outstanding(ps.load(git_repo), git_repo)
```

If `ps.record("L5.5", ...)` refuses for a missing argument, copy the exact L5.5 call from `tests/test_pre_0110_followup.py` (search `"L5.5"`) rather than guessing; the second test only needs a complete run.

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/test_tautology_l5_6_file.py -q`
Expected: the first test FAILS at `assert path.exists()`.

- [ ] **Step 3: Implement**

Add above `def _record`:

```python
def _save_proofs(results: list[ProofResult]) -> None:
    """Keep each proof's verdict beside the run's other outputs: runs/<run_id>/L5.6.json.

    The L5.6 entry holds only counts and run-state.json is deleted at close, so without
    this file nothing after the run can tell which finding was proved red (the bug ledger
    needs exactly that). Under .claude/audits/, so the tree fingerprint ignores it.
    Best-effort: a failure warns and never blocks the record.
    """
    try:
        from cca_checks import deferred, pipeline_state
        state = pipeline_state.load(".")
        if state is None:
            return
        path = deferred.runs_dir(".") / state.run_id / "L5.6.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"finding": r.finding, "nodeid": r.nodeid, "verdict": r.verdict}
                for r in results]
        path.write_text(json.dumps(rows, indent=1) + "\n", encoding="utf-8")
    except Exception as exc:  # noqa: BLE001 (see the docstring)
        print(f"warning: could not save the L5.6 proof file ({exc})", file=sys.stderr)
```

In `main`, change the record block to:

```python
    if a.record:
        _save_proofs(results)
        status, detail = _l5_6_entry(results)
        recorded = _record(status, detail)
        if recorded:
            return recorded
```

- [ ] **Step 4: Run to verify it passes**, then perform the red proofs named in the docstrings (apply each mutation, see the named assert fail, restore), and replace `2026-10-NN` with the date.

Run: `python -m pytest tests/test_tautology_l5_6_file.py cca_checks/plugin/tools/test_cca_tautology_check.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cca_checks/plugin/tools/cca_tautology_check.py tests/test_tautology_l5_6_file.py
git commit -m "feat(tautology): verify --record keeps each proof's verdict in runs/<run_id>/L5.6.json"
```

---

### Task 2: Capture a closed run as a pending record

**Files:**
- Create: `cca_checks/bugs/pending.py`
- Modify: `cca_checks/pipeline_state.py` (`close`, after `_write_summary(state, root)`)
- Modify: `tests/conftest.py` (autouse `CCA_BUGS_DIR` isolation)
- Test: `tests/test_bugs_pending.py`

**Interfaces:**
- Consumes: `cca_checks.bugs.store.bugs_dir() -> Path`; `cca_checks.bugs.gitread.remote_repo(path) -> str | None` (returns `owner/name` for a GitHub origin, `None` otherwise); `cca_checks.deferred.runs_dir(root)`; `pipeline_state.LEDGER` (`".claude/audits/AUDITOR_SCORECARD.jsonl"`).
- Produces:
  - `pending.PENDING = "pending"`, `pending.RECORDED = "recorded"`, `pending.ABANDONED = "abandoned"` (folder names under `bugs_dir()`).
  - `pending.capture(root, run_id: str) -> Path | None`: the record folder, or `None` when the run fixed nothing (no L4 file, or an empty one).
  - Record folder `bugs_dir()/pending/<run_id>__<commit[:12]>/` holding: `summary.json`, `L2.json`, `L2.5.json`, `L4.json`, `L5.6.json` (each only if present in `runs/<run_id>/`), `scorecard.jsonl` (the run's rows from the project ledger, verbatim), `meta.json` = `{"schema": 1, "run_id", "commit", "repo" (owner/name or null), "branch" (or null), "captured_utc", "host"}`.

- [ ] **Step 1: Isolate the ledger directory in every test**

In `tests/conftest.py`, next to `_private_run_registry`, add:

```python
@pytest.fixture(autouse=True)
def _private_bugs_dir(monkeypatch, tmp_path_factory):
    """Every test gets its own bug ledger directory, so `pipeline close` never writes a
    pending record into the real `~/.claude/cca-bugs`. Outside `tmp_path`, which several
    fixtures make the repository under audit."""
    path = tmp_path_factory.mktemp("cca-bugs")
    monkeypatch.setenv("CCA_BUGS_DIR", str(path))
    return path
```

Check `_private_run_registry` is itself autouse; if it is not, make this one autouse anyway (it must apply to every existing close test).

- [ ] **Step 2: Write the failing tests**

```python
"""Stage 1: `pipeline close` captures a run that fixed something as a pending record."""
import json
import subprocess

from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.bugs import pending


def _finish_run_with_a_fix(repo):
    """A STANDARD run that fixed one finding, recorded through L6, its files on disk."""
    state = ps.start("STANDARD", root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(repo), root=repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo), root=repo)
    ps.record("L2.6", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(repo), root=repo)
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    l4 = write_output(repo, [{"fix_id": "FIX-001", "finding": "BUG-001", "files": ["app.py"]}])
    ps.record("L4", fixes=1, output=l4, root=repo)
    ps.record("L5", root=repo)
    ps.record("L5.5", root=repo)
    ps.record("L5.6", source="tool", root=repo)
    ps.record("L6", detail="APPROVED", root=repo)
    ledger = repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    with ledger.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps({"run_id": state.run_id, "fix_id": "FIX-001",
                             "architect_verdict": "APPROVED"}) + "\n")
        fh.write(json.dumps({"run_id": "someone-else", "fix_id": "FIX-009"}) + "\n")
    return state


def test_close_captures_the_run_with_its_own_rows_only(git_repo, _private_bugs_dir):
    """Invariant: close leaves pending/<run_id>__<commit12>/ with the run's files and only
    its own scorecard rows.

    Red 2026-10-NN: mutation deleting the `_capture_pending(state, root)` call in `close`;
    failed at `assert len(folders) == 1`.
    Red 2026-10-NN: mutation dropping the run_id filter in `_own_rows`; failed at
    `assert len(rows) == 1`.
    """
    state = _finish_run_with_a_fix(git_repo)
    assert ps.close(root=git_repo) is not None
    folders = list((_private_bugs_dir / "pending").iterdir())
    assert len(folders) == 1
    record = folders[0]
    head = subprocess.run(["git", "-C", str(git_repo), "rev-parse", "HEAD"],
                          capture_output=True, text=True, check=True).stdout.strip()
    assert record.name == f"{state.run_id}__{head[:12]}"
    meta = json.loads((record / "meta.json").read_text(encoding="utf-8"))
    assert meta["run_id"] == state.run_id and meta["commit"] == head
    assert meta["repo"] is None  # the fixture repository has no GitHub origin
    for name in ("summary.json", "L4.json"):
        assert (record / name).exists()
    rows = (record / "scorecard.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(rows) == 1


def test_a_run_that_fixed_nothing_leaves_no_record(git_repo, _private_bugs_dir):
    """Red 2026-10-NN: mutation removing the `if not fixes: return None` guard; failed at
    `assert not (_private_bugs_dir / "pending").exists()`.
    """
    state = ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(git_repo),
              root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    ps.close(root=git_repo)
    assert state.run_id
    assert not (_private_bugs_dir / "pending").exists()


def test_a_capture_failure_never_refuses_close(git_repo, monkeypatch, capsys):
    """Invariant: capture is best-effort; close still succeeds and warns.

    Red 2026-10-NN: mutation removing the try/except in `_capture_pending`; failed with
    OSError escaping `ps.close`.
    """
    _finish_run_with_a_fix(git_repo)

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(pending, "capture", boom)
    assert ps.close(root=git_repo) is not None
    assert "could not save this run for the bug ledger" in capsys.readouterr().err
```

As in Task 1: if any `ps.record` call above is refused for its shape, copy the exact call from `tests/test_pre_0110_followup.py::_complete_run` neighbors or `tests/test_pipeline_state.py::test_a_full_run_with_fixes_may_commit_and_close`; the fixture only needs a complete run whose L4 file lists one fix.

- [ ] **Step 3: Run to verify they fail**

Run: `python -m pytest tests/test_bugs_pending.py -q`
Expected: FAIL (`ImportError` on `pending` first; that is not the red proof, the red proofs are the mutations named in the docstrings, done in Step 6).

- [ ] **Step 4: Implement `cca_checks/bugs/pending.py`**

```python
"""Stage 1 of the forward ledger: keep a closed run until its pull request merges.

`pipeline close` calls `capture`. The record holds copies of the run's own files, so a
worktree or a VPS2 session copy can be deleted after the merge without losing the run.
Stage 2 (`resolve.py`) reads it. Nothing here touches the ledger.
"""
from __future__ import annotations

import json
import shutil
import socket
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .gitread import remote_repo
from .store import bugs_dir

PENDING = "pending"
RECORDED = "recorded"
ABANDONED = "abandoned"
RUN_FILES = ("summary.json", "L2.json", "L2.5.json", "L4.json", "L5.6.json")
SCORECARD = ".claude/audits/AUDITOR_SCORECARD.jsonl"


def _branch(root: Path) -> str | None:
    out = subprocess.run(["git", "-C", str(root), "branch", "--show-current"],
                         capture_output=True, text=True)
    name = out.stdout.strip()
    return name or None


def _own_rows(root: Path, run_id: str) -> str:
    path = root / SCORECARD
    if not path.exists():
        return ""
    keep = []
    for line in path.read_text(encoding="utf-8-sig", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and row.get("run_id") == run_id:
            keep.append(line)
    return "".join(f"{line}\n" for line in keep)


def capture(root, run_id: str) -> Path | None:
    root = Path(root)
    run_dir = root / ".claude" / "audits" / "runs" / run_id
    l4 = run_dir / "L4.json"
    fixes = json.loads(l4.read_text(encoding="utf-8-sig")) if l4.exists() else []
    if not fixes:
        return None
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    commit = summary["commit"]
    dest = bugs_dir() / PENDING / f"{run_id}__{commit[:12]}"
    dest.mkdir(parents=True, exist_ok=True)
    for name in RUN_FILES:
        if (run_dir / name).exists():
            shutil.copy2(run_dir / name, dest / name)
    (dest / "scorecard.jsonl").write_text(_own_rows(root, run_id), encoding="utf-8",
                                          newline="\n")
    meta = {
        "schema": 1, "run_id": run_id, "commit": commit,
        "repo": remote_repo(root), "branch": _branch(root),
        "captured_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "host": socket.gethostname(),
    }
    (dest / "meta.json").write_text(json.dumps(meta, indent=1) + "\n", encoding="utf-8")
    return dest
```

Check `remote_repo`'s signature in `cca_checks/bugs/gitread.py:49` before relying on it (it takes the clone path and returns `owner/name` or `None`). If it raises `GitError` on a repository with no origin, wrap the call: `try: repo = remote_repo(root) except GitError: repo = None`.

- [ ] **Step 5: Call it from `close`**

In `cca_checks/pipeline_state.py`, add next to `_registry`:

```python
def _capture_pending(state: PipelineState, root) -> None:
    """Save the closed run for the bug ledger; a failure is a warning, never a refusal.

    The ledger records a run only after its pull request merges (`bugs resolve`), and by
    then the worktree may be gone, so the run's files are copied out now.
    """
    try:
        from .bugs import pending
        pending.capture(root, state.run_id)
    except Exception as exc:  # noqa: BLE001 (see the docstring)
        print(f"warning: could not save this run for the bug ledger "
              f"({exc.__class__.__name__}: {exc})", file=sys.stderr)
```

In `close`, change

```python
    if not state.legacy:
        _write_summary(state, root)
```

to

```python
    if not state.legacy:
        _write_summary(state, root)
        _capture_pending(state, root)
```

- [ ] **Step 6: Run, then red-prove each docstring mutation**

Run: `python -m pytest tests/test_bugs_pending.py tests/test_run_registry.py tests/test_deferred.py -q`
Expected: PASS. Then apply each mutation named in the three docstrings, confirm the named assert fails, restore, and date the docstrings.

- [ ] **Step 7: Commit**

```bash
git add cca_checks/bugs/pending.py cca_checks/pipeline_state.py tests/conftest.py tests/test_bugs_pending.py
git commit -m "feat(bugs): pipeline close keeps a run that fixed something as a pending ledger record"
```

---

### Task 3: Build ledger rows from a captured run

Pure function, no git, no network.

**Files:**
- Create: `cca_checks/bugs/forward.py`
- Test: `tests/test_bugs_forward.py`

**Interfaces:**
- Consumes: `BugRow`, `EVIDENCED`, `CLAIMED`, `ABSENT` from `cca_checks/bugs/model.py`; `fix_number` from `cca_checks/bugs/ledgers.py`.
- Produces:

```python
@dataclass(frozen=True)
class Merge:
    repo: str          # "owner/name"
    sha: str           # the merge (squash) commit on the default branch, 40 hex
    date: str          # "YYYY-MM-DD", the PR's merged_at date
    pr: int

def rows_for(record: Path, merge: Merge) -> list[BugRow]
```

One row per L4 fix entry, in L4 order. Field sources:

| BugRow field | Source |
|---|---|
| `finding_id` | L4 `finding` or `finding_id` (Codex); fall back to `fix_id` when neither is present |
| `project`, `repo` | `merge.repo.split("/")[1]`, `merge.repo` |
| `fix_commit`, `date` | `merge.sha`, `merge.date` |
| `files` | L4 entry `files` |
| `summary` | L2 finding `claim`; else the L4 entry `summary`; else `"(no description recorded)"` |
| `severity`, `category`, `auditor` | L2 finding `priority`, `category`, `auditor` |
| `run_id` | `meta.json` `run_id` |
| `evidence.confirmed_l25` | `EVIDENCED` if L2.5 gives the finding `CONFIRMED`, else `ABSENT` |
| `evidence.fixed` | `EVIDENCED` (the merge is the evidence) |
| `evidence.l6_approved` | `EVIDENCED` if a scorecard row with this `fix_id` has `architect_verdict` starting `APPROVED`; `ABSENT` if `BLOCKED` or `REVISE`; else `CLAIMED` |
| `evidence.red_proof` | from `L5.6.json`, matched on the finding id or the fix id (compare FIX ids by `fix_number`): `EVIDENCED` if `RED`, `ABSENT` for any other verdict or no proof |
| `sources` | `[f"run:{merge.repo}:{run_id}", f"pr:{merge.repo}#{merge.pr}", f"git:{merge.sha}"]` |

- [ ] **Step 1: Write the failing tests**

```python
"""Rows from a captured run plus its merge."""
import json

from cca_checks.bugs.forward import Merge, rows_for
from cca_checks.bugs.model import ABSENT, CLAIMED, EVIDENCED

MERGE = Merge(repo="acme/widgets", sha="c" * 40, date="2026-10-14", pr=12)


def make_record(tmp_path, *, l4_key="finding", proofs=None, approved=True, verdict="CONFIRMED"):
    r = tmp_path / "rec"
    r.mkdir(parents=True)
    (r / "meta.json").write_text(json.dumps({"run_id": "r1", "commit": "a" * 40}))
    (r / "L2.json").write_text(json.dumps([
        {"id": "BUG-001", "priority": "P1", "category": "state", "auditor": "bug-auditor",
         "claim": "dry run creates the file"},
        {"id": "DOC-001", "priority": "P2", "category": "docs", "auditor": "doc-auditor",
         "claim": "help text is stale"}]))
    (r / "L2.5.json").write_text(json.dumps([{"id": "BUG-001", "verdict": verdict},
                                              {"id": "DOC-001", "verdict": "CONFIRMED"}]))
    (r / "L4.json").write_text(json.dumps([
        {"fix_id": "FIX-001", l4_key: "BUG-001", "files": ["a.py"]},
        {"fix_id": "FIX-002", l4_key: "DOC-001", "files": ["b.py"]}]))
    rows = [{"run_id": "r1", "fix_id": "FIX-001",
             "architect_verdict": "APPROVED" if approved else "BLOCKED"}]
    (r / "scorecard.jsonl").write_text("".join(json.dumps(x) + "\n" for x in rows))
    if proofs is not None:
        (r / "L5.6.json").write_text(json.dumps(proofs))
    return r


def test_a_merged_red_proved_fix_is_proved(tmp_path):
    """Invariant: confirmed + merged + approved + RED proof gives a proved row keyed by the
    merge commit.

    Red 2026-10-NN: mutation `fix_commit=meta["commit"]` instead of `merge.sha`; failed at
    `assert bug.fix_commit == MERGE.sha`.
    Red 2026-10-NN: mutation making red_proof always ABSENT; failed at
    `assert bug.tier == "proved"`.
    """
    rec = make_record(tmp_path, proofs=[{"finding": "FIX-001", "nodeid": "t::a",
                                         "verdict": "RED"}])
    bug, doc = rows_for(rec, MERGE)
    assert bug.fix_commit == MERGE.sha
    assert bug.bug_id == f"widgets:{'c' * 12}:BUG-001"
    assert bug.tier == "proved"
    assert bug.summary == "dry run creates the file" and bug.severity == "P1"
    assert doc.evidence["red_proof"] == ABSENT and doc.evidence["l6_approved"] == CLAIMED
    assert doc.tier == "historical"


def test_codex_l4_files_name_the_finding_as_finding_id(tmp_path):
    """Review Focus 4. Red 2026-10-NN: mutation reading only `entry.get("finding")`; failed
    at `assert [r.finding_id for r in rows] == ["BUG-001", "DOC-001"]` (got FIX ids).
    """
    rows = rows_for(make_record(tmp_path, l4_key="finding_id"), MERGE)
    assert [r.finding_id for r in rows] == ["BUG-001", "DOC-001"]


def test_a_proof_named_by_the_finding_id_counts(tmp_path):
    """Red 2026-10-NN: mutation matching proofs on the fix id only; failed at
    `assert rows[0].evidence["red_proof"] == EVIDENCED`.
    """
    rec = make_record(tmp_path, proofs=[{"finding": "BUG-001", "nodeid": "t", "verdict": "RED"}])
    rows = rows_for(rec, MERGE)
    assert rows[0].evidence["red_proof"] == EVIDENCED


def test_a_non_red_proof_or_a_blocked_architect_is_absent(tmp_path):
    """Red 2026-10-NN: mutation `EVIDENCED if proof else ABSENT` (any verdict counts);
    failed at `assert rows[0].evidence["red_proof"] == ABSENT`.
    """
    rec = make_record(tmp_path, approved=False,
                      proofs=[{"finding": "FIX-001", "nodeid": "t", "verdict": "TAUTOLOGICAL"}])
    rows = rows_for(rec, MERGE)
    assert rows[0].evidence["red_proof"] == ABSENT
    assert rows[0].evidence["l6_approved"] == ABSENT


def test_an_unconfirmed_finding_is_not_confirmed(tmp_path):
    """Red 2026-10-NN: mutation setting confirmed_l25 to EVIDENCED unconditionally; failed at
    the assert below.
    """
    rows = rows_for(make_record(tmp_path, verdict="UNCERTAIN"), MERGE)
    assert rows[0].evidence["confirmed_l25"] == ABSENT
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_bugs_forward.py -q`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `cca_checks/bugs/forward.py`**

```python
"""Ledger rows for one captured run whose pull request merged.

Built only from the run's own files and the merge GitHub reported, never from a commit
message, so a squash merge (whose message does not carry CCA's fix bullets) loses nothing.
The row is keyed by the merge commit, the same `bug_id` a backfill of that commit makes.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from .ledgers import fix_number
from .model import ABSENT, CLAIMED, EVIDENCED, BugRow


@dataclass(frozen=True)
class Merge:
    repo: str
    sha: str
    date: str
    pr: int


def _load(record: Path, name: str, default):
    path = record / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _scorecard(record: Path) -> list[dict]:
    path = record / "scorecard.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _same(a: str, b: str) -> bool:
    na, nb = fix_number(a), fix_number(b)
    return a == b or (na is not None and na == nb)


def _architect(rows: list[dict], fix_id: str) -> str:
    for row in rows:
        if _same(str(row.get("fix_id") or ""), fix_id) and "architect_verdict" in row:
            verdict = str(row["architect_verdict"]).upper()
            if verdict.startswith("APPROVED"):
                return EVIDENCED
            if verdict in ("BLOCKED", "REVISE"):
                return ABSENT
    return CLAIMED


def _proof(proofs: list[dict], finding: str, fix_id: str) -> str:
    verdicts = [str(p.get("verdict", "")).upper() for p in proofs
                if _same(str(p.get("finding", "")), fix_id) or p.get("finding") == finding]
    return EVIDENCED if verdicts and all(v == "RED" for v in verdicts) else ABSENT


def rows_for(record: Path, merge: Merge) -> list[BugRow]:
    meta = _load(record, "meta.json", {})
    l2 = {f.get("id"): f for f in _load(record, "L2.json", []) if isinstance(f, dict)}
    l25 = {v.get("id"): v.get("verdict") for v in _load(record, "L2.5.json", [])
           if isinstance(v, dict)}
    proofs = [p for p in _load(record, "L5.6.json", []) if isinstance(p, dict)]
    card = _scorecard(record)
    run_id = meta.get("run_id")
    out = []
    for entry in _load(record, "L4.json", []):
        if not isinstance(entry, dict):
            continue
        fix_id = str(entry.get("fix_id") or "")
        finding = entry.get("finding") or entry.get("finding_id") or fix_id
        found = l2.get(finding, {})
        out.append(BugRow(
            project=merge.repo.split("/", 1)[1], repo=merge.repo, finding_id=finding,
            fix_commit=merge.sha, date=merge.date, files=list(entry.get("files") or []),
            summary=found.get("claim") or entry.get("summary") or "(no description recorded)",
            severity=found.get("priority"), category=found.get("category"),
            auditor=found.get("auditor"), run_id=run_id,
            evidence={
                "confirmed_l25": EVIDENCED if l25.get(finding) == "CONFIRMED" else ABSENT,
                "fixed": EVIDENCED,
                "l6_approved": _architect(card, fix_id),
                "red_proof": _proof(proofs, finding, fix_id),
            },
            sources=[f"run:{merge.repo}:{run_id}", f"pr:{merge.repo}#{merge.pr}",
                     f"git:{merge.sha}"],
        ))
    return out
```

- [ ] **Step 4: Run, red-prove each docstring mutation, date them**

Run: `python -m pytest tests/test_bugs_forward.py tests/test_bugs_model.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add cca_checks/bugs/forward.py tests/test_bugs_forward.py
git commit -m "feat(bugs): ledger rows from a captured run and its merge commit"
```

---

### Task 4: Ask GitHub whether the run's PR merged

**Files:**
- Create: `cca_checks/bugs/github.py`
- Test: `tests/test_bugs_github.py`

**Interfaces:**
- Consumes: `Merge` from Task 3.
- Produces:

```python
Runner = Callable[[list[str]], str]   # gh argv (without "gh") -> stdout; raises GhError

class GhError(Exception): ...

@dataclass(frozen=True)
class Waiting:   reason: str          # no merged PR yet (open PR, or no PR lists the commit)
@dataclass(frozen=True)
class Abandoned: reason: str          # every PR listing the commit was closed unmerged
@dataclass(frozen=True)
class Unknown:   reason: str          # gh failed; try again later

def lookup(repo: str | None, commit: str, run: Runner = gh) -> Merge | Waiting | Abandoned | Unknown
def gh(args: list[str]) -> str        # the real runner: subprocess ["gh", *args], timeout 60 s
```

Rules:
1. `repo` is `None` (a Codex record captured on VPS2, whose origin is a local path): `gh api -X GET search/commits -f q=hash:<commit>` and take `items[0].repository.full_name`. No item: `Waiting("no repository on GitHub holds the commit yet")`.
2. Default branch: `gh api repos/<repo>` → `default_branch`.
3. `gh api repos/<repo>/commits/<commit>/pulls` → list. A PR with `merged_at` not null **and** `base.ref == default_branch` → `Merge(repo, pr["merge_commit_sha"], pr["merged_at"][:10], pr["number"])`. GitHub keeps a PR's commits after a squash merge, so this works although the commit itself is not on master.
4. Otherwise, a non-empty list where every PR has `state == "closed"` and `merged_at` null → `Abandoned`. A PR merged into another base (stacked) is `Waiting`, since it may reach the default branch later through another PR.
5. Empty list → `Waiting("no pull request lists the commit")`. (Review Focus 2: a force-pushed head lands here and stays waiting; Task 5 reports it as stale.)
6. Any `GhError`, or JSON that does not parse → `Unknown(str(exc))`.

`gh` errors: `subprocess.run` with `FileNotFoundError` (gh not installed) or a non-zero exit raises `GhError` carrying stderr's first line.

- [ ] **Step 1: Write the failing tests** with a fake runner (no network):

```python
"""lookup() against a fake gh."""
import json

import pytest

from cca_checks.bugs.forward import Merge
from cca_checks.bugs.github import Abandoned, GhError, Unknown, Waiting, lookup

SHA = "a" * 40


def fake(pulls, *, default="master", search=None):
    def run(args):
        path = args[-1] if args[0] == "api" else ""
        if "search/commits" in args:
            return json.dumps({"items": search or []})
        if path == "repos/acme/widgets":
            return json.dumps({"default_branch": default})
        if path == f"repos/acme/widgets/commits/{SHA}/pulls":
            return json.dumps(pulls)
        raise AssertionError(args)
    return run


def pr(number, *, merged=None, state="closed", base="master", merge_sha="m" * 40):
    return {"number": number, "state": state, "merged_at": merged,
            "merge_commit_sha": merge_sha, "base": {"ref": base}}


def test_a_squash_merged_pr_gives_its_merge_commit():
    """Red 2026-10-NN: mutation returning `SHA` instead of `merge_commit_sha`; failed at the
    equality below."""
    got = lookup("acme/widgets", SHA, fake([pr(7, merged="2026-10-14T09:00:00Z")]))
    assert got == Merge("acme/widgets", "m" * 40, "2026-10-14", 7)


def test_a_pr_merged_into_another_base_is_still_waiting():
    """Red 2026-10-NN: mutation dropping the base == default check; failed at isinstance."""
    got = lookup("acme/widgets", SHA, fake([pr(7, merged="2026-10-14T09:00:00Z", base="stack")]))
    assert isinstance(got, Waiting)


def test_closed_without_merging_is_abandoned_but_open_is_waiting():
    """Red 2026-10-NN: mutation treating any closed-unmerged PR in the list as abandoned
    (`any` for `all`); failed at the second isinstance."""
    assert isinstance(lookup("acme/widgets", SHA, fake([pr(7)])), Abandoned)
    assert isinstance(lookup("acme/widgets", SHA, fake([pr(7), pr(8, state="open")])), Waiting)


def test_no_pr_lists_the_commit_is_waiting_not_abandoned():
    """Review Focus 2. Red 2026-10-NN: mutation returning Abandoned for an empty list."""
    assert isinstance(lookup("acme/widgets", SHA, fake([])), Waiting)


def test_an_unknown_repo_is_found_by_searching_the_commit():
    """Red 2026-10-NN: mutation returning Waiting whenever repo is None; failed at ==."""
    run = fake([pr(7, merged="2026-10-14T09:00:00Z")],
               search=[{"repository": {"full_name": "acme/widgets"}}])
    assert lookup(None, SHA, run) == Merge("acme/widgets", "m" * 40, "2026-10-14", 7)


@pytest.mark.parametrize("err", [GhError("gh: not logged in"), ValueError("bad json")])
def test_any_gh_failure_is_unknown(err):
    """Review Focus 3. Red 2026-10-NN: mutation narrowing the except to GhError only; the
    ValueError case raises instead of returning Unknown."""
    def run(args):
        raise err
    assert isinstance(lookup("acme/widgets", SHA, run), Unknown)
```

- [ ] **Step 2: Run to verify they fail**: `python -m pytest tests/test_bugs_github.py -q` (module missing).

- [ ] **Step 3: Implement `cca_checks/bugs/github.py`**

```python
"""Did the pull request that holds a run's commit merge into the default branch?

Asked of GitHub through the `gh` CLI, never a library. GitHub keeps a pull request's
commits after a squash merge, so a commit that never reached the default branch still
names the PR that did, and that PR names the squash commit the ledger keys rows by.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Callable

from .forward import Merge


class GhError(Exception):
    pass


@dataclass(frozen=True)
class Waiting:
    reason: str


@dataclass(frozen=True)
class Abandoned:
    reason: str


@dataclass(frozen=True)
class Unknown:
    reason: str


Runner = Callable[[list[str]], str]


def gh(args: list[str]) -> str:
    try:
        out = subprocess.run(["gh", *args], capture_output=True, text=True,
                             encoding="utf-8", timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired) as exc:
        raise GhError(f"gh could not run: {exc}") from exc
    if out.returncode != 0:
        first = (out.stderr.strip().splitlines() or ["no message"])[0]
        raise GhError(f"gh {' '.join(args[:2])} exited {out.returncode}: {first}")
    return out.stdout


def lookup(repo: str | None, commit: str, run: Runner = gh):
    try:
        if repo is None:
            items = json.loads(run(["api", "-X", "GET", "search/commits",
                                    "-f", f"q=hash:{commit}"])).get("items") or []
            if not items:
                return Waiting("no repository on GitHub holds the commit yet")
            repo = items[0]["repository"]["full_name"]
        default = json.loads(run(["api", f"repos/{repo}"]))["default_branch"]
        pulls = json.loads(run(["api", f"repos/{repo}/commits/{commit}/pulls"]))
    except (GhError, ValueError, KeyError, TypeError) as exc:
        return Unknown(str(exc))
    for p in pulls:
        if p.get("merged_at") and (p.get("base") or {}).get("ref") == default:
            return Merge(repo, p["merge_commit_sha"], p["merged_at"][:10], p["number"])
    if not pulls:
        return Waiting("no pull request lists the commit")
    if all(p.get("state") == "closed" and not p.get("merged_at") for p in pulls):
        return Abandoned("closed without merging: " + ", ".join(f"#{p['number']}" for p in pulls))
    return Waiting("pull request not merged into " + default)
```

- [ ] **Step 4: Run, red-prove, date docstrings**: `python -m pytest tests/test_bugs_github.py -q` → PASS.

- [ ] **Step 5: Commit**

```bash
git add cca_checks/bugs/github.py tests/test_bugs_github.py
git commit -m "feat(bugs): find the merge commit of the pull request that holds a run's commit"
```

---

### Task 5: `bugs resolve`: pending records become ledger rows once merged

**Files:**
- Create: `cca_checks/bugs/resolve.py`
- Modify: `cca_checks/bugs/__main__.py` (new `resolve` subparser; dispatch on `a.cmd`)
- Test: `tests/test_bugs_resolve.py`

**Interfaces:**
- Consumes: `pending.PENDING/RECORDED/ABANDONED`, `forward.rows_for`, `forward.Merge`, `github.lookup`, `github.Runner`, `store.write`, `store.bugs_dir`, `model.BugRow`.
- Produces:

```python
@dataclass
class ResolveReport:
    recorded: list[str]        # record folder names moved to recorded/
    abandoned: list[str]
    waiting: list[str]         # "<name>: <reason>"
    unknown: list[str]
    stale: list[str]           # waiting longer than stale_days, by meta captured_utc
    rows_written: int
    memos_written: int

def resolve(root: Path, *, write: bool, run: Runner = gh, stale_days: int = 14,
            now: datetime | None = None) -> ResolveReport
```

CLI: `python -m cca_checks bugs resolve [--write] [--json] [--stale-days N]`. Exit 0 normally; exit 3 when any record is `unknown` (the worker treats 3 as retryable). `--json` prints `asdict(report)`. Without `--write`: look up and report, write and move nothing.

Ordering and ordinals (Review Focus 1, 5): process pending folders sorted by `meta.captured_utc`, then name. Group the merged ones by merge sha; within a group assign `ordinal` per `finding_id` in that order (first 1, next 2). `store.write` then skips any `bug_id` already in the ledger, so a rerun after a crash appends nothing, and the folder move completes. Move only after `store.write` returned. A merged record whose rows were all already present still moves.

- [ ] **Step 1: Write the failing tests**

```python
"""Stage 2 against a fake gh and a temp ledger directory (CCA_BUGS_DIR is private)."""
import json
from datetime import datetime, timezone

from test_bugs_forward import make_record  # reuse the record builder

from cca_checks.bugs.resolve import resolve

SHA = "a" * 40


def pending_record(root, tmp_path, name, *, captured="2026-10-13T10:00:00Z", commit=SHA):
    rec = make_record(tmp_path / name, proofs=[{"finding": "FIX-001", "nodeid": "t",
                                                "verdict": "RED"}])
    meta = {"schema": 1, "run_id": name, "commit": commit, "repo": "acme/widgets",
            "branch": "feat/x", "captured_utc": captured, "host": "h"}
    (rec / "meta.json").write_text(json.dumps(meta))
    dest = root / "pending" / f"{name}__{commit[:12]}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    rec.rename(dest)
    return dest


def gh_merged(args):
    if args[-1] == "repos/acme/widgets":
        return json.dumps({"default_branch": "master"})
    return json.dumps([{"number": 7, "state": "closed", "merged_at": "2026-10-14T09:00:00Z",
                        "merge_commit_sha": "m" * 40, "base": {"ref": "master"}}])


def gh_open(args):
    if args[-1] == "repos/acme/widgets":
        return json.dumps({"default_branch": "master"})
    return json.dumps([{"number": 7, "state": "open", "merged_at": None,
                        "merge_commit_sha": None, "base": {"ref": "master"}}])


def test_a_merged_run_is_written_once_and_moved(_private_bugs_dir, tmp_path):
    """Red 2026-10-NN: mutation deleting `_move(folder, root, RECORDED)`; failed at
    `assert not any((root / "pending").iterdir())`."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1")
    rep = resolve(root, write=True, run=gh_merged)
    assert rep.rows_written == 2 and len(rep.recorded) == 1
    assert not any((root / "pending").iterdir())
    assert (root / "recorded" / f"r1__{SHA[:12]}" / "meta.json").exists()
    assert resolve(root, write=True, run=gh_merged).rows_written == 0


def test_two_runs_in_one_merge_keep_both_rows(_private_bugs_dir, tmp_path):
    """Review Focus 1. Red 2026-10-NN: mutation leaving every ordinal at 1; failed at
    `assert rep.rows_written == 4` (2: the second run's rows collided and were skipped)."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1", captured="2026-10-13T10:00:00Z")
    pending_record(root, tmp_path, "r2", captured="2026-10-13T11:00:00Z", commit="b" * 40)
    rep = resolve(root, write=True, run=gh_merged)
    assert rep.rows_written == 4
    ids = [json.loads(x)["bug_id"] for x in (root / "bugs.jsonl").read_text().splitlines()]
    assert f"widgets:{'m' * 12}:BUG-001~2" in ids


def test_a_crash_before_the_move_writes_nothing_twice(_private_bugs_dir, tmp_path, monkeypatch):
    """Review Focus 5. Red 2026-10-NN: mutation assigning ordinals against the ledger's
    existing ids instead of within the batch; failed at `rows_written == 0`."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1")
    import cca_checks.bugs.resolve as rs
    real_move = rs._move
    monkeypatch.setattr(rs, "_move", lambda *a: (_ for _ in ()).throw(OSError("crash")))
    try:
        resolve(root, write=True, run=gh_merged)
    except OSError:
        pass
    monkeypatch.setattr(rs, "_move", real_move)
    rep = resolve(root, write=True, run=gh_merged)
    assert rep.rows_written == 0 and len(rep.recorded) == 1


def test_waiting_records_stay_and_old_ones_are_stale(_private_bugs_dir, tmp_path):
    """Review Focus 2. Red 2026-10-NN: mutation comparing against stale_days * 0; failed at
    `assert rep.stale == []` for the fresh record."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "old", captured="2026-09-01T00:00:00Z")
    pending_record(root, tmp_path, "new", captured="2026-10-13T00:00:00Z", commit="b" * 40)
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    rep = resolve(root, write=True, run=gh_open, now=now)
    assert len(rep.waiting) == 2 and rep.rows_written == 0
    assert [s.split(":")[0] for s in rep.stale] == [f"old__{SHA[:12]}"]
    assert len(list((root / "pending").iterdir())) == 2


def test_gh_failure_touches_nothing_and_exits_3(_private_bugs_dir, tmp_path, capsys,
                                                monkeypatch):
    """Review Focus 3. Red 2026-10-NN: mutation returning 0 when unknown is non-empty; failed
    at `assert code == 3`."""
    from cca_checks.bugs.__main__ import main
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1")
    import cca_checks.bugs.github as g
    import cca_checks.bugs.resolve as rs

    def broken(args):
        raise g.GhError("gh: not logged in")

    monkeypatch.setattr(rs, "DEFAULT_RUNNER", broken)
    code = main(["resolve", "--write", "--json"])
    assert code == 3
    assert json.loads(capsys.readouterr().out)["rows_written"] == 0
    assert len(list((root / "pending").iterdir())) == 1
```

`tests/test_bugs_forward.py` must expose `make_record(tmp_path, ...)` at module level (Task 3 already does); importing it from a sibling test module works because `tests/` is on `sys.path` the same way `bugs_testkit` and `conftest` already are. If the import fails, move `make_record` into `tests/bugs_testkit.py` and import it from there in both files.

- [ ] **Step 2: Run to verify they fail**: `python -m pytest tests/test_bugs_resolve.py -q`.

- [ ] **Step 3: Implement `cca_checks/bugs/resolve.py`**

```python
"""Stage 2 of the forward ledger: write a pending run's bugs once its pull request merged.

Runs at every session close and hourly (the dotclaude worker `refresh-cca-vps2.ps1`).
A record waits in pending/ until GitHub says its PR merged into the default branch, moves
to recorded/ after its rows are appended, or to abandoned/ when every PR was closed
unmerged. Append-only end to end: a rerun after any crash appends nothing new.
"""
from __future__ import annotations

import json
import shutil
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import github
from .forward import Merge, rows_for
from .pending import ABANDONED, PENDING, RECORDED
from .store import write as write_rows

DEFAULT_RUNNER = github.gh


@dataclass
class ResolveReport:
    recorded: list[str] = field(default_factory=list)
    abandoned: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)
    unknown: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)
    rows_written: int = 0
    memos_written: int = 0


def _meta(folder: Path) -> dict:
    try:
        return json.loads((folder / "meta.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _move(folder: Path, root: Path, where: str) -> None:
    dest = root / where / folder.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if dest.exists():
        shutil.rmtree(dest)
    shutil.move(str(folder), str(dest))


def _captured(meta: dict) -> datetime | None:
    try:
        return datetime.strptime(meta["captured_utc"], "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc)
    except (KeyError, TypeError, ValueError):
        return None


def resolve(root: Path, *, write: bool, run=None, stale_days: int = 14,
            now: datetime | None = None) -> ResolveReport:
    run = run or DEFAULT_RUNNER
    now = now or datetime.now(timezone.utc)
    report = ResolveReport()
    base = root / PENDING
    folders = sorted((p for p in base.iterdir() if p.is_dir()) if base.exists() else [],
                     key=lambda p: (_meta(p).get("captured_utc") or "", p.name))
    merged: list[tuple[Path, Merge]] = []
    for folder in folders:
        meta = _meta(folder)
        got = github.lookup(meta.get("repo"), meta.get("commit", ""), run)
        if isinstance(got, Merge):
            merged.append((folder, got))
        elif isinstance(got, github.Abandoned):
            report.abandoned.append(folder.name)
            if write:
                _move(folder, root, ABANDONED)
        elif isinstance(got, github.Unknown):
            report.unknown.append(f"{folder.name}: {got.reason}")
        else:
            report.waiting.append(f"{folder.name}: {got.reason}")
            when = _captured(meta)
            if when is not None and now - when > timedelta(days=stale_days):
                report.stale.append(f"{folder.name}: waiting since {meta['captured_utc']}")
    seen: dict[tuple[str, str], int] = defaultdict(int)
    batch = []
    for folder, merge in merged:
        rows = rows_for(folder, merge)
        for row in rows:
            seen[(merge.sha, row.finding_id)] += 1
            row.ordinal = seen[(merge.sha, row.finding_id)]
        batch.append((folder, rows))
    if write and batch:
        stamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        added, memos = write_rows([r for _, rows in batch for r in rows], root, stamp)
        report.rows_written, report.memos_written = added, memos
        for folder, _ in batch:
            _move(folder, root, RECORDED)
    report.recorded = [f.name for f, _ in batch]
    return report
```

The store function is imported as `write_rows` because the keyword `write: bool` would shadow it inside `resolve`.

- [ ] **Step 4: Add the CLI**

In `cca_checks/bugs/__main__.py` `main`, after the `backfill` subparser:

```python
    r = sub.add_parser("resolve", help="write pending runs whose pull request merged")
    r.add_argument("--write", action="store_true", help="append rows and move records")
    r.add_argument("--json", action="store_true")
    r.add_argument("--stale-days", type=int, default=14)
```

and before the backfill code path (`a = p.parse_args(argv)` stays first):

```python
    if a.cmd == "resolve":
        from dataclasses import asdict

        from . import resolve as rs
        report = rs.resolve(bugs_dir(), write=a.write, stale_days=a.stale_days)
        data = asdict(report)
        if a.json:
            print(json.dumps(data, indent=2))
        else:
            print(_printable("\n".join(f"{k}: {v}" for k, v in data.items())))
        return 3 if report.unknown else 0
```

Update the module docstring's usage line to name both subcommands.

- [ ] **Step 5: Run, red-prove each docstring mutation, date them**

Run: `python -m pytest tests/test_bugs_resolve.py tests/test_bugs_store.py tests/test_bugs_forward.py tests/test_bugs_github.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add cca_checks/bugs/resolve.py cca_checks/bugs/__main__.py tests/test_bugs_resolve.py
git commit -m "feat(bugs): bugs resolve writes a pending run once its pull request merged"
```

---

### Task 6: Full suite, CHANGELOG, PR

- [ ] **Step 1: CHANGELOG.** Add at the top, above `## [0.11.0]`:

```markdown
## [Unreleased]

### Added
- The bug ledger now grows by itself. `pipeline close` saves a run that fixed something to `~/.claude/cca-bugs/pending/<run_id>__<commit>/` (its L2, L2.5, L4 and L5.6 files, its scorecard rows and its summary), outside the worktree, so deleting the worktree loses nothing. `python -m cca_checks bugs resolve --write` asks GitHub, through `gh`, whether the pull request holding the run's commit merged into the default branch: a merged run becomes ledger rows keyed by the merge commit, a pull request closed unmerged moves the record to `abandoned/`, and anything else waits; a record waiting longer than 14 days is listed as stale. Squash merges work because GitHub keeps a pull request's commits. Exit 3 when `gh` could not answer.
- `cca_tautology_check.py verify --record` writes `runs/<run_id>/L5.6.json`, each proof's verdict by finding. The L5.6 entry holds only counts, so without this file no run could ever be recorded as proved.
```

- [ ] **Step 2: Full suite on VPS2** (background):

```bash
bash ~/.claude/bin/vps2-pytest -q
```

Expected: all pass. An exit 0 after an SSH reset is not a pass; read the pytest summary line (memory: `heavy-tests-run-on-vps2-through-a-gate`).

- [ ] **Step 3: Commit, push, open the PR**

```bash
git add CHANGELOG.md docs/superpowers/plans/2026-10-13-cca-forward-ledger.md
git commit -m "docs(changelog): the forward bug ledger"
git push -u origin feat/cca-forward-ledger
gh pr create --repo GiulioDER/cca-audit --title "feat(bugs): record each CCA run in the bug ledger once its PR merges" --body-file <prepared body>
```

The PR body lists the tasks, the red proofs, and ends with the attribution line `🤖 Generated with [Claude Code](https://claude.com/claude-code)`. Release (version bump, tag, `release.yml`) is the user's call after merge, as for 0.11.0.

---

### Task 7: The dotclaude worker, wired into every refresh

Do this in a worktree of `~/.claude` (`git -C ~/.claude worktree add -b feat/cca-refresh-worker <path> origin/main`; check the default branch name first with `git -C ~/.claude remote show origin`). The worker invokes the **installed** package, so it does nothing useful until Task 9's install, and its tests stub everything.

**Files:**
- Create: `recall-vps2/refresh-cca-vps2.ps1`
- Modify: `recall-vps2/refresh-all-vps2.ps1` (the worker list)
- Create: `recall-vps2/test_refresh_cca.py`
- Modify: `commands/session-close.md` (step 6), `CLAUDE.md` (the "Since 2026-08-27 the routine form is ONE command" paragraph: three workers, and `last-cca-refresh.json`)

**Interfaces:**
- Consumes: `python -m cca_checks bugs resolve --write --json` (Task 5; exit 0 or 3; JSON with `rows_written`, `stale`, `waiting`, `unknown`); `recall-vps2/sync_cca_bugs.sh`; VPS2 `~/recall-repos/bin/refresh-cca-generation.sh` (queues on `embed_queue.sh` itself).
- Produces: `recall-vps2/last-cca-refresh.json` = `{"last_tick_utc", "harvested", "rows_written", "waiting", "stale", "unknown", "tenant": "skipped"|"refreshed"|"failed", "exit"}`; exit 0 success, 1 failure, 3 retryable (gh unknown), 5 VPS2 unreachable.

Behavior, in order:
1. Take `recall-vps2/cca-refresh-worker.lock` the way `refresh-project-vps2.ps1` takes its lock (copy that block; it was hardened by `test_refresh_locks.py`). Held: exit 0 with outcome `lock-held` in the state file.
2. **Harvest** VPS2: `ssh vps2 'cd ~/.claude/cca-bugs 2>/dev/null && [ -d pending ] && tar -cf - pending'` piped into `tar -C <local bugs dir> -xf -` (Git Bash `tar`; local bugs dir from `$env:CCA_BUGS_DIR` or `~/.claude/cca-bugs`). Only after the extract succeeded, and for exactly the folder names the archive listed (`tar -tf`), run `ssh vps2 'cd ~/.claude/cca-bugs && mkdir -p harvested && mv pending/<name> harvested/'` per name. A folder that already exists locally is overwritten by the extract (identical content: same run, same commit). Unreachable: record exit 5 and continue to step 3 with local records only.
3. **Resolve**: `python -m cca_checks bugs resolve --write --json`, run from `$env:USERPROFILE` (never inside a recall checkout, whose local-check guard refuses `python -m cca_checks`). Python from `$env:CCA_BUGS_PYTHON` or `python`.
4. If `rows_written > 0`: `bash sync_cca_bugs.sh`, then `ssh vps2 'bash ~/recall-repos/bin/refresh-cca-generation.sh'`. Tenant `refreshed` on both exit 0, else `failed` and exit 1.
5. Write the state file (every tick, including skips, like the memory worker) and exit.

- [ ] **Step 1: Write the failing tests** in `recall-vps2/test_refresh_cca.py`, modelled on `test_refresh_locks.py` (copy of the worker in `tmp_path`, stub executables first on `PATH`). Stubs: `ssh` (bash script appending its argv to `calls.log`; for the harvest command it emits a tar of a fixture `pending/` folder), `python` (prints a canned JSON report and exits with `$STUB_EXIT`), and `bash sync_cca_bugs.sh` replaced by a stub `sync_cca_bugs.sh` in the copy's folder that logs its call.

Tests (each docstring carries its red proof):
- `test_no_new_rows_skips_sync_and_tenant`: python stub reports `rows_written: 0`; assert `calls.log` has no `sync_cca_bugs` and no `refresh-cca-generation`, state `tenant == "skipped"`, exit 0. Red: mutation `-ge 0` for `-gt 0` in the rows check.
- `test_new_rows_sync_then_refresh_the_tenant`: `rows_written: 2`; assert sync logged before `refresh-cca-generation`, `tenant == "refreshed"`. Red: mutation deleting the refresh call.
- `test_harvest_moves_only_what_was_extracted`: assert one `mv pending/<name> harvested/` per fixture folder and the folder exists locally. Red: mutation issuing the remote `mv` before the extract (assert the extract-failure variant leaves no `mv` call: make `tar` fail by emitting garbage).
- `test_gh_unknown_exits_3_and_writes_state`: python stub exits 3; worker exit 3, state `exit == 3`, `last_tick_utc` present. Red: mutation mapping 3 to 0.
- `test_vps2_unreachable_still_resolves_local_records`: ssh stub exits 255 on harvest; python stub is still called; worker exit 5. Red: mutation returning before step 3.

Run: `python -m pytest recall-vps2/test_refresh_cca.py -q` (local is fine: this is not a cca-audit or recall checkout). Expected first: FAIL (worker missing).

- [ ] **Step 2: Write `refresh-cca-vps2.ps1`** implementing steps 1 to 5 above, same structure, parameters (`-Queued`, `-DryRun`), helpers (`Format-Utc`, logging to `logs/`) and lock code as `refresh-project-vps2.ps1`. `-DryRun`: harvest listing only (`tar -tf`), `bugs resolve --json` without `--write`, no sync, no refresh.

- [ ] **Step 3: Add the worker to the coordinator.** In `refresh-all-vps2.ps1`:

```powershell
$ccaWorker = Join-Path $root "refresh-cca-vps2.ps1"
...
foreach ($worker in @($memoryWorker, $projectWorker, $ccaWorker)) {
```

Exit 3 from the cca worker must not turn the coordinator's run into a failure that blocks the others; the loop already runs every worker before deciding, so leave the final `exit 1` rule as is (a gh outage is reported, and the next tick retries).

- [ ] **Step 4: Run the tests and red-prove each one.** `python -m pytest recall-vps2/test_refresh_cca.py recall-vps2/test_refresh_locks.py -q` → PASS.

- [ ] **Step 5: Docs.** In `commands/session-close.md` step 6, after the paragraph that introduces `refresh-session-close.sh`, add: "It also runs the CCA ledger worker: CCA runs saved at `pipeline close` (here, and Codex's on VPS2) are written to the bug ledger once their pull request merged, and the `cca` tenant is rebuilt when that wrote anything. State: `~/.claude/recall-vps2/last-cca-refresh.json`; `stale` lists runs waiting more than 14 days." In `CLAUDE.md`, the routine-form paragraph: "runs the existing memory refresh, the local-checkout project refresh, and the CCA ledger refresh", and name `last-cca-refresh.json` beside the other two state files. Date the edit per that file's rules.

- [ ] **Step 6: Commit and push the dotclaude branch, open its PR** (`GiulioDER/dotclaude`), body ending with the Claude Code attribution line.

---

### Task 8: Codex

Codex needs no hook change: its `SessionEnd` and `PreCompact` hooks queue `~/.codex/re-call/refresh-vps2.ps1`, which runs `refresh-session-close.sh`, which runs `refresh-all-vps2.ps1` (verified 2026-10-13: `refresh-vps2.ps1:42`). Two things remain.

- [ ] **Step 1: Make VPS2's `cca_checks` capture.** Codex's wrapper runs `pipeline close` with `PYTHONPATH=$HOME/test-runs/cca-audit/master` first. After the PR merges, refresh that tree from `origin/master` and verify on VPS2:

```bash
ssh vps2 'cd ~/test-runs/cca-audit/master && PYTHONPATH=. ~/test-runs/cca-audit/.venv/bin/python -c "import cca_checks.bugs.pending as p; print(p.PENDING)"'
```

Expected: `pending`. Find out first how that tree is updated (it is the `vps2-pytest` ship target for branch `master`; running `bash ~/.claude/bin/vps2-pytest tests/test_bugs_pending.py -q` from a cca-audit checkout on master refreshes it). Note in the dotclaude PR how it is kept current, since a stale tree silently captures nothing.

- [ ] **Step 2: One paragraph in `~/.codex/re-call/SESSION_CLOSE.md`** under the hook list: "5. The CCA ledger worker, through the same coordinator: a CCA run closed on VPS2 is saved there at `pipeline close`, harvested by the next refresh, and written to the bug ledger once its pull request merged. Nothing to do by hand."

---

### Task 9: Install and the first live check

- [ ] **Step 1:** After the cca-audit PR merges (and, if the user releases it, from PyPI), re-install the user-level package from the merge commit, per memory `cca-installed-from-master-pre-0-11`:

```bash
pip install --user --force-reinstall --no-deps git+https://github.com/GiulioDER/cca-audit@<merge sha>
```

Verify from outside any checkout: `python -c "import cca_checks.bugs.resolve"`.

- [ ] **Step 2:** Merge the dotclaude PR and pull `~/.claude` to it.

- [ ] **Step 3: Backfill the 19 runs already closed since 0.11.0**, which closed before capture existed. For each Claude run folder (`~/Documents/recall/.claude/worktrees/*/.claude/audits/runs/<id>/` with a `summary.json`) and each Codex one on VPS2 (`~/recall-repos/sess-codex-cca-*/src/.claude/audits/runs/<id>/`), call `pending.capture(<root>, <run_id>)` once (on VPS2 with the refreshed tree). Then run the worker once by hand:

```bash
powershell -NoProfile -ExecutionPolicy Bypass -File ~/.claude/recall-vps2/refresh-cca-vps2.ps1
```

Expected: `last-cca-refresh.json` shows the merged runs recorded (each Codex PR that merged, for example #909's run if it was one), the open ones waiting, `rows_written > 0`, `tenant: refreshed`; `ssh vps2 'bash ~/recall-repos/bin/refresh-cca-generation.sh --check'` shows a new active generation with more than 643 memos.

- [ ] **Step 4:** Update memory: `cca-bug-ledger-written.md` (the forward path exists, where pending lives, the worker and its state file) and its `MEMORY.md` line.
