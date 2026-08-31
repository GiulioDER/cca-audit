"""The commit gate must refuse a run that skipped a layer, and only that run.

Two failure directions matter here and they are not symmetric. A gate that lets
an unverified fix through returns the pipeline to the state that made this module
necessary. A gate that blocks an unrelated commit is merely annoying, but it is
the failure that gets a guard uninstalled, after which the first failure is
permanent. So both are tested, and the "allowed" cases are as load-bearing as the
"blocked" ones.
"""

from __future__ import annotations

import json

import pytest

from cca_checks import pipeline_state as ps


def test_no_run_open_allows_any_commit(tmp_path):
    # The common case by far: most commits in most repos have no audit in
    # flight, and the gate must be invisible to them.
    assert ps.load(tmp_path) is None
    decision = ps.verify_commit_allowed(None)
    assert decision.allowed


def test_open_run_blocks_until_core_layers_are_recorded(tmp_path):
    state = ps.start("DEEP", root=tmp_path)
    decision = ps.verify_commit_allowed(state)
    assert not decision.allowed
    # Every core layer is named, not just the first: a gate that reports one
    # missing layer at a time turns a single refusal into four.
    assert {"L1", "L2", "L2.5", "L6"} <= {r.split()[0] for r in decision.reasons}


def test_layers_alone_do_not_pass_without_an_approving_verdict(tmp_path):
    ps.start("FAST", root=tmp_path)
    for layer in ("L1", "L2", "L2.5"):
        ps.record(layer, detail="done", root=tmp_path)
    for verdict in ("REVISE", "BLOCKED"):
        state = ps.record("L6", detail=verdict, root=tmp_path)
        decision = ps.verify_commit_allowed(state)
        assert not decision.allowed, f"{verdict} must not authorise a commit"
        assert verdict in " ".join(decision.reasons)
    state = ps.record("L6", detail="APPROVED", root=tmp_path)
    assert ps.verify_commit_allowed(state).allowed


def test_l6_recorded_without_a_verdict_is_not_a_pass(tmp_path):
    # "The gate ran" and "the gate approved" must never be the same recording.
    ps.start("FAST", root=tmp_path)
    for layer in ("L1", "L2", "L2.5", "L6"):
        ps.record(layer, root=tmp_path)
    decision = ps.verify_commit_allowed(ps.load(tmp_path))
    assert not decision.allowed
    assert any("without a verdict" in r for r in decision.reasons)


@pytest.mark.parametrize(
    "tier,expected_extra",
    [("FAST", {"L5"}), ("STANDARD", {"L5", "L5.5", "L5.6"}), ("DEEP", {"L5", "L5.5", "L5.6"})],
)
def test_fix_layers_are_required_only_once_fixes_exist(tmp_path, tier, expected_extra):
    state = ps.start(tier, root=tmp_path)
    assert set(ps.required_layers(state)) == {"L1", "L2", "L2.5", "L6"}
    state = ps.record("L4", detail="3 fixes", fixes=3, root=tmp_path)
    assert set(ps.required_layers(state)) == {"L1", "L2", "L2.5", "L6"} | expected_extra


def test_fast_tier_is_not_asked_for_gates_it_does_not_run(tmp_path):
    # Step 0.6's tier table gives FAST no regression diff and no red-state proof.
    # Requiring them here would block a correct fast run, which is how a guard
    # earns a reputation for being wrong and gets bypassed.
    ps.start("FAST", root=tmp_path)
    ps.record("L4", fixes=2, root=tmp_path)
    for layer in ("L1", "L2", "L2.5", "L5"):
        ps.record(layer, detail="done", root=tmp_path)
    state = ps.record("L6", detail="APPROVED", root=tmp_path)
    assert ps.verify_commit_allowed(state).allowed


def test_a_skipped_layer_is_refused_as_loudly_as_a_missing_one(tmp_path):
    ps.start("FAST", root=tmp_path)
    for layer in ("L1", "L2"):
        ps.record(layer, root=tmp_path)
    ps.record("L2.5", status="skipped", detail="no findings", root=tmp_path)
    state = ps.record("L6", detail="APPROVED", root=tmp_path)
    decision = ps.verify_commit_allowed(state)
    assert not decision.allowed
    assert any("skipped" in r for r in decision.reasons)


def test_no_fix_run_may_never_commit(tmp_path):
    ps.start("DEEP", no_fix=True, root=tmp_path)
    for layer in ("L1", "L2", "L2.5"):
        ps.record(layer, root=tmp_path)
    state = ps.record("L6", detail="APPROVED", root=tmp_path)
    decision = ps.verify_commit_allowed(state)
    assert not decision.allowed
    assert any("no-fix" in r for r in decision.reasons)


def test_unreadable_state_raises_rather_than_reading_as_no_run(tmp_path):
    # The optimistic reading of a corrupt file is "nothing is in flight", and it
    # is exactly wrong: the file exists because a run started.
    path = ps.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        ps.load(tmp_path)


def test_unknown_tier_and_layer_are_refused(tmp_path):
    with pytest.raises(ValueError):
        ps.start("THOROUGH", root=tmp_path)
    ps.start("DEEP", root=tmp_path)
    with pytest.raises(ValueError):
        ps.record("L2_5", root=tmp_path)


def test_recording_without_an_open_run_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        ps.record("L1", root=tmp_path)


def test_abort_clears_the_block_and_leaves_a_dated_trail(tmp_path):
    ps.start("DEEP", root=tmp_path)
    log = ps.abort("unrelated hotfix", root=tmp_path)
    assert ps.load(tmp_path) is None
    text = log.read_text(encoding="utf-8")
    assert "ABORTED" in text and "unrelated hotfix" in text


def test_abort_works_even_on_an_unparseable_state(tmp_path):
    # An abort that itself needs a well-formed state file would leave a corrupt
    # run permanently blocking every commit in the checkout.
    path = ps.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{{{", encoding="utf-8")
    log = ps.abort("corrupt state", root=tmp_path)
    assert log is not None
    assert not path.exists()


def test_close_records_the_layers_that_ran(tmp_path):
    ps.start("STANDARD", root=tmp_path)
    for layer in ("L1", "L2", "L2.5"):
        ps.record(layer, root=tmp_path)
    ps.record("L6", detail="APPROVED", root=tmp_path)
    log = ps.close(root=tmp_path)
    text = log.read_text(encoding="utf-8")
    assert "COMPLETE" in text and "L2.5" in text
    assert ps.load(tmp_path) is None


def test_state_survives_a_round_trip_through_disk(tmp_path):
    ps.start("DEEP", mode="HUNT", root=tmp_path)
    ps.record("L1", detail="11 auditors", fixes=0, root=tmp_path)
    reloaded = ps.load(tmp_path)
    assert reloaded.mode == "HUNT"
    assert reloaded.layers["L1"]["detail"] == "11 auditors"
    raw = json.loads(ps.state_path(tmp_path).read_text(encoding="utf-8"))
    assert raw["tier"] == "DEEP"
