"""Red to green tests for the follow-up CCA run on 554c8c0 (run 20261011T121347Z).

One test per CONFIRMED finding that has observable behaviour (V02 to V15 in the run's
L2.5 output), plus controls against over-blocking. The red proof of each test is the
code at 554c8c0, executed by `cca_tautology_check.py verify` at Step 5.6.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib
import subprocess
import sys

import pytest
from conftest import load_hook, make_repo, tree_of, write_output

from cca_checks import pipeline_state as ps

guard = load_hook("cca_pipeline_guard.py")
core = load_hook("cca_gate_core.py")

_TOOL = (
    pathlib.Path(__file__).resolve().parents[1]
    / "cca_checks" / "plugin" / "tools" / "cca_tautology_check.py"
)


def _load_tool():
    spec = importlib.util.spec_from_file_location("cca_tautology_check_followup", _TOOL)
    module = importlib.util.module_from_spec(spec)
    # Registered before exec: @dataclass looks its module up in sys.modules.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _guard_exit(monkeypatch, repo, command: str) -> int:
    monkeypatch.chdir(repo)
    payload = json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    return guard.main()


def _d(seq, detail="done", **extra):
    return {"status": "done", "detail": detail, "seq": seq, **extra}


def _complete_run(repo):
    """A finished STANDARD run whose fix verdicts judged the tree as it is now."""
    tree = tree_of(repo)
    layers = {"L1": _d(1), "L2": _d(2), "L2.5": _d(3), "L2.6": _d(4, source="tool"),
              "L3": _d(5), "L4": _d(6), "L5": _d(7, tree=tree), "L5.5": _d(8, tree=tree),
              "L5.6": _d(9, source="tool", tree=tree), "L6": _d(10, "APPROVED", tree=tree)}
    path = repo / ".claude" / "audits" / "run-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "schema": 2, "run_id": "r1", "tier": "STANDARD", "mode": "DIFF", "no_fix": False,
        "started_utc": "2026-10-11T00:00:00Z", "base_tree": "stale-base",
        "fixes_applied": 1, "seq": 20, "layers": layers,
    }), encoding="utf-8")


def _ledger(repo, run_id, rows):
    path = repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for priority, verdict in rows:
            fh.write(json.dumps({"run_id": run_id, "auditor": "a", "category": "c",
                                 "priority": priority, "verdict": verdict}) + "\n")


def _no_proofs(monkeypatch, repo, *extra) -> int:
    monkeypatch.chdir(repo)
    monkeypatch.setattr("sys.argv", ["cca_tautology_check.py", "verify", "--no-proofs",
                                     *extra, "--record"])
    return _load_tool().main()


def _run_through_l3(repo, *, rows, to_fix, tier="DEEP"):
    state = ps.start(tier, root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": len(rows)}, output=write_output(repo), root=repo)
    _ledger(repo, state.run_id, rows)
    tally = {"confirmed": sum(v == "CONFIRMED" for _, v in rows), "fp": 0, "uncertain": 0,
             "duplicate": 0}
    ps.record("L2.5", counts=tally, output=write_output(repo), root=repo)
    ps.record("L2.6", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": to_fix}, output=write_output(repo), root=repo)
    return state


def _fix_through_l55(repo, fixes):
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=fixes, output=write_output(repo), root=repo)
    ps.record("L5", root=repo)
    ps.record("L5.5", root=repo)


# --- V02 (G-01): a staged deletion with the file back on disk ---------------------------

def test_v02_guard_refuses_a_staged_deletion_of_a_judged_file(monkeypatch, git_repo):
    """Invariant: the commit ships what the verdicts judged; a judged file the commit would
    delete is not that. Failure mode: `git rm --cached f` left f on disk, so the staged
    check (index vs working tree name lists) saw nothing and the commit dropped a judged
    file (V02). Red proof: the guard at 554c8c0 returns 0 here."""
    (git_repo / "app.py").write_text("x = 2  # reviewed\n", encoding="utf-8")
    _git(git_repo, "add", "app.py")
    _complete_run(git_repo)
    _git(git_repo, "rm", "--cached", "-q", "app.py")
    assert (git_repo / "app.py").exists()
    assert _guard_exit(monkeypatch, git_repo, "git commit -m fix") == 2


def test_v02_control_a_real_deletion_may_commit(monkeypatch, git_repo):
    """Control: deleting a file from disk and the index, as judged, still commits."""
    (git_repo / "gone.py").write_text("y = 1\n", encoding="utf-8")
    _git(git_repo, "add", "gone.py")
    _git(git_repo, "commit", "-q", "-m", "add gone")
    _git(git_repo, "rm", "-q", "gone.py")
    _complete_run(git_repo)
    assert _guard_exit(monkeypatch, git_repo, "git commit -m fix") == 0


def test_v08_guard_refuses_a_staged_path_with_leading_whitespace(monkeypatch, tmp_path):
    """Invariant: a staged path whose content differs from the working tree is refused
    whatever its name. Failure mode: `run()` stripped `-z` output, so a leading space
    survived in one list and not the other and the intersection missed it (V08).
    Red proof: the guard at 554c8c0 returns 0 here."""
    repo = make_repo(tmp_path, ".claude/\n")
    for name in ("  a.txt", " f.py"):
        (repo / name).write_text("v1\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "names")
    # Judged tree: '  a.txt' modified and left unstaged, ' f.py' at v2 and staged.
    (repo / "  a.txt").write_text("dirty, unstaged\n", encoding="utf-8")
    (repo / " f.py").write_text("v2\n", encoding="utf-8")
    _git(repo, "add", " f.py")
    _complete_run(repo)
    # Unjudged content staged, the working tree restored to the judged v2.
    (repo / " f.py").write_text("UNJUDGED\n", encoding="utf-8")
    _git(repo, "add", " f.py")
    (repo / " f.py").write_text("v2\n", encoding="utf-8")
    assert _guard_exit(monkeypatch, repo, "git commit -m fix") == 2


# --- V03 (G-02): quotes in a comment, ANSI-C strings, unterminated quotes ---------------

@pytest.mark.parametrize("command", [
    "# don't stage before the fix\nprintf X > app.py\ngit add app.py\ngit commit -qm fix",
    "echo $'\\'' ; printf X > app.py ; git commit -qam fix",
    "git commit -m 'unterminated",
])
def test_v03_chained_with_does_not_fail_open_on_quotes(command):
    """Invariant: the scanner never reports a command lone when bash would run more than
    the commit, and never when it lost track of quoting. Failure mode: an apostrophe in a
    `#` comment (or a `$'...'` string) opened a quote bash never opens, every later
    separator read as quoted, and an unterminated quote returned None (V03).
    Red proof: chained_with at 554c8c0 returns None for each.
    Case 2 is refused only through the unterminated-quote rule: `$'...'` is still read as
    a plain single quote (parked, CHANGELOG Known gaps), so a spelling with an even
    number of quotes is not caught."""
    assert guard.chained_with(command) is not None


# --- V11 (G-05, PARKED at the attempt cap): the commit form that must stay allowed -------

@pytest.mark.parametrize("command", [
    "git commit -m \"$(cat <<'EOF'\nfix: one; two && three, it's \"quoted\"\n\nbody\nEOF\n)\"",
    "git commit -m \"$(cat <<\"EOF\"\nfix\nEOF\n)\"",
    "git commit -m 'literal $(not run) and `not run`'",
])
def test_v11_control_the_heredoc_message_form_is_still_lone(command):
    """Control: the pipeline's own commit form, a quoted heredoc fed to `cat` inside the
    message, and substitution syntax inside single quotes stay allowed."""
    assert guard.chained_with(command) is None


# --- V12 (G-06): a commit that redirects git's index or repository ----------------------

@pytest.mark.parametrize("command", [
    "GIT_INDEX_FILE=.git/alt git commit -m x",
    "env GIT_DIR=.git git commit -m x",
    "git --git-dir=.git commit -m x",
    "git --work-tree . commit -m x",
])
def test_v12_guard_refuses_a_redirected_index_or_repository(monkeypatch, git_repo, command):
    """Invariant: the staged check judges the index the commit ships. Failure mode:
    `GIT_INDEX_FILE=<alt> git commit` shipped an index the check never read (V12).
    Red proof: the guard at 554c8c0 returns 0 for each on a complete, fresh run."""
    _complete_run(git_repo)
    assert _guard_exit(monkeypatch, git_repo, command) == 2


# --- V10 (G-04): L4 --fixes 0 must not discharge a plan --------------------------------

def test_v10_l6_refused_after_a_plan_whose_l4_applied_nothing(git_repo):
    """Invariant: an L3 that planned fixes owes L4 through L5.6 even if L4 reports none
    applied. Failure mode: any L4 after L3 cancelled the plan, L5 to L5.6 dropped out and
    L6 followed L4 directly (V10). Red proof: record L6 at 554c8c0 is accepted."""
    _run_through_l3(git_repo, rows=[("P1", "CONFIRMED"), ("P1", "CONFIRMED")], to_fix=2)
    ps.record("L4", fixes=0, output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused):
        ps.record("L6", detail="APPROVED", root=git_repo)


# --- V04, V13, V15 (G-03): the zero-proof claim -----------------------------------------

def test_v04_parked_cannot_exceed_the_ledger_p1_count(monkeypatch, git_repo):
    """Invariant: --parked is a count of parked CONFIRMED P1s, so it cannot exceed the
    ledger's CONFIRMED P1 count. Failure mode: --parked 99 recorded L5.6 with zero proofs
    after every planned P1 fix was applied (V04). Red proof: the tool at 554c8c0 returns
    0 here. (A bound by unapplied fixes was tried and refused the documented
    park-after-L4 flow; see FIX_JOURNAL G-03 attempt 1.)"""
    _run_through_l3(git_repo, rows=[("P1", "CONFIRMED"), ("P1", "CONFIRMED")], to_fix=2)
    _fix_through_l55(git_repo, fixes=2)
    assert _no_proofs(monkeypatch, git_repo, "--parked", "99") != 0
    assert "L5.6" not in ps.load(git_repo).layers


def test_v13_no_proofs_refused_for_an_unplanned_edit(monkeypatch, git_repo):
    """Invariant: the zero-proof claim is checked whenever fixes landed, not only when L3
    planned them. Failure mode: L3 --to-fix 0 followed by an edit let --no-proofs pass
    with CONFIRMED P1s in the ledger (V13). Red proof: the tool at 554c8c0 returns 0."""
    _run_through_l3(git_repo, rows=[("P1", "CONFIRMED")], to_fix=0)
    _fix_through_l55(git_repo, fixes=1)
    assert _no_proofs(monkeypatch, git_repo) != 0


def test_v15_no_proofs_refused_when_the_ledger_is_missing(monkeypatch, git_repo):
    """Invariant: an unreadable ledger on a run whose L2.5 confirmed findings is a
    refusal, not zero P1. Failure mode: deleting the ledger after L2.5 (nothing goes
    stale, .claude/audits is excluded) let --no-proofs pass (V15).
    Red proof: the tool at 554c8c0 returns 0 here."""
    _run_through_l3(git_repo, rows=[("P1", "CONFIRMED")], to_fix=1)
    _fix_through_l55(git_repo, fixes=1)
    (git_repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl").unlink()
    assert _no_proofs(monkeypatch, git_repo) != 0


def test_v15_no_proofs_refused_when_the_run_rows_are_gone(monkeypatch, git_repo):
    """Invariant: fewer CONFIRMED ledger rows for the run than L2.5 confirmed is a refusal,
    whether the file was deleted, emptied or rewritten. Failure mode: the first fix only
    checked that the file existed, so a ledger truncated to empty (a `>` typed for `>>`)
    still read as zero P1 and recorded L5.6 with no proof (L6 REVISE, V15).
    Red proof: the tool at 554c8c0, and the `is_file()` version of the first fix
    (measured by the architect), return 0 here."""
    _run_through_l3(git_repo, rows=[("P1", "CONFIRMED")], to_fix=1)
    _fix_through_l55(git_repo, fixes=1)
    (git_repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl").write_text(
        "", encoding="utf-8")
    assert _no_proofs(monkeypatch, git_repo) != 0
    assert "L5.6" not in ps.load(git_repo).layers


def test_v04_control_a_genuinely_parked_p1_still_records(monkeypatch, git_repo):
    """Control: one P1 and one P2 planned, only the P2 fix applied, the P1 parked:
    --parked 1 records L5.6 with zero proofs and says so."""
    _run_through_l3(git_repo, rows=[("P1", "CONFIRMED"), ("P2", "CONFIRMED")], to_fix=2)
    _fix_through_l55(git_repo, fixes=1)
    assert _no_proofs(monkeypatch, git_repo, "--parked", "1") == 0
    assert "1 CONFIRMED P1 parked" in ps.load(git_repo).layers["L5.6"]["detail"]


# --- added after the L5.5 review (attempt 1 of G-02, G-03, G-05) -------------------------

@pytest.mark.parametrize("command", [
    "echo evil > app.py ${x:- #}; git add app.py; git commit -m x",
    "echo evil > app.py ${x/ #/y}; git commit -am x",
    "(( 1 #)); echo evil > app.py; git commit -am x",
    "(#'\necho evil > app.py\ngit add app.py\ngit commit -m x ) #'",
    # Round 2: contexts where bash keeps '#' literal and the scanner does not model them.
    'echo evil > app.py "${x:-" #"}"; git commit -am x',
    "[[ a == @(#a|b) ]]; echo evil > app.py; git commit -am x",
    "[[ a == +( #) ]]; echo evil > app.py; git commit -am x",
])
def test_g02_hash_is_literal_inside_expansions_and_a_comment_after_paren(command):
    """Invariant: a comment the scanner skips never hides a command bash would run.
    Failure mode: the first attempt skipped to the end of the line after ` #` inside
    `${...}` and `((...))`, where bash keeps `#` literal, hiding the `;` that HEAD
    refused; `(` was not a word boundary; and bash also keeps `#` literal in contexts
    the scanner does not model (L5.5 G-02, rounds 1 and 2).
    Red proofs (2026-10-11): case 4 (`(#'...`) returns None at 554c8c0, a red proof of
    V03. Cases 1 to 3 and 5 to 7 are refused at 554c8c0 too, so they are regression
    guards proven by mutation: 1 to 3 fail when `#` is a comment whatever the nesting
    and `(` is no boundary (attempt 1); 5 to 7 fail when the `_HIDES_A_COMMAND` check is
    disabled (attempt 2)."""
    assert guard.chained_with(command) is not None


def test_g03_control_parking_after_l4_records_with_parked(monkeypatch, git_repo):
    """Control: the documented park flow. L3 plans the P1 fix, L4 records it, a later gate
    parks it and its edit is reverted without re-recording L4; --parked 1 records L5.6.
    Red proof (mutation): bounding --parked by to_fix minus fixes applied (the first
    G-03 attempt) refuses it."""
    _run_through_l3(git_repo, rows=[("P1", "CONFIRMED")], to_fix=1)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(git_repo), root=git_repo)
    (git_repo / "app.py").write_text("x = 1\n", encoding="utf-8")  # parked: reverted
    ps.record("L5", root=git_repo)
    ps.record("L5.5", root=git_repo)
    assert _no_proofs(monkeypatch, git_repo, "--parked", "1") == 0
    assert "1 CONFIRMED P1 parked" in ps.load(git_repo).layers["L5.6"]["detail"]
