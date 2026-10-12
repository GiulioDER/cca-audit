"""Red to green tests for the gate defects the pre-0.11.0 CCA run confirmed.

One test per CONFIRMED P1 finding (U01 to U09 in the run's L2.5 output), each driving
the real API or the real hook against a throwaway git repository, plus the controls
that keep a fix from over-blocking. The red proof of every test is the pre-fix code
at 0ec8c47, executed by `cca_tautology_check.py verify` at Step 5.6.

Merged into feat/deferred-pass on 2026-10-11, where `record` holds the L2, L2.5 and L3
files to their counts and to each other. `_findings`, `_verdicts` and `_plan` now build files
of that shape; no assertion moved. Each test whose fixture changed was seen red again that
day, against a mutation of the merged production line, in its own assertion:
- U01 accept: `total = judged + unverified` became `total = judged` in
  pipeline_state._check_counts; failed at `pytest.fail` (L2.5 refused).
- U04: `_fixes_planned` in cca_gate_core returned False; failed at the `outstanding`
  assertion (only "L6 has no recorded verdict" was owed).
- U09: `if planned > a.parked:` became `if False:` in cca_tautology_check.main; failed at
  `assert 0 != 0`.
- U09 parked control: `planned > a.parked` became `planned > 0`; failed at `assert 2 == 0`.
- U15: `if unknown or mismatched:` became `if False:` in _check_counts; failed at
  pytest.raises (DID NOT RAISE). Its L2.5 file now holds three CONFIRMED verdicts: with the
  empty file it had before, the L2.5 shape check refused it with the same word, so the test
  stayed green with the ledger tally disabled.
The U01, U04 and U09 (P2 only) controls are labelled controls against over-blocking.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import subprocess
import sys

import pytest
from conftest import load_hook, tree_of, write_output

from cca_checks import pipeline_state as ps

guard = load_hook("cca_pipeline_guard.py")
core = load_hook("cca_gate_core.py")

_TOOL = (
    pathlib.Path(__file__).resolve().parents[1]
    / "cca_checks" / "plugin" / "tools" / "cca_tautology_check.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("cca_tautology_check_u09", _TOOL)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass looks its module up in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _guard_exit(monkeypatch, repo, command: str) -> int:
    monkeypatch.chdir(repo)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    return guard.main()


def _d(seq, detail="done", **extra):
    return {"status": "done", "detail": detail, "seq": seq, **extra}


def _write_state(repo, layers, *, base="stale-base", fixes=1, tier="STANDARD"):
    path = repo / ".claude" / "audits" / "run-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": 2, "run_id": "r1", "tier": tier, "mode": "DIFF", "no_fix": False,
        "started_utc": "2026-10-11T00:00:00Z", "base_tree": base, "fixes_applied": fixes,
        "seq": 20, "layers": layers,
    }), encoding="utf-8")


def _complete(tree):
    return {"L1": _d(1), "L2": _d(2), "L2.5": _d(3), "L2.6": _d(4, source="tool"),
            "L3": _d(5), "L4": _d(6), "L5": _d(7, tree=tree), "L5.5": _d(8, tree=tree),
            "L5.6": _d(9, source="tool", tree=tree), "L6": _d(10, "APPROVED", tree=tree)}


# Since the deferred-pass merge, `record` holds the L2, L2.5 and L3 files to their counts and
# to each other (an id and a priority per L2 finding, a verdict per L2 id, a plan of CONFIRMED
# ids), so these helpers build files of that shape. Fixture support only: no assertion moved.
def _findings(n, **priority):
    """L2 findings U01 to U<n>, each P2 unless `priority` (U01="P1", ...) says otherwise."""
    return [{"id": f"U{i:02d}", "priority": priority.get(f"U{i:02d}", "P2")}
            for i in range(1, n + 1)]


def _verdicts(**tally):
    """L2.5 verdicts for U01 onward, handed out in the order `tally` names them."""
    out = []
    for verdict, n in tally.items():
        for _ in range(n):
            out.append({"id": f"U{len(out) + 1:02d}", "verdict": verdict})
    return out


def _plan(to_fix=(), deferred=()):
    """An L3 file: the ids planned for a fix, and the CONFIRMED ids deferred."""
    return {"to_fix": list(to_fix), "deferred": list(deferred)}


def _ledger(repo, run_id, rows):
    path = repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for priority, verdict in rows:
            fh.write(json.dumps({"run_id": run_id, "auditor": "a", "category": "c",
                                 "priority": priority, "verdict": verdict}) + "\n")


# --- U01/U02: L2.5 must be recordable when P3 findings are deferred unverified ---------

def test_u01_l25_accepts_unverified_findings(git_repo):
    """Invariant: a run that verifies only its P1/P2 findings, as Step 2.5 says, can record
    L2.5; the deferred P3s are counted as --unverified, not as invented verdicts.

    Failure mode: the code demanded confirmed+fp+uncertain+duplicate == L2 --unique, so
    any run with a P3 was refused at L2.5 (U01, hit live by run 20261011T074020Z).
    Red proof: pre-fix pipeline_state._check_counts at 0ec8c47 refuses with
    "L2.5 accounts for 1 findings but L2 recorded 3 unique".
    """
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 3},
              output=write_output(git_repo, _findings(3, U01="P1", U02="P3", U03="P3")),
              root=git_repo)
    _ledger(git_repo, state.run_id, [("P1", "CONFIRMED")])
    try:
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "unverified": 2},
                  output=write_output(git_repo, _verdicts(CONFIRMED=1, DEFERRED=2)),
                  root=git_repo)
    except ps.RecordRefused as exc:
        pytest.fail(f"L2.5 with 2 unverified P3s was refused: {exc}")
    assert "L2.5" in ps.load(git_repo).layers


def test_u01_control_unverified_cannot_hide_a_missing_verdict(git_repo):
    """Control: --unverified does not relax the total; verdicts + unverified must still
    equal L2 --unique. Passes before and after the fix (pre-fix the total is short too)."""
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 3}, output=write_output(git_repo, _findings(3)),
              root=git_repo)
    with pytest.raises(ps.RecordRefused):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "unverified": 1},
                  output=write_output(git_repo, _verdicts(CONFIRMED=1, DEFERRED=1)),
                  root=git_repo)


# --- U03: the commit ships the INDEX, so the index must be what was judged ------------

def test_u03_guard_refuses_staged_content_that_differs_from_the_judged_tree(
        monkeypatch, git_repo):
    """Invariant: a commit is allowed only when what it ships (the index) is the tree the
    fix verdicts judged.

    Failure mode: the fingerprint hashes the WORKING TREE; content staged and then
    reverted in the working tree was committed while every verdict read fresh (U03).
    Red proof: pre-fix guard at 0ec8c47 returns 0 here.
    """
    app = git_repo / "app.py"
    app.write_text("x = 2  # reviewed\n", encoding="utf-8")
    _write_state(git_repo, _complete(tree_of(git_repo)))
    app.write_text("x = 666  # staged, never reviewed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "app.py"], check=True,
                   capture_output=True)
    app.write_text("x = 2  # reviewed\n", encoding="utf-8")
    assert _guard_exit(monkeypatch, git_repo, "git commit -m fix") == 2


def test_u03_control_staged_reviewed_content_may_commit(monkeypatch, git_repo):
    """Control against over-blocking: index == judged tree, commit allowed before and after."""
    app = git_repo / "app.py"
    app.write_text("x = 2  # reviewed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "app.py"], check=True,
                   capture_output=True)
    _write_state(git_repo, _complete(tree_of(git_repo)))
    assert _guard_exit(monkeypatch, git_repo, "git commit -m fix") == 0


# --- U04: an L3 that planned fixes owes L4 even before the tree changes ---------------

def test_u04_l6_refused_after_a_plan_with_fixes_and_no_l4(git_repo):
    """Invariant: when L3 planned fixes, L6 cannot be recorded (and the run cannot close)
    until L4 onward ran.

    Failure mode: only next_owed knew about planned fixes; predecessors() let L6 follow
    L3 directly, and close() logged the run COMPLETE with its fixes never applied (U04,
    seen live: `record L3 --to-fix 17` answered `outstanding: [L6]`).
    Red proof: pre-fix record("L6") at 0ec8c47 is accepted.
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2}, output=write_output(git_repo, _findings(2)),
              root=git_repo)
    ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(git_repo, _verdicts(CONFIRMED=2)), root=git_repo)
    ps.record("L3", counts={"to_fix": 2}, output=write_output(git_repo, _plan(["U01", "U02"])),
              root=git_repo)
    state = ps.load(git_repo)
    assert any(r.startswith("L4 ") for r in ps.outstanding(state, git_repo)), \
        ps.outstanding(state, git_repo)
    with pytest.raises(ps.RecordRefused):
        ps.record("L6", detail="APPROVED", root=git_repo)


