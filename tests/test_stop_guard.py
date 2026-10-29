"""The Stop hook: no ending the turn in the middle of a CCA run, three times at most."""

from __future__ import annotations

import io
import json
import os

import pytest
from conftest import (
    findings_output,
    load_hook,
    make_repo,
    plan_output,
    verdicts_output,
    write_output,
)

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
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=plan_output(git_repo), root=git_repo)
    ps.record("L6", detail="BLOCKED", root=git_repo)
    assert _stop(monkeypatch, capsys, git_repo) is None


def test_a_complete_run_that_is_still_open_asks_for_close(monkeypatch, capsys, git_repo):
    """Mutation: the empty-reasons branch returns 0. Fails: None is not a block.

    Red 2026-10-10: `return 0` inserted before the final `_block("... still open ...")`;
    failed at `assert out is not None and "pipeline close" in out["reason"]`.
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0}, output=plan_output(git_repo), root=git_repo)
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


def _session_with_a_worktree_below(tmp_path):
    """A session checkout with a second checkout below it, as `.claude/worktrees/<x>`."""
    session = tmp_path / "main"
    session.mkdir()
    make_repo(session, ".claude/\n")
    child = session / ".claude" / "worktrees" / "x"
    child.mkdir(parents=True)
    make_repo(child, ".claude/\n")
    return session, child


def test_a_run_in_a_worktree_below_the_session_blocks_and_names_it(monkeypatch, capsys, tmp_path):
    """The live test's defect: a run opened in a worktree below the session was invisible.

    Invariant: with the payload `cwd` at the session checkout and an incomplete run open in
    a checkout below it, the Stop hook blocks, names that run's root, and spends its refusal.
    Red 2026-10-11: baseline 0ec8c47 (the hook looked only from `cwd`); failed at
    `assert out is not None and out["decision"] == "block"`.
    """
    session, child = _session_with_a_worktree_below(tmp_path)
    ps.start("FAST", root=child)
    ps.record("L1", output=write_output(child), root=child)
    out = _stop(monkeypatch, capsys, session)
    assert out is not None and out["decision"] == "block"
    assert os.path.realpath(child) in out["reason"] and "L2" in out["reason"]
    assert ps.load(child).stop_refusals == 1


def test_a_registered_run_outside_the_session_does_not_block(monkeypatch, capsys, tmp_path):
    """A run in a sibling checkout belongs to another session.

    Red 2026-10-11: mutation the `if core._inside(str(r), str(cwd))` filter removed from
    `_decide` (every registered run is a candidate); failed at `assert _stop(...) is None`.
    """
    here = tmp_path / "here"
    there = tmp_path / "there"
    here.mkdir()
    there.mkdir()
    make_repo(here, ".claude/\n")
    make_repo(there, ".claude/\n")
    ps.start("FAST", root=there)
    assert _stop(monkeypatch, capsys, here) is None
    assert ps.load(there).stop_refusals == 0


def test_a_registry_entry_whose_state_is_gone_is_ignored(monkeypatch, capsys, tmp_path):
    """An entry left behind (a crash between `unlink` and `unregister`) never blocks.

    Two layers implement this: `registered_roots` drops the entry, and `_judge` reads a
    missing state as no run. Either alone keeps this green, so the red proof disables both.
    Red 2026-10-11: mutation the `if state_file(key).exists()` filter removed from
    `cca_gate_core.registered_roots` AND `_judge`'s `if state is None or ...: return None`
    changed to block with "unreadable" for a missing state; failed at
    `assert _stop(monkeypatch, capsys, session) is None`. Each mutation alone: green.
    """
    session, child = _session_with_a_worktree_below(tmp_path)
    core = stop._load_core()
    core.register(child, "ghost")
    assert _stop(monkeypatch, capsys, session) is None
    assert not (child / ".claude").exists()


def test_another_sessions_run_below_never_blocks_nor_spends(monkeypatch, capsys, tmp_path):
    """A run below the session's directory that another session opened is not this session's.

    Invariant: with the run's `owner_session` set and a payload `session_id` that differs,
    the Stop hook allows the stop and spends none of that run's refusals (its owner keeps
    all three). The live defect, 2026-10-21: a session in the main checkout was held three
    times by a run another session had opened in a worktree below it, and those three stops
    used up the run's refusals.
    Red 2026-10-21: mutation the `owner != session` early return removed from `_judge`;
    failed at `assert _stop(..., session_id="session-B") is None` (a block was printed).
    """
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-A")
    session, child = _session_with_a_worktree_below(tmp_path)
    ps.start("FAST", root=child)
    ps.record("L1", output=write_output(child), root=child)
    for _ in range(4):
        assert _stop(monkeypatch, capsys, session, session_id="session-B") is None
    assert ps.load(child).stop_refusals == 0


def test_the_owning_session_is_still_held(monkeypatch, capsys, tmp_path):
    """Scope control, not a red-proved behaviour test: the owner check must not free the owner.

    Exempt from red proof on purpose: with the owner check deleted the owner is held exactly
    as before, so no plausible mutation of the new branch turns this red; it pins that the
    branch compares identities rather than allowing every session that sends one.
    """
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-A")
    session, child = _session_with_a_worktree_below(tmp_path)
    ps.start("FAST", root=child)
    ps.record("L1", output=write_output(child), root=child)
    out = _stop(monkeypatch, capsys, session, session_id="session-A")
    assert out is not None and out["decision"] == "block" and "L2" in out["reason"]
    assert ps.load(child).stop_refusals == 1


def test_start_records_the_owner_and_resume_hands_the_run_over(monkeypatch, git_repo):
    """`start` stamps `CLAUDE_CODE_SESSION_ID` as `owner_session`; `resume` moves it.

    Red 2026-10-21: mutation `owner_session=os.environ.get(...) or None` replaced by
    `owner_session=None` in `pipeline_state.start`; failed at
    `assert ps.load(git_repo).owner_session == "session-A"`.
    """
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-A")
    ps.start("FAST", root=git_repo)
    assert ps.load(git_repo).owner_session == "session-A"
    ps.pause("machine restart", root=git_repo)
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-B")
    ps.resume(root=git_repo)
    assert ps.load(git_repo).owner_session == "session-B"


def test_the_run_in_the_sessions_own_checkout_holds_it_whatever_the_owner(
    monkeypatch, capsys, git_repo
):
    """The owner exemption covers only runs below `cwd`, never the run where the session works.

    Invariant: a run found by walking up from `cwd` blocks even when its `owner_session`
    names another session, because that owner comes from an environment variable the agent
    controls at `start`; trusting it here would let an agent switch off its own guard.
    Red 2026-10-21: mutation `if below and theirs and ...` changed to `if theirs and ...`
    in `_judge`; failed at `assert out is not None and out["decision"] == "block"`.
    """
    monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "someone-else")
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    out = _stop(monkeypatch, capsys, git_repo, session_id="session-A")
    assert out is not None and out["decision"] == "block" and "L2" in out["reason"]


def test_an_unowned_run_below_falls_back_to_the_worktree_claim(monkeypatch, capsys, tmp_path):
    """A run opened before `owner_session` existed is owned by the session claiming its worktree.

    Invariant: with no `owner_session` in the state and a `claude-session-claim` naming
    another session in the run checkout's git directory, a run found below `cwd` allows the
    stop and spends no refusal. The live case, 2026-10-21: run 20261021T193810Z predates the
    field, and a main-checkout session kept spending its refusals.
    Red 2026-10-21: mutation `owner = claimed_session(root)` replaced by `owner = None` in
    `_judge`; failed at `assert _stop(..., session_id="session-B") is None`.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    session, child = _session_with_a_worktree_below(tmp_path)
    ps.start("FAST", root=child)
    ps.record("L1", output=write_output(child), root=child)
    assert ps.load(child).owner_session is None
    (child / ".git" / "claude-session-claim").write_text(
        "session=session-A\npid=1\n", encoding="utf-8"
    )
    assert _stop(monkeypatch, capsys, session, session_id="session-B") is None
    assert ps.load(child).stop_refusals == 0


