"""The gate owes L5.4 (fix audit) to runs started by the new version, and only to them."""

from __future__ import annotations

import subprocess
import sys

import pytest
from conftest import (
    append_ledger,
    findings_output,
    load_hook,
    plan_output,
    verdicts_output,
    write_output,
)

from cca_checks import fixaudit
from cca_checks import pipeline_state as ps

core = load_hook("cca_gate_core.py")


def _state(tier, **extra):
    return {"schema": core.SCHEMA, "tier": tier, "mode": "DIFF", "no_fix": False,
            "layers": {}, **extra}


@pytest.mark.parametrize("tier", core.TIERS)
def test_a_new_run_owes_l54_after_l5_on_every_tier(tier):
    """Invariant: a fix_audit run must audit its fixes (L5.4) right after L5.

    Red 2026-11-22: baseline c3586f0 plus the names added without the table change;
    failed at `assert "L5.4" in required` (L5.4 absent from required_layers).
    """
    required = core.required_layers(_state(tier, fix_audit=True), owed=True)
    assert "L5.4" in required
    assert required.index("L5.4") == required.index("L5") + 1


@pytest.mark.parametrize("tier", core.TIERS)
def test_an_old_run_owes_exactly_what_it_owed_before(tier):
    """Invariant: a state without "fix_audit" keeps the pre-L5.4 tables byte for byte.

    Red 2026-11-22: mutation in cca_gate_core.required_layers, `state.get("fix_audit")`
    replaced by `True`; failed at `assert required == expected` (L5.4 appeared).
    """
    state = _state(tier)
    std = tier != "FAST"
    expected = ("L1", "L2", "L2.5") + (("L2.6",) if std else ()) + ("L3", "L4", "L5") \
        + (("L5.5", "L5.6") if std else ()) + ("L6",)
    assert core.required_layers(state, owed=True) == expected
    assert core.predecessors("L5.5", state, True) == ("L5",)
    assert core.predecessors("L5.6", state, True) == ("L5",)
    assert core.predecessors("L6", state, True) == (("L5", "L5.5", "L5.6") if std else ("L5",))


def _through_l3(repo, tier="STANDARD", before_l3=lambda: None):
    state = ps.start(tier, root=repo)
    ps.record("L1", detail="6 auditors", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 1}, output=findings_output(repo, 1), root=repo)
    append_ledger(repo, state.run_id, 1)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo, confirmed=1), root=repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    before_l3()
    _record_l3(repo)
    return state


def _record_l3(repo):
    ps.record("L3", counts={"to_fix": 1}, output=plan_output(repo, to_fix=1), root=repo)


def _put(repo, text):
    (repo / "app.py").write_text(text, encoding="utf-8")


def _run(repo, *args):
    """(exit code, stdout) of a git command; a failure is a value the test asserts on."""
    proc = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def test_l3_stores_the_prefix_tree(git_repo):
    """Invariant: L3 stores the tree the run held when the plan was recorded, not the start's.

    Red 2026-11-22: baseline c3586f0 plus the field added to PipelineState but never set
    in record(); failed at `assert state.prefix_tree == ...` (None != tree id). The tree now
    differs between `start` and L3, so a copy of base_tree cannot pass; red (I1) 2026-11-22
    with `_store_prefix_tree` storing `state.base_tree` in place of `tree`: failed
    `assert state.prefix_tree == core.fingerprint(git_repo)`.
    """
    _through_l3(git_repo, before_l3=lambda: _put(git_repo, "x = 1\nfeature = 1\n"))
    state = ps.load(git_repo)
    assert state.fix_audit is True
    assert state.prefix_tree == core.fingerprint(git_repo)
    assert state.prefix_tree != state.base_tree


def test_recording_l3_again_keeps_the_first_prefix_tree(git_repo):
    """Invariant: a second L3 record never replaces prefix_tree, and one after L4 at another
    tree is refused, so the fix diff still holds the fix.

    Node: tests/test_fixaudit_gate.py::test_recording_l3_again_keeps_the_first_prefix_tree.
    Red 2026-11-22 against 2b269bd's pipeline_state.py (`_check_counts` overwrote
    prefix_tree on every L3): failed `assert state.prefix_tree == first` after the re-record
    before L4. With the `elif ... "L4" in state.layers` refusal mutated to `elif False:`, it
    failed `pytest.raises(ValueError)` (DID NOT RAISE).
    """
    _through_l3(git_repo)
    first = ps.load(git_repo).prefix_tree
    _put(git_repo, "x = 2\n")  # the fix
    _record_l3(git_repo)
    state = ps.load(git_repo)
    assert state.prefix_tree == first
    ps.record("L4", fixes=1, output=write_output(git_repo), root=git_repo)
    with pytest.raises(ValueError, match="after L4 at a different tree"):
        _record_l3(git_repo)
    state = ps.load(git_repo)
    assert state.prefix_tree == first
    hunks, _ = fixaudit.fix_hunks(git_repo, state.prefix_tree, core.fingerprint(git_repo))
    assert [(h.path, h.removed, h.added) for h in hunks] == [("app.py", ("x = 1",), ("x = 2",))]