def test_u04_control_a_plan_with_no_fixes_goes_straight_to_l6(git_repo):
    """Control: --to-fix 0 still lets L6 follow L3. Passes before and after the fix."""
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=write_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=write_output(git_repo, _plan()), root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    assert ps.outstanding(ps.load(git_repo), git_repo) == []


# --- U05: an unknown fingerprint must never make a verdict read fresh -----------------

def test_u05_unknown_fingerprint_makes_fix_verdicts_stale():
    """Invariant: when the tree cannot be fingerprinted (None), a fix verdict cannot be
    shown current, so the commit is refused.

    Failure mode: `entry["tree"] != tree` compared None with None, so on any host where
    the fingerprint is unavailable (timeout, git missing, git < 2.31) every L5..L6
    verdict read fresh after any later edit (U05).
    Red proof: pre-fix cca_gate_core.commit_reasons at 0ec8c47 returns [].
    """
    state = {"schema": 2, "run_id": "r1", "tier": "STANDARD", "mode": "DIFF",
             "no_fix": False, "base_tree": None, "fixes_applied": 1, "seq": 20,
             "layers": _complete(None)}
    assert core.commit_reasons(state, None) != []


# --- U08: an edit chained into the commit command is invisible to PreToolUse ----------

def test_u08_guard_refuses_a_commit_chained_after_an_edit(monkeypatch, git_repo):
    """Invariant: while a run is open, the guard judges exactly the tree that is committed,
    so it refuses a commit that shares its command line with anything else.

    Failure mode: PreToolUse fingerprints BEFORE the command runs, so
    `<edit> && git add f && git commit` was judged on the pre-edit tree and committed an
    edit no verdict saw (U08).
    Red proof: pre-fix guard at 0ec8c47 returns 0 here.
    """
    app = git_repo / "app.py"
    app.write_text("x = 2  # reviewed\n", encoding="utf-8")
    _write_state(git_repo, _complete(tree_of(git_repo)))
    command = "echo 'y = 3' >> app.py && git add app.py && git commit -m fix"
    assert _guard_exit(monkeypatch, git_repo, command) == 2


