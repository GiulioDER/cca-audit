"""The Stop hook: no ending the turn in the middle of a CCA run, three times at most."""

from __future__ import annotations

import io
import json

from conftest import load_hook, write_output

from cca_checks import pipeline_state as ps

stop = load_hook("cca_stop_guard.py")


def _stop(monkeypatch, capsys, repo, **payload) -> dict | None:
    body = {"hook_event_name": "Stop", "cwd": str(repo), "stop_hook_active": False, **payload}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(body)))
    assert stop.main() == 0
    out = capsys.readouterr().out.strip()
    return json.loads(out) if out else None


def test_no_open_run_never_blocks(monkeypatch, capsys, git_repo):
    """Mutation: the `root is None` branch returns `_block("x")`. Fails: a block is printed.

    Red 2026-10-10: that mutation; failed at `assert _stop(...) is None`.
    """
    assert _stop(monkeypatch, capsys, git_repo) is None


def test_an_incomplete_run_blocks_with_the_next_owed_layer(monkeypatch, capsys, git_repo):
    """Mutation: return 0 before the decision. Fails: None is not a block.

    Red 2026-10-10: `return 0` inserted before `find_root` in `_decide`; failed at
    `assert out is not None and out["decision"] == "block" ...`.
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    out = _stop(monkeypatch, capsys, git_repo)
    assert out is not None and out["decision"] == "block" and "L2" in out["reason"]


def test_three_refusals_then_it_lets_go_and_logs_once(monkeypatch, capsys, git_repo):
    """Mutation: cap = 0. Fails: first stop is not blocked.

    Red 2026-10-10: `refusals >= core.STOP_REFUSAL_CAP` mutated to `refusals >= 0`; failed at
    `assert [b is not None ...] == [True, True, True, False, False]`.
    """
    ps.start("FAST", root=git_repo)
    blocks = [_stop(monkeypatch, capsys, git_repo) for _ in range(5)]
    assert [b is not None for b in blocks] == [True, True, True, False, False]
    log = (git_repo / ".claude" / "audits" / "EXECUTION_LOG.md").read_text(encoding="utf-8")
    assert log.count("STOPPED_INCOMPLETE") == 1


def test_stop_guard_allows_while_background_tasks_run(monkeypatch, capsys, git_repo):
    """Review Focus 3. Mutation: ignore background_tasks. Fails: blocked, and a refusal is spent.

    Red 2026-10-10: the `payload.get("background_tasks")` early return removed;
    failed at `assert _stop(...) is None`.
    """
    ps.start("FAST", root=git_repo)
    assert _stop(monkeypatch, capsys, git_repo, background_tasks=[{"id": "a1"}]) is None
    assert ps.load(git_repo).stop_refusals == 0


def test_an_architect_block_lets_the_turn_end(monkeypatch, capsys, git_repo):
    """L6 BLOCKED waits for a human. Mutation: drop the BLOCKED branch. Fails: blocked.

    Red 2026-10-10: the `== "BLOCKED"` early return removed; failed at
    `assert _stop(...) is None`.
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=write_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=write_output(git_repo), root=git_repo)
    ps.record("L6", detail="BLOCKED", root=git_repo)
    assert _stop(monkeypatch, capsys, git_repo) is None


def test_a_complete_run_that_is_still_open_asks_for_close(monkeypatch, capsys, git_repo):
    """Mutation: the empty-reasons branch returns 0. Fails: None is not a block.

    Red 2026-10-10: `return 0` inserted before the final `_block("... still open ...")`;
    failed at `assert out is not None and "pipeline close" in out["reason"]`.
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=write_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=write_output(git_repo), root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    out = _stop(monkeypatch, capsys, git_repo)
    assert out is not None and "pipeline close" in out["reason"]


def test_a_legacy_run_is_left_alone(monkeypatch, capsys, git_repo):
    """Mutation: drop `or core.is_legacy(state)`. Fails: a legacy run is judged and blocked.

    Red 2026-10-10: that mutation; failed at `assert _stop(...) is None`.
    """
    path = ps.state_path(git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "old", "tier": "FAST", "layers": {}}), encoding="utf-8")
    assert _stop(monkeypatch, capsys, git_repo) is None


def test_any_failure_inside_the_guard_allows_the_stop(monkeypatch, capsys, git_repo):
    """A stop guard must never trap a session over its own bug (the opposite of the commit guard).

    Mutation: the broad `except Exception` in `main` returns
    `_block("internal error")` instead of 0. Fails: a block is printed.

    Red 2026-10-10: `main`'s `except Exception` changed to `return _block("internal
    error")`; failed at `assert out is None` (a block was printed).
    """
    ps.start("FAST", root=git_repo)
    real_core = stop._load_core()

    def broken_outstanding(state, tree):
        raise RuntimeError("the rules module has a bug")

    monkeypatch.setattr(real_core, "outstanding", broken_outstanding)
    monkeypatch.setattr(stop, "_load_core", lambda: real_core)
    out = _stop(monkeypatch, capsys, git_repo)
    assert out is None
    assert ps.load(git_repo).stop_refusals == 0


def test_a_core_that_cannot_load_allows_the_stop(monkeypatch, capsys, git_repo):
    """Mutation: `_load_core` failure returns a block. Fails: a block is printed.

    Red 2026-10-10: the `except Exception` around `_load_core()` changed to
    `return _block("no core")`; failed at `assert _stop(...) is None`.
    """
    ps.start("FAST", root=git_repo)

    def no_core():
        raise PermissionError("cannot read the rules")

    monkeypatch.setattr(stop, "_load_core", no_core)
    assert _stop(monkeypatch, capsys, git_repo) is None


def test_an_unreadable_state_blocks_once_then_lets_go(monkeypatch, capsys, git_repo):
    """A corrupt run-state.json costs one continuation, never a trapped session.

    Mutation: drop the `stop_hook_active` early return in the ValueError branch
    (the 2dd581f code). Fails: the second call still blocks.

    Red 2026-10-10: that mutation; failed at `assert second is None`.
    """
    path = ps.state_path(git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    first = _stop(monkeypatch, capsys, git_repo)
    assert first is not None and first["decision"] == "block"
    assert "pipeline abort" in first["reason"]
    second = _stop(monkeypatch, capsys, git_repo, stop_hook_active=True)
    assert second is None


def test_a_paused_run_lets_the_turn_end_without_spending_a_refusal(monkeypatch, capsys, git_repo):
    """A paused run allows the stop silently, before the refusal counter is touched.

    Red 2026-10-11: mutation the `if state.get("paused"): return 0` branch removed from
    `_decide`; failed at `assert _stop(monkeypatch, capsys, git_repo) is None` (a block was
    printed and a refusal spent).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.pause("machine restart", root=git_repo)
    for _ in range(4):
        assert _stop(monkeypatch, capsys, git_repo) is None
    state = ps.load(git_repo)
    assert state.stop_refusals == 0 and not state.stop_logged
