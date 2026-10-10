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


def test_fingerprint_ignores_the_run_state_when_claude_is_not_ignored(git_repo_tracking_claude):
    """Review Focus 1. Mutation: drop the `:(exclude)` pathspec. Fails: values differ.

    Red 2026-10-10: `:(exclude)` pathspec removed from git add; failed at assert
        core.fingerprint(repo) == before
    """
    repo = git_repo_tracking_claude
    before = core.fingerprint(repo)
    state = repo / ".claude" / "audits" / "run-state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    assert core.fingerprint(repo) == before


def test_fingerprint_works_once_an_ignored_claude_dir_exists(git_repo):
    """Run state written into an ignored .claude/ must not break the fingerprint.

    Red at a281860 2026-10-10 (core restored from that commit): fingerprint returned
    None; failed at `assert after is not None and after == before == tree_of(git_repo)`
    (assert None is not None). The exclude pathspec was always passed, and git exits 1
    when it names an ignored path.
    """
    before = core.fingerprint(git_repo)
    state = git_repo / ".claude" / "audits" / "run-state.json"
    state.parent.mkdir(parents=True)
    state.write_text("{}", encoding="utf-8")
    after = core.fingerprint(git_repo)
    assert after is not None and after == before == tree_of(git_repo)


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