@pytest.mark.parametrize("command", [
    "git commit -m fix",
    "git -C . commit -m 'a; b && c | d'",
    "git commit -m \"$(cat <<'EOF'\nfix: one; two && three\n\nbody\nEOF\n)\"",
])
def test_u08_control_a_lone_commit_is_still_allowed(monkeypatch, git_repo, command):
    """Control against over-blocking: a lone commit, including separators inside quotes and
    a heredoc message, is allowed on a complete, fresh run before and after the fix."""
    app = git_repo / "app.py"
    app.write_text("x = 2  # reviewed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "app.py"], check=True,
                   capture_output=True)
    _write_state(git_repo, _complete(tree_of(git_repo)))
    assert _guard_exit(monkeypatch, git_repo, command) == 0


# --- U09: --no-proofs must not satisfy L5.6 on a run that fixed a CONFIRMED P1 --------

def _run_to_l5(repo, *, p1_confirmed: bool, to_fix: int):
    state = ps.start("STANDARD", root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 1},
              output=write_output(repo, _findings(1, U01="P1" if p1_confirmed else "P2")),
              root=repo)
    _ledger(repo, state.run_id, [("P1" if p1_confirmed else "P2", "CONFIRMED")])
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(repo, _verdicts(CONFIRMED=1)), root=repo)
    ps.record("L2.6", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": to_fix, "deferred": 1 - to_fix},
              output=write_output(repo, _plan(["U01"][:to_fix], ["U01"][to_fix:])), root=repo)
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(repo), root=repo)
    ps.record("L5", root=repo)