# A Codex DIFF run names its core auditors at start (dispatch receipts, #107); one that has only
# started already owes L1, which is all these tests need.
CODEX_FAST = ["security-auditor", "bug-auditor", "code-auditor"]


def test_a_codex_run_below_never_blocks_nor_spends(monkeypatch, capsys, tmp_path):
    """A Codex run below the session's directory is another client's, owner or not.

    Invariant: a run started with `client="codex"` in a worktree below `cwd`, with no
    `owner_session` and no worktree claim, allows the stop and spends none of its refusals.
    The live case, 2026-10-29: Codex's run in cca-pr729-720 (client codex, no owner) held a
    main-checkout Claude session three times.
    Red 2026-10-29: mutation deleting the `if below and state.get("client") == "codex":`
    early return in `_judge`; failed at `assert _stop(..., session_id="session-B") is None`.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    session, child = _session_with_a_worktree_below(tmp_path)
    ps.start("FAST", root=child, client="codex", auditors=CODEX_FAST)
    assert ps.load(child).owner_session is None
    for _ in range(4):
        assert _stop(monkeypatch, capsys, session, session_id="session-B") is None
    assert ps.load(child).stop_refusals == 0


def test_a_codex_run_in_the_sessions_own_checkout_still_holds_it(monkeypatch, capsys, git_repo):
    """The Codex exemption covers only runs below `cwd`: `client` is set by the agent at
    `start`, like the owner, so trusting it in the session's own checkout would let an agent
    switch off its own guard.

    Red 2026-10-29: mutation `if below and state.get("client") == "codex":` changed to
    `if state.get("client") == "codex":`; failed at `assert out is not None`.
    """
    monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    ps.start("FAST", root=git_repo, client="codex", auditors=CODEX_FAST)
    out = _stop(monkeypatch, capsys, git_repo, session_id="session-A")
    assert out is not None and out["decision"] == "block" and "L1" in out["reason"]


@pytest.mark.parametrize("how", ["owner_session", "worktree_claim"])
def test_a_claude_session_cannot_free_its_own_run_by_calling_it_codex(monkeypatch, capsys,
                                                                     tmp_path, how):
    """`client` is agent-controlled: a run below `cwd` recorded as `client: codex` but owned by
    this session (its `owner_session`, or the worktree claim) still blocks.

    Red 2026-10-29, security review of #114: mutation dropping `and not
    state.get("owner_session") and not mine` from the Codex early return in `_judge`; failed
    at `assert out is not None` for both parametrizations.
    """
    if how == "owner_session":
        monkeypatch.setenv("CLAUDE_CODE_SESSION_ID", "session-A")
    else:
        monkeypatch.delenv("CLAUDE_CODE_SESSION_ID", raising=False)
    session, child = _session_with_a_worktree_below(tmp_path)
    ps.start("FAST", root=child, client="codex", auditors=CODEX_FAST)
    if how == "worktree_claim":
        (child / ".git" / "claude-session-claim").write_text(
            "session=session-A\npid=1\n", encoding="utf-8")
    out = _stop(monkeypatch, capsys, session, session_id="session-A")
    assert out is not None and out["decision"] == "block", out
