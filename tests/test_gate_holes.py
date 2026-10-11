"""The holes the 2026-10-10 spec closes, written so they run UNCHANGED on d2317de.

They build the run state by hand and drive the commit guard and `close()`
directly, using only APIs that exist at d2317de, so the red proof is against the
real baseline rather than a mutation. Each must fail there in its assertion.
"""

from __future__ import annotations

import io
import json

import pytest
from conftest import load_hook, tree_of

from cca_checks import pipeline_state as ps

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
    """Baseline d2317de: guard returns 0 because fixes_applied == 0.

    Red at d2317de 2026-10-10: assert 0 == 2
    """
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = {"L1": _d(1), "L2": _d(2), "L2.5": _d(3), "L2.6": _d(4, source="tool"),
              "L3": _d(5), "L6": _d(6, "APPROVED")}
    _write(git_repo, _run(git_repo, layers))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole2_n_a_does_not_satisfy_a_required_layer(monkeypatch, git_repo):
    """Baseline: `n/a` is not in the guard's BAD_STATUS, so it passes.

    Red at d2317de 2026-10-10: assert 0 == 2
    """
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = _complete(tree_of(git_repo))
    layers["L2.5"]["status"] = "n/a"
    _write(git_repo, _run(git_repo, layers, fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole3_an_edit_after_approval_makes_the_verdicts_stale(monkeypatch, git_repo):
    """Baseline: presence is all it checks, so the stale APPROVED passes.

    Red at d2317de 2026-10-10: assert 0 == 2
    """
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = _complete(tree_of(git_repo))
    (git_repo / "app.py").write_text("x = 3\n", encoding="utf-8")
    _write(git_repo, _run(git_repo, layers, fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole4_l4_recorded_again_after_l6_invalidates_it(monkeypatch, git_repo):
    """Baseline: order is never read, so it passes.

    Red at d2317de 2026-10-10: assert 0 == 2
    """
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = _complete(tree_of(git_repo))
    layers["L4"] = _d(11)
    _write(git_repo, _run(git_repo, layers, fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole5_a_hunt_without_l0_4_cannot_commit(monkeypatch, git_repo):
    """Baseline: L0.4 is not a layer, so the hunt commits.

    Red at d2317de 2026-10-10: assert 0 == 2
    """
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    _write(git_repo, _run(git_repo, _complete(tree_of(git_repo)), tier="DEEP",
                          mode="HUNT", fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole6_a_tool_layer_recorded_by_hand_is_refused(monkeypatch, git_repo):
    """Baseline: source is ignored, so it passes.

    Red at d2317de 2026-10-10: assert 0 == 2
    """
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    layers = _complete(tree_of(git_repo))
    layers["L5.6"]["source"] = "cli"
    _write(git_repo, _run(git_repo, layers, fixes=1))
    assert _commit_exit(monkeypatch, git_repo) == 2


def test_hole7_close_refuses_an_incomplete_run(git_repo):
    """Baseline: close() logs COMPLETE and returns the log path; DID NOT RAISE.

    Red at d2317de 2026-10-10: Failed: DID NOT RAISE <class 'ValueError'>
    """
    _write(git_repo, _run(git_repo, {"L1": _d(1)}))
    with pytest.raises(ValueError):
        ps.close(root=git_repo)
    assert (git_repo / ".claude" / "audits" / "run-state.json").exists()