def test_u09_no_proofs_refused_when_a_confirmed_p1_was_planned(monkeypatch, git_repo):
    """Invariant: L5.6 is recorded with zero proofs only when the run has no CONFIRMED P1
    to prove.

    Failure mode: `verify --no-proofs --record` recorded L5.6 done, source tool, without
    reading anything, so the tool-only red-proof gate was satisfied by a flag on a run
    that fixed a confirmed P1 (U09).
    Red proof: pre-fix cca_tautology_check.main at 0ec8c47 returns 0 and records L5.6.
    """
    _run_to_l5(git_repo, p1_confirmed=True, to_fix=1)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(
        "sys.argv", ["cca_tautology_check.py", "verify", "--no-proofs", "--record"])
    assert _load_tool().main() != 0
    assert "L5.6" not in ps.load(git_repo).layers


def test_u09_control_no_proofs_still_records_when_only_p2_was_fixed(monkeypatch, git_repo):
    """Control: a run whose only confirmed finding is a P2 records L5.6 with zero proofs,
    before and after the fix."""
    _run_to_l5(git_repo, p1_confirmed=False, to_fix=1)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr(
        "sys.argv", ["cca_tautology_check.py", "verify", "--no-proofs", "--record"])
    assert _load_tool().main() == 0
    assert ps.load(git_repo).layers["L5.6"]["source"] == "tool"


# --- P2 fixes: behaviour tests, proven red against 0ec8c47 at Step 5.6 ----------------

def test_u14_start_refuses_a_reused_run_id(git_repo):
    """Invariant: a run id names one run. Failure mode: a reused id counted the earlier
    run's ledger rows toward this run's L2.5, so L2.5 passed with no rows of its own (U14).
    Red proof: pre-fix start at 0ec8c47 accepts the reused id."""
    _ledger(git_repo, "pr-63", [("P2", "CONFIRMED")])
    with pytest.raises(ps.RecordRefused, match="used by an earlier run"):
        ps.start("STANDARD", run_id="pr-63", root=git_repo)


def test_u15_ledger_breakdown_must_match_the_counts(git_repo):
    """Invariant: the L2.5 counts agree with the ledger verdict by verdict. Failure mode:
    only the totals were compared, so three FALSE_POSITIVE rows backed --confirmed 3 and
    raised the L3 ceiling (U15). Red proof: pre-fix record at 0ec8c47 accepts it."""
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 3}, output=write_output(git_repo, _findings(3)),
              root=git_repo)
    _ledger(git_repo, state.run_id, [("P2", "FALSE_POSITIVE")] * 3)
    with pytest.raises(ps.RecordRefused, match="disagrees"):
        ps.record("L2.5", counts={"confirmed": 3, "fp": 0, "uncertain": 0, "duplicate": 0},
                  output=write_output(git_repo, _verdicts(CONFIRMED=3)), root=git_repo)


def test_u17_install_warns_about_a_short_guard_timeout(tmp_path, monkeypatch):
    """Invariant: an armed guard whose timeout is below the printed 60 s is reported.
    Failure mode: a 0.10.x entry kept its 15 s timeout, the 30 s fingerprint timed it out,
    and a timed-out hook lets the commit through; install said nothing (U17).
    Red proof: pre-fix install at 0ec8c47 returns no such warning."""
    from cca_checks import plugin
    monkeypatch.setattr(pathlib.Path, "home", classmethod(lambda cls: tmp_path / "home"))
    settings = tmp_path / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({"hooks": {
        "PreToolUse": [{"matcher": "Bash", "hooks": [
            {"type": "command", "command": "python .claude/hooks/cca_pipeline_guard.py",
             "timeout": 15}]}],
        "Stop": [{"hooks": [
            {"type": "command", "command": "python .claude/hooks/cca_stop_guard.py"}]}],
    }}), encoding="utf-8")
    result = plugin.install(tmp_path)
    assert any("cca_pipeline_guard.py" in w and "15" in w for w in result.warnings), \
        result.warnings
    assert not any("cca_stop_guard.py in" in w for w in result.warnings), result.warnings


def test_u19_print_hook_warns_when_the_hooks_are_not_installed(tmp_path, capsys):
    """Invariant: printing the hook entries for a target without the hook files says so.
    Failure mode: a Stop hook registered before its script exists exits 2 and refuses
    every turn end (U19). Red proof: pre-fix --print-hook at 0ec8c47 prints no warning."""
    from cca_checks.plugin.cli import main
    assert main(["install", "--print-hook", "--target", str(tmp_path)]) == 0
    assert "not installed" in capsys.readouterr().err


