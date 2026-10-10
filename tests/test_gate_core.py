"""Rules of the CCA gates. Pure functions over state dicts, plus the fingerprint.

Red proofs (the core is a new symbol, so every proof here is a mutation of the
implementation, applied, observed failing in its assertion, then reverted):
recorded per test.
"""

from __future__ import annotations

import pytest
from conftest import load_hook, make_repo, tree_of

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
    """Mutation: return a hash of HEAD only. Fails: fingerprint != tree_of after an edit.

    Red 2026-10-10: write-tree replaced by rev-parse HEAD^{tree}; failed at assert
        core.fingerprint(git_repo) == tree_of(git_repo)
    """
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert core.fingerprint(git_repo) == tree_of(git_repo)


def test_fingerprint_changes_on_edit_and_on_untracked_file(git_repo):
    """Mutation: drop `-A` from git add (tracked only). Fails on the untracked assertion.

    Red 2026-10-10: `git add -A` replaced by `git add -u`; failed at assert len({t0, t1, t2}) ==
        3 (2 == 3)
    """
    t0 = core.fingerprint(git_repo)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    t1 = core.fingerprint(git_repo)
    (git_repo / "test_new.py").write_text("def test(): pass\n", encoding="utf-8")
    t2 = core.fingerprint(git_repo)
    assert len({t0, t1, t2}) == 3


def test_fingerprint_survives_committing_the_same_content(git_repo):
    """Review Focus 2. Mutation: hash `git diff HEAD` instead of write-tree.

    Fails: values differ.

    Red 2026-10-10: write-tree replaced by sha1 of `git diff HEAD`; failed at assert
        core.fingerprint(git_repo) == before
    """
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    before = core.fingerprint(git_repo)
    import subprocess
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "fix"], check=True)
    assert core.fingerprint(git_repo) == before


@pytest.mark.parametrize("gitignore", [".claude/\n", "*.json\n", ".claude/audits/*\n", ""],
                         ids=["claude-dir", "json-glob", "audits-contents", "nothing-ignored"])
def test_fingerprint_ignores_the_run_artifacts_whatever_gitignore_says(tmp_path, gitignore):
    """.claude/audits is excluded from the fingerprint under every ignore rule.

    Writing the run state, the log and the ledger must leave the fingerprint
    unchanged, not None, and equal to the helper's.

    Red at 764525c 2026-10-10 (core and conftest restored from that commit): the
    `*.json` case failed at `assert after is not None and after == before == tree_of(repo)`
    (the state file reads as ignored, the exclude was dropped, and the ledger and log
    entered the tree). The other three cases passed there, so they are controls.
    The `claude-dir` case keeps the receipt of
    test_fingerprint_works_once_an_ignored_claude_dir_exists (red at a281860:
    fingerprint returned None) and `nothing-ignored` that of
    test_fingerprint_ignores_the_run_state_when_claude_is_not_ignored (red: `:(exclude)`
    pathspec removed; fingerprint != before).
    """
    repo = make_repo(tmp_path, gitignore)
    before = core.fingerprint(repo)
    audits = repo / ".claude" / "audits"
    audits.mkdir(parents=True)
    for name in ("run-state.json", "EXECUTION_LOG.md", "AUDITOR_SCORECARD.jsonl"):
        (audits / name).write_text("{}\n", encoding="utf-8")
    after = core.fingerprint(repo)
    assert after is not None and after == before == tree_of(repo)


def test_fingerprint_is_none_outside_a_repository(tmp_path):
    """Mutation: return "" on failure. Fails: "" is not None.

    Red 2026-10-10: except branch returns "" instead of None; failed at assert
        core.fingerprint(tmp_path) is None
    """
    assert core.fingerprint(tmp_path) is None


# --- required layers and fixes owed -------------------------------------------

def test_fixes_are_owed_when_the_tree_changed_without_any_l4_record():
    """THE hole: 0.10.x keyed this on fixes_applied alone. Mutation: return fixes > 0. Fails.

    Red 2026-10-10: fixes_owed reduced to `fixes_applied > 0`; failed at assert
        core.fixes_owed(_state(base="T0"), "T1")
    """
    assert core.fixes_owed(_state(base="T0"), "T1")
    assert not core.fixes_owed(_state(base="T0"), "T0")


