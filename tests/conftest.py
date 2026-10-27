"""Shared fixtures for the pipeline gate tests.

`tree_of` deliberately re-implements the fingerprint with plain git commands
instead of importing it: the hole tests in test_gate_holes.py must run unchanged
against baseline d2317de, where the core module does not exist.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import uuid

import pytest

_HOOKS = pathlib.Path(__file__).resolve().parents[1] / "cca_checks" / "plugin" / "hooks"


@pytest.fixture(autouse=True)
def _private_run_registry(monkeypatch, tmp_path_factory):
    """Every test gets its own open run registry, so none touches the real `~/.claude`.

    A separate directory from `tmp_path`: several fixtures make `tmp_path` the repository
    under audit, and a registry file inside it would change the tree fingerprint.
    """
    registry = tmp_path_factory.mktemp("cca-registry") / "open-runs.json"
    monkeypatch.setenv("CCA_RUN_REGISTRY", str(registry))
    return registry


@pytest.fixture(autouse=True)
def _private_bugs_dir(monkeypatch, tmp_path_factory):
    """Every test gets its own bug ledger directory, so `pipeline close` never writes a
    pending record into the real `~/.claude/cca-bugs`. Outside `tmp_path`, which several
    fixtures make the repository under audit."""
    path = tmp_path_factory.mktemp("cca-bugs")
    monkeypatch.setenv("CCA_BUGS_DIR", str(path))
    return path


@pytest.fixture(autouse=True)
def _private_user_claude_dir(monkeypatch, tmp_path_factory):
    """`pipeline start` compares the user level `.claude/` with the package; never the real one.

    Without this, every CLI start in the suite would read this machine's `~/.claude`, and
    a stale file there would print warnings into output the tests parse.
    """
    from cca_checks.plugin import installer

    user_dir = tmp_path_factory.mktemp("home") / ".claude"
    monkeypatch.setattr(installer, "user_claude_dir", lambda: user_dir)
    return user_dir


def _git(root, *args, env=None) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, env=env
    ).stdout.decode("utf-8", "replace").strip()


def tree_of(repo) -> str:
    """Tree id of the whole repository's working tree, excluding the run's .claude/audits.

    `repo` may be a subdirectory: the tree is still the whole repository's, and the
    excluded path is `repo`'s own `.claude/audits`. Mirrors cca_gate_core.fingerprint.
    """
    top = _git(repo, "rev-parse", "--show-toplevel")
    rel = os.path.relpath(os.path.realpath(repo), os.path.realpath(top)).replace(os.sep, "/")
    excluded = ":(literal)" + (".claude/audits" if rel == "." else f"{rel}/.claude/audits")
    index = _git(top, "rev-parse", "--path-format=absolute", "--git-path", "index")
    with tempfile.TemporaryDirectory() as tmp:
        tmp_index = os.path.join(tmp, "index")
        if os.path.exists(index):
            shutil.copy2(index, tmp_index)  # keep mtime: see cca_gate_core.fingerprint
        env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
        _git(top, "add", "-A", "--", ".", env=env)
        _git(top, "rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", excluded, env=env)
        return _git(top, "write-tree", env=env)


def write_output(root, value=None) -> str:
    """A JSON layer output file for `record(..., output=...)`; returns its path.

    Written under `root`'s `.claude/audits/`, which the fingerprint excludes, so creating
    it never reads as an edit to the tree under audit (a file anywhere else would make
    the fix layers owed). Each call writes a new file.
    """
    folder = pathlib.Path(root) / ".claude" / "audits" / "outputs-in"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{uuid.uuid4().hex}.json"
    path.write_text(json.dumps([] if value is None else value), encoding="utf-8")
    return str(path)


#: The order verdicts_output hands ids out in. CONFIRMED come first, so a plan_output over
#: the first `to_fix + deferred` ids names only findings L2.5 confirmed.
_VERDICT_ORDER = (("confirmed", "CONFIRMED"), ("fp", "FALSE_POSITIVE"),
                  ("uncertain", "UNCERTAIN"), ("duplicate", "DUPLICATE"),
                  ("unverified", "DEFERRED"), ("stale", "STALE"))


def finding_id(i: int) -> str:
    return f"F-{i}"


def findings_output(root, n=0, *, priorities=None) -> str:
    """An L2 file of `n` consolidated findings, F-1 to F-n, each P2 unless `priorities`
    (a dict of id to P1, P2 or P3) says otherwise."""
    priorities = priorities or {}
    rows = [{"id": finding_id(i), "auditor": "cca-bug-auditor", "category": "x",
             "priority": priorities.get(finding_id(i), "P2"), "file": "app.py", "line": i,
             "claim": f"claim {i}", "high_stakes": False} for i in range(1, n + 1)]
    return write_output(root, rows)


def verdicts_output(root, *, ids=None, **counts) -> str:
    """An L2.5 file whose verdicts match `counts`, keyed like the record flags
    (confirmed, fp, uncertain, duplicate, unverified, stale); `unverified` ids get DEFERRED.
    The ids are F-1, F-2, ... in that order, or taken in order from `ids` when given."""
    rows, i = [], 0
    for key, verdict in _VERDICT_ORDER:
        for _ in range(counts.get(key, 0)):
            rows.append({"id": ids[i] if ids is not None else finding_id(i + 1),
                         "verdict": verdict})
            i += 1
    return write_output(root, rows)


def plan_output(root, *, to_fix=0, deferred=0, ids=None) -> str:
    """An L3 file planning the first `to_fix` CONFIRMED ids and deferring the next `deferred`;
    the ids are F-1, F-2, ... or, when given, the first ones of `ids`."""
    count = to_fix + deferred
    chosen = list(ids)[:count] if ids is not None else [finding_id(i) for i in range(1, count + 1)]
    return write_output(root, {"to_fix": chosen[:to_fix], "deferred": chosen[to_fix:]})


def pool_output(repo, parent="parent") -> tuple[str, list[str]]:
    """An L2 file holding exactly the pool of the closed run `parent`, its stored L2 objects in
    pool order, as a DEFERRED run's L2 must; returns (path, pool ids)."""
    run = pathlib.Path(repo) / ".claude" / "audits" / "runs" / parent
    pool = json.loads((run / "summary.json").read_text(encoding="utf-8"))["pool"]
    stored = {f["id"]: f for f in json.loads((run / "L2.json").read_bytes())}
    return write_output(repo, [stored[i] for i in pool]), list(pool)