def test_u25_stop_guard_skips_the_fingerprint_once_spent_and_logged(
        monkeypatch, capsys, git_repo):
    """Invariant: after the refusal cap is reached and logged, a turn end costs no
    fingerprint. Failure mode: it fingerprinted (up to 30 s) and discarded the result on
    every turn end (U25). Red proof: pre-fix stop guard at 0ec8c47 calls fingerprint."""
    stop = load_hook("cca_stop_guard.py")
    ps.start("FAST", root=git_repo)
    path = git_repo / ".claude" / "audits" / "run-state.json"
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update(stop_refusals=3, stop_logged=True)
    path.write_text(json.dumps(raw), encoding="utf-8")
    real_core = stop._load_core()
    calls = []
    monkeypatch.setattr(real_core, "fingerprint", lambda root: calls.append(root))
    monkeypatch.setattr(stop, "_load_core", lambda: real_core)
    body = {"hook_event_name": "Stop", "cwd": str(git_repo), "stop_hook_active": False}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(body)))
    assert stop.main() == 0
    assert capsys.readouterr().out.strip() == ""
    assert calls == []


# --- controls added after the L5.5 review (attempt 2 of FIX-02 and FIX-06) -------------

def test_u03_control_staging_a_subset_of_the_judged_files_may_commit(monkeypatch, git_repo):
    """Control: staging only some judged files (the default mode's separate commit for the
    fixes) is allowed; each staged file is content a verdict saw. Passes before and after.

    Red proof (mutation, 2026-10-11): `staged_ok = not dirty` (the first FIX-02 attempt,
    refusing whenever a TRACKED file is modified and unstaged) fails `assert 2 == 0`.
    """
    git = ["git", "-C", str(git_repo)]
    (git_repo / "lib.py").write_text("y = 0\n", encoding="utf-8")
    subprocess.run([*git, "add", "lib.py"], check=True, capture_output=True)
    subprocess.run([*git, "commit", "-q", "-m", "lib"], check=True, capture_output=True)
    (git_repo / "app.py").write_text("x = 2  # reviewed fix\n", encoding="utf-8")
    (git_repo / "lib.py").write_text("y = 1  # feature, judged too, left unstaged\n",
                                     encoding="utf-8")
    subprocess.run([*git, "add", "app.py"], check=True, capture_output=True)
    _write_state(git_repo, _complete(tree_of(git_repo)))
    assert _guard_exit(monkeypatch, git_repo, "git commit -m fix") == 0


def test_u03_control_a_staged_then_changed_audit_file_is_ignored(monkeypatch, tmp_path):
    """Control: a repository that TRACKS its .claude/audits trail, with a file there staged
    and then changed again, still commits; the run's own trail never perturbs the gate.
    (The L5.5 reviewer's reproduction of the first FIX-02 attempt.)

    Red proof (mutation, 2026-10-11): dropping the `:(exclude,literal)` audits pathspec
    from the staged check fails `assert 2 == 0`.
    """
    from conftest import make_repo
    repo = make_repo(tmp_path, "")
    trail = repo / ".claude" / "audits" / "notes.md"
    trail.parent.mkdir(parents=True)
    trail.write_text("v1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", str(trail)], check=True,
                   capture_output=True)
    trail.write_text("v2\n", encoding="utf-8")
    (repo / "app.py").write_text("x = 2  # reviewed\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "app.py"], check=True, capture_output=True)
    _write_state(repo, _complete(tree_of(repo)))
    assert _guard_exit(monkeypatch, repo, "git commit -m fix") == 0


def test_u09_control_parked_p1_is_declared_and_recorded(monkeypatch, git_repo):
    """Control: a run whose only CONFIRMED P1 was parked records L5.6 with zero proofs once
    it says so with --parked, and the count is visible in the entry.

    Red proof (mutation, 2026-10-11): `planned > a.parked` changed to `planned > 0` (the
    first FIX-06 attempt, which trapped such a run) fails `assert 2 == 0`.
    """
    _run_to_l5(git_repo, p1_confirmed=True, to_fix=1)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("sys.argv", ["cca_tautology_check.py", "verify", "--no-proofs",
                                     "--parked", "1", "--record"])
    assert _load_tool().main() == 0
    assert "1 CONFIRMED P1 parked" in ps.load(git_repo).layers["L5.6"]["detail"]