def test_fixes_are_owed_when_either_fingerprint_is_unknown():
    """Fail closed. Mutation: treat None as unchanged. Fails on both asserts.

    Red 2026-10-10: None fingerprints treated as unchanged; failed at assert
        core.fixes_owed(_state(base=None), "T0")
    """
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
    """Mutation: drop "L3" from the table. Fails on every row but the no-fix one.

    Red 2026-10-10: `req.append("L3")` replaced by `pass`; failed at assert
        core.required_layers(state, owed) == expected (4 of 5 rows)
    """
    state = _state(tier, mode=mode, no_fix=no_fix)
    assert core.required_layers(state, owed) == expected


# --- outstanding -----------------------------------------------------------------

def test_a_complete_standard_run_has_nothing_outstanding():
    """Control for over-blocking. Mutation: require L0.4 always. Fails: L0.4 reported.

    Red 2026-10-10: `if _hunt(state):` replaced by `if True:` for L0.4; failed at assert
        core.outstanding(_state(layers=_full_standard("T1")), "T1") == []
    """
    assert core.outstanding(_state(layers=_full_standard("T1")), "T1") == []


def test_n_a_does_not_satisfy_a_required_layer():
    """Mutation: accept status in {"done", "n/a"}. Fails: no reason for L2.5.

    Red 2026-10-10: `status != "done"` replaced by `status not in ("done", "n/a")`; failed at
        assert any(r.startswith("L2.5 is recorded as n/a") ...)
    """
    layers = _full_standard("T1")
    layers["L2.5"]["status"] = "n/a"
    assert any(r.startswith("L2.5 is recorded as n/a") for r in
               core.outstanding(_state(layers=layers), "T1"))


def test_a_verdict_from_before_the_latest_edit_is_stale():
    """Mutation: skip the tree comparison. Fails: no 'before the latest edit' reason.

    Red 2026-10-10: tree comparison branch replaced by `if False:`; failed at assert
        {r.split()[0] ...} == {"L5", "L5.5", "L5.6", "L6"}
    """
    reasons = core.outstanding(_state(layers=_full_standard("T1")), "T2")
    assert {r.split()[0] for r in reasons if "latest edit" in r} == {"L5", "L5.5", "L5.6", "L6"}


def test_re_recording_l4_makes_every_later_verdict_older_than_it():
    """Mutation: delete the `for prior in predecessors(...)` loop in outstanding(). Fails.

    Red 2026-10-10: predecessor loop replaced by `for prior in ():`; failed at assert
        any(r.startswith("L5 is older than L4") for r in reasons)
    """
    layers = _full_standard("T1")
    layers["L4"] = _done(11)
    reasons = core.outstanding(_state(layers=layers), "T1")
    assert any(r.startswith("L5 is older than L4") for r in reasons)


def test_tool_layers_recorded_by_hand_are_refused():
    """Mutation: drop the source check. Fails: no 'must be recorded by its tool' reason.

    Red 2026-10-10: source check replaced by `if False:`; failed at assert any("L2.6 must be
        recorded by its tool" in r ...)
    """
    layers = _full_standard("T1")
    layers["L2.6"]["source"] = "cli"
    assert any("L2.6 must be recorded by its tool" in r
               for r in core.outstanding(_state(layers=layers), "T1"))


def test_hunt_needs_l0_4_pass():
    """Mutation: drop the PASS check. Fails: REJECT accepted.

    Red 2026-10-10: PASS check condition prefixed with `False and`; failed at assert any("L0.4
        did not read PASS" in r for r in reasons)
    """
    layers = {"L0.4": _done(1, "REJECT"), "L1": _done(2), "L2": _done(3), "L2.5": _done(4),
              "L2.6": _done(5, source="tool"), "L3": _done(6), "L6": _done(7, "APPROVED")}
    reasons = core.outstanding(_state("DEEP", mode="HUNT", layers=layers), "T0")
    assert any("L0.4 did not read PASS" in r for r in reasons)