def append_ledger(repo, run_id, n, *, outcome_rows=0, outcome_result="fixed",
                  verdict="CONFIRMED") -> None:
    """`n` verdict rows (each `verdict`) and `outcome_rows` outcome rows for `run_id` in the
    disposition ledger. L2.5 tallies the rows per verdict, so each must match its count."""
    path = pathlib.Path(repo) / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"run_id": run_id, "auditor": "cca-bug-auditor",
                                 "category": "x", "verdict": verdict,
                                 "fix_id": f"FIX-{i}"}) + "\n")
        for i in range(outcome_rows):
            fh.write(json.dumps({"run_id": run_id, "fix_id": f"FIX-{i}",
                                 "result": outcome_result,
                                 "architect_verdict": "APPROVED"}) + "\n")


def closed_parent(repo, *, run_id="parent", p2_deferred=0, p3=3, human=0, parked=0) -> str:
    """A closed STANDARD run: fixes F-1, defers `p2_deferred` CONFIRMED P2s at L3 and `p3` P3s
    at L2.5, leaves `human` UNCERTAIN, and logs `parked` parked outcomes. Returns its run id."""
    # Imported here, not at the top: this module's docstring promises that the hole tests run
    # unchanged against a baseline where `cca_checks.pipeline_state` does not exist.
    from cca_checks import pipeline_state as ps

    confirmed = 1 + p2_deferred
    unique = confirmed + human + p3
    p3_ids = {finding_id(i): "P3" for i in range(confirmed + human + 1, unique + 1)}
    ps.start("STANDARD", run_id=run_id, root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": unique},
              output=findings_output(repo, unique, priorities=p3_ids), root=repo)
    append_ledger(repo, run_id, confirmed)
    append_ledger(repo, run_id, human, verdict="UNCERTAIN")
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": human,
                              "duplicate": 0, "unverified": p3},
              output=verdicts_output(repo, confirmed=confirmed, uncertain=human, unverified=p3),
              root=repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": 1, "deferred": p2_deferred},
              output=plan_output(repo, to_fix=1, deferred=p2_deferred), root=repo)
    app = pathlib.Path(repo) / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + f"# fixed by {run_id}\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(repo), root=repo)
    ps.record("L5", detail="green", root=repo)
    ps.record("L5.5", detail="all SAFE", root=repo)
    ps.record("L5.6", detail="1 RED", source="tool", root=repo)
    ps.record("L6", detail="APPROVED", root=repo)
    _git(repo, "commit", "-qam", f"fix: audit fixes from CCA ({run_id})")
    append_ledger(repo, run_id, 0, outcome_rows=parked, outcome_result="parked")
    ps.close(root=repo)
    return run_id


def load_hook(name: str):
    path = _HOOKS / name
    spec = importlib.util.spec_from_file_location(path.stem, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def make_repo(root: pathlib.Path, gitignore: str) -> pathlib.Path:
    """A one commit repository whose .gitignore holds exactly `gitignore`."""
    return _init(root, gitignore)


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


@pytest.fixture(autouse=True)
def _no_remote_semgrep_unless_asked(monkeypatch):
    """Keep the suite local even when the developer exports CCA_REMOTE_SEMGREP.

    The existing semgrep tests stub subprocess.run globally; with a remote configured
    they would route through ssh instead and test the wrong thing. CCA_TESTS_USE_REMOTE=1
    keeps the variable, for a deliberate live run against a real host.
    """
    if os.environ.get("CCA_TESTS_USE_REMOTE") != "1":
        monkeypatch.delenv("CCA_REMOTE_SEMGREP", raising=False)


@pytest.fixture
def east_of_utc_late_evening(monkeypatch):
    """The clock of a host two hours east of UTC, stopped at 2026-10-22 23:30 UTC, which is
    already 01:30 on 2026-10-23 there: the UTC date and the host's local date differ.

    Patches the `datetime` that `cca_checks.bugs.envelope` reads; `raising=False` so a test can
    run against code that predates that import and fail in its own assertion.
    """
    from datetime import datetime, timedelta, timezone

    from cca_checks.bugs import envelope

    instant = datetime(2026, 10, 22, 23, 30, tzinfo=timezone.utc)
    host = timezone(timedelta(hours=2))

    class HostClock(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz is not None else \
                instant.astimezone(host).replace(tzinfo=None)

    monkeypatch.setattr(envelope, "datetime", HostClock, raising=False)
    return instant