def test_the_prefix_tree_survives_gc_and_abort_removes_its_ref(git_repo):
    """Invariant: the prefix tree is pinned by refs/cca/<run_id>/prefix, so `git gc` cannot
    prune it from under the fix diff, and abort deletes the run's refs.

    Node: tests/test_fixaudit_gate.py::test_the_prefix_tree_survives_gc_and_abort_removes_its_ref.
    Red 2026-11-22 against 2b269bd's pipeline_state.py (no pin): failed at the cat-file
    assertion (the tree was pruned). With `_unpin`'s `"update-ref", "-d", ref` mutated to
    `"rev-parse", ref`, it failed the for-each-ref assertion after abort.
    """
    state = _through_l3(git_repo, before_l3=lambda: _put(git_repo, "x = 'only here'\n"))
    tree = ps.load(git_repo).prefix_tree
    _run(git_repo, "gc", "--prune=now", "--quiet")
    assert _run(git_repo, "cat-file", "-t", tree) == (0, "tree")
    assert _run(git_repo, "rev-parse", f"refs/cca/{state.run_id}/prefix") == (0, tree)
    ps.abort("done with the test", root=git_repo)
    assert _run(git_repo, "for-each-ref", "refs/cca/") == (0, "")


def test_close_removes_the_runs_refs(git_repo, monkeypatch):
    """Invariant: close deletes refs/cca/<run_id>/, so a finished run leaves no pinned tree.

    Node: tests/test_fixaudit_gate.py::test_close_removes_the_runs_refs.
    Red 2026-11-22 with close's `_unpin(...) if state.fix_audit else ''` term mutated to
    `''`: failed at the for-each-ref assertion (the prefix ref was left).
    """
    _through_l3(git_repo)
    # close's completeness rules are tested elsewhere; this test is about its cleanup only
    monkeypatch.setattr(ps, "outstanding", lambda *a, **k: [])
    ps.close(root=git_repo)
    assert _run(git_repo, "for-each-ref", "refs/cca/") == (0, "")


def test_abort_with_a_broken_git_still_retires_the_run_and_says_the_refs_stayed(git_repo,
                                                                               monkeypatch):
    """Invariant: abort is how a run with a broken git is retired, so a ref it cannot delete is
    noted in the log, never a refusal.

    Node: tests/test_fixaudit_gate.py::test_abort_with_a_broken_git_still_retires_the_run_and_says_the_refs_stayed.
    Red 2026-11-22 with `_unpin`'s final `if failed else ""` mutated to `if False else ""`:
    failed the "left in place" assertion (the note was dropped).
    """
    state = _through_l3(git_repo)
    monkeypatch.setattr(ps, "_git", lambda *a, **k: None)  # git can no longer run
    log = ps.abort("git is broken", root=git_repo)
    assert not ps.state_path(git_repo).exists()
    assert f"(refs/cca/{state.run_id}/ left in place)" in log.read_text(encoding="utf-8")


def test_a_git_failure_in_fixaudit_is_a_refusal_not_a_traceback(git_repo):
    """Invariant: a GitError (here a prefix tree pruned after its ref was deleted by hand)
    exits 2 with a reason.

    Node: tests/test_fixaudit_gate.py::test_a_git_failure_in_fixaudit_is_a_refusal_not_a_traceback.
    Red 2026-11-22 against 2b269bd's fixaudit_record.py (`except ValueError` only): failed
    `assert proc.returncode == 2` (1, the traceback's exit code).
    """
    state = _through_l3(git_repo, before_l3=lambda: _put(git_repo, "x = 'only here'\n"))
    _put(git_repo, "x = 3\n")
    ps.record("L4", fixes=1, output=write_output(git_repo, [{
        "fix_id": "FIX-1", "finding": "F-1", "files": ["app.py"], "sibling_patterns": [],
        "sibling_reason": "r"}]), root=git_repo)
    _run(git_repo, "update-ref", "-d", f"refs/cca/{state.run_id}/prefix")
    _run(git_repo, "gc", "--prune=now", "--quiet")
    proc = subprocess.run([sys.executable, "-m", "cca_checks", "fixaudit", "check", "--root",
                           str(git_repo)], capture_output=True, text=True)
    assert proc.returncode == 2
    assert proc.stderr.startswith("refused: git ") and "bad object" in proc.stderr


def test_l55_cannot_be_recorded_before_l54_in_a_new_run(git_repo):
    """Invariant: in a fix_audit run L5.5 has L5.4 as predecessor.

    Red 2026-11-22: mutation in cca_gate_core.predecessors, `"L5.5": ("L5.4",)` replaced
    by `("L5",)`; failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    _through_l3(git_repo)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(git_repo), root=git_repo)
    ps.record("L5", detail="green", root=git_repo)
    with pytest.raises(ValueError, match=r"L5\.4"):
        ps.record("L5.5", detail="all SAFE", root=git_repo)