def test_l6_must_approve_for_completeness():
    """Mutation: drop the verdict check from outstanding. Fails: REVISE accepted.

    Red 2026-10-10: verdict check in outstanding() replaced by `elif False:`; failed at assert
        any("returned REVISE" in r ...)
    """
    layers = _full_standard("T1")
    layers["L6"]["detail"] = "REVISE"
    assert any("returned REVISE" in r for r in core.outstanding(_state(layers=layers), "T1"))


def test_a_no_fix_run_is_complete_after_verification():
    """Mutation: require L3 for no-fix runs too. Fails: L3 reported.

    Red 2026-10-10: `if not state.get("no_fix"):` replaced by `if True:` in required_layers;
        failed at assert core.outstanding(_state("DEEP", no_fix=True, layers=layers), "T0") ==
        []
    """
    layers = {"L1": _done(1), "L2": _done(2), "L2.5": _done(3), "L2.6": _done(4, source="tool")}
    assert core.outstanding(_state("DEEP", no_fix=True, layers=layers), "T0") == []


def test_a_no_fix_run_that_changed_the_tree_is_not_complete():
    """Mutation: drop the no-fix tree comparison. Fails: empty list.

    Red 2026-10-10: no-fix tree comparison replaced by `if False:`; failed at assert
        core.outstanding(_state("DEEP", no_fix=True, layers=layers), "T9")
    """
    layers = {"L1": _done(1), "L2": _done(2), "L2.5": _done(3), "L2.6": _done(4, source="tool")}
    assert core.outstanding(_state("DEEP", no_fix=True, layers=layers), "T9")


# --- commit_reasons and legacy -------------------------------------------------

def test_no_fix_run_may_never_commit_even_when_complete():
    """
    Red 2026-10-10: commit_reasons: `reasons = [_NO_FIX_REASON] if ... else []` replaced by
        `reasons = []`; failed at assert any("no-fix" in r for r in reasons)
    """
    layers = {"L1": _done(1), "L2": _done(2), "L2.5": _done(3), "L2.6": _done(4, source="tool")}
    reasons = core.commit_reasons(_state("DEEP", no_fix=True, layers=layers), "T0")
    assert any("no-fix" in r for r in reasons)


def test_legacy_state_is_judged_by_the_0_10_rules():
    """A 0.10.x run (no schema) with only the old four layers may still commit.

    Mutation: route legacy states to `outstanding`. Fails: L3 reported missing.

    Red 2026-10-10: `if is_legacy(state):` replaced by `if False:` in commit_reasons; failed at
        assert core.commit_reasons(legacy, None) == []
    """
    legacy = {"run_id": "t", "tier": "FAST", "mode": "DIFF", "no_fix": False,
              "fixes_applied": 0,
              "layers": {k: {"status": "done", "detail": v} for k, v in
                         {"L1": "d", "L2": "d", "L2.5": "d", "L6": "APPROVED"}.items()}}
    assert core.is_legacy(legacy)
    assert core.commit_reasons(legacy, None) == []


# --- fingerprint hardening (task 4 rulings A to C) ------------------------------

def _spy_on_run(monkeypatch):
    """Record the `timeout` of each subprocess.run the fingerprint makes."""
    seen: list = []
    real = core.subprocess.run

    def spy(*args, **kwargs):
        seen.append(kwargs.get("timeout"))
        return real(*args, **kwargs)

    monkeypatch.setattr(core.subprocess, "run", spy)
    return seen


def test_every_git_call_gets_what_is_left_of_one_fingerprint_deadline(monkeypatch, git_repo):
    """FINGERPRINT_TIMEOUT_S bounds the WHOLE fingerprint, not each git call.

    A per call bound allows several times the constant, past the hook timeout, and a hook
    that times out fails open. A fake clock advances one second per reading, so the
    remaining budget handed to successive calls must shrink strictly.

    Red 2026-10-10: baseline 025e4ff (per call timeout=FINGERPRINT_TIMEOUT_S); failed at
        assert all(a > b for a, b in zip(seen, seen[1:], strict=False))
    """
    seen = _spy_on_run(monkeypatch)
    ticks = iter(range(1000, 2000))
    monkeypatch.setattr(
        core, "time", type("Clock", (), {"monotonic": staticmethod(lambda: next(ticks))}),
        raising=False,
    )
    assert core.fingerprint(git_repo) is not None
    assert len(seen) >= 4
    assert all(t is not None and t <= core.FINGERPRINT_TIMEOUT_S for t in seen)
    assert all(a > b for a, b in zip(seen, seen[1:], strict=False)), seen


def test_the_fingerprint_gives_up_once_its_deadline_has_passed(monkeypatch, git_repo):
    """After the first git call the clock jumps past the deadline: no tree, not a late one.

    Red 2026-10-10: baseline 025e4ff (no deadline); failed at
        assert core.fingerprint(git_repo) is None   (a tree id was returned)
    """
    seen = _spy_on_run(monkeypatch)
    clock = type("Clock", (), {"monotonic": staticmethod(lambda: 0 if not seen else 10_000)})
    monkeypatch.setattr(core, "time", clock, raising=False)
    assert core.fingerprint(git_repo) is None
    assert len(seen) == 1


def test_a_git_planted_inside_a_subdirectory_of_the_audited_tree_is_refused(
    monkeypatch, git_repo, tmp_path_factory
):
    """On Windows a `git.exe` in the hook's cwd can be found first, and cwd may be a subdir.

    Red 2026-10-10: baseline 025e4ff (refuses only a git whose directory equals root); failed at
        assert core._git_exe(str(git_repo)) is None
    """
    planted = git_repo / "tools"
    planted.mkdir()
    monkeypatch.setattr(core.shutil, "which", lambda *a, **k: str(planted / "git.exe"))
    assert core._git_exe(str(git_repo)) is None


def test_a_relative_git_path_is_refused(monkeypatch, git_repo):
    """A relative result from which() resolves against the cwd, which is the audited tree.

    Red 2026-10-10: baseline 025e4ff (abspath applied to it); failed at
        assert core._git_exe(str(git_repo)) is None
    """
    monkeypatch.setattr(core.shutil, "which", lambda *a, **k: "git.exe")
    assert core._git_exe(str(git_repo)) is None


def test_a_git_outside_the_audited_tree_is_accepted(monkeypatch, git_repo, tmp_path_factory):
    """Control for the two refusals above: over-refusing would disable every gate."""
    outside = tmp_path_factory.mktemp("bin") / "git.exe"
    monkeypatch.setattr(core.shutil, "which", lambda *a, **k: str(outside))
    assert core._git_exe(str(git_repo)) == str(outside)


def test_a_run_in_a_subdirectory_still_fingerprints_the_whole_repository(git_repo):
    """An edit outside the run's directory must change the fingerprint.

    Red 2026-10-10: baseline 025e4ff (`git add -A -- .` from the run root); failed at
        assert core.fingerprint(sub) != before
    """
    sub = git_repo / "sub"
    sub.mkdir()
    (sub / "inner.py").write_text("y = 1\n", encoding="utf-8")
    before = core.fingerprint(sub)
    assert before == tree_of(sub)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    assert core.fingerprint(sub) != before
    assert core.fingerprint(sub) == tree_of(sub) == tree_of(git_repo)


@pytest.mark.parametrize("gitignore", ["", ".claude/\n"])
def test_a_subdirectory_runs_artifacts_do_not_change_the_fingerprint(tmp_path, gitignore):
    """Control: the run's own `.claude/audits` under the subdirectory is excluded.

    Not a red proof (the baseline also excludes it); it guards the new relative path.
    """
    repo = make_repo(tmp_path, gitignore)
    sub = repo / "sub"
    sub.mkdir()
    (sub / "inner.py").write_text("y = 1\n", encoding="utf-8")
    before = core.fingerprint(sub)
    audits = sub / ".claude" / "audits"
    audits.mkdir(parents=True)
    for name in ("run-state.json", "EXECUTION_LOG.md"):
        (audits / name).write_text("{}\n", encoding="utf-8")
    assert core.fingerprint(sub) == before == tree_of(sub)
