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
import subprocess

import pytest

from cca_checks import pipeline_state as ps


def _ledger(repo, run_id, n, *, outcome_rows=0):
    path = repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"run_id": run_id, "auditor": "cca-bug-auditor",
                                 "category": "x", "verdict": "CONFIRMED",
                                 "fix_id": f"FIX-{i}"}) + "\n")
        for i in range(outcome_rows):
            fh.write(json.dumps({"run_id": run_id, "fix_id": f"FIX-{i}",
                                 "result": "fixed", "architect_verdict": "APPROVED"}) + "\n")


def _through_l3(repo, tier, *, confirmed=1, no_fix=False):
    state = ps.start(tier, no_fix=no_fix, root=repo)
    ps.record("L1", detail="6 auditors", root=repo)
    ps.record("L2", counts={"unique": confirmed}, root=repo)
    if tier != "FAST":
        _ledger(repo, state.run_id, confirmed)
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": 0,
                              "duplicate": 0}, root=repo)
    if tier != "FAST":
        ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    if not no_fix:
        ps.record("L3", counts={"to_fix": confirmed}, root=repo)
    return state


def _fix_through_l6(repo, tier, verdict="APPROVED"):
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, root=repo)
    ps.record("L5", detail="green", root=repo)
    if tier != "FAST":
        ps.record("L5.5", detail="all SAFE", root=repo)
        ps.record("L5.6", detail="1 RED", source="tool", root=repo)
    return ps.record("L6", detail=verdict, root=repo)


def test_no_run_open_allows_any_commit(git_repo):
    assert ps.load(git_repo) is None
    assert ps.verify_commit_allowed(None).allowed


def test_start_records_schema_and_the_base_tree(git_repo):
    state = ps.start("DEEP", root=git_repo)
    raw = json.loads(ps.state_path(git_repo).read_text(encoding="utf-8"))
    assert raw["schema"] == 2 and raw["base_tree"] and raw["seq"] == 0
    assert state.base_tree == raw["base_tree"]


@pytest.mark.parametrize("tier", ["FAST", "STANDARD", "DEEP"])
def test_a_full_run_with_fixes_may_commit_and_close(git_repo, tier):
    _through_l3(git_repo, tier)
    state = _fix_through_l6(git_repo, tier)
    assert ps.verify_commit_allowed(state, root=git_repo).allowed
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "fix"], check=True)
    assert ps.close(root=git_repo) is not None


def test_close_after_the_commit_succeeds(git_repo):
    """Review Focus 2. Mutation: fingerprint from `git diff HEAD`. Fails: close refuses.

    Red 2026-10-10: fingerprint returns `git diff HEAD --stat`; failed at
    `assert ps.close(root=git_repo) is not None` (RecordRefused: layers predate the edit).
    """
    _through_l3(git_repo, "STANDARD")
    _fix_through_l6(git_repo, "STANDARD")
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "fix"], check=True)
    assert ps.close(root=git_repo) is not None
    assert ps.load(git_repo) is None


def test_a_zero_fix_run_needs_l3_and_l6_but_not_the_fix_layers(git_repo):
    _through_l3(git_repo, "STANDARD", confirmed=0)
    state = ps.record("L6", detail="APPROVED", root=git_repo)
    assert set(ps.required_layers(state, root=git_repo)) == {"L1", "L2", "L2.5", "L2.6",
                                                            "L3", "L6"}
    assert ps.close(root=git_repo) is not None


def test_a_no_fix_run_can_close_after_verification(git_repo):
    """Mutation: require L3 for no-fix in the core. Fails: close raises.

    Red 2026-10-10: core.required_layers always appends L3; failed at
    `assert ps.close(root=git_repo) is not None` (RecordRefused: L3 has no verdict).
    """
    _through_l3(git_repo, "DEEP", no_fix=True)
    assert ps.close(root=git_repo) is not None


def test_l6_before_its_predecessors_is_refused_at_record_time(git_repo):
    """Baseline d2317de: record accepts any order. Here: RecordRefused naming L3.

    Red 2026-10-10: `if missing:` replaced by `if False:` in record(); failed at
    pytest.raises (DID NOT RAISE RecordRefused). The baseline cannot prove it: the
    symbol RecordRefused does not exist there.
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="L3"):
        ps.record("L6", detail="APPROVED", root=git_repo)


def test_tool_layers_cannot_be_recorded_from_the_cli_source(git_repo):
    _through_l3(git_repo, "STANDARD")
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, root=git_repo)
    ps.record("L5", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="cca_tautology_check"):
        ps.record("L5.6", detail="1 RED", root=git_repo)


def test_l2_5_counts_must_account_for_every_unique_finding(git_repo):
    """Mutation: skip the sum comparison. Fails: no RecordRefused.

    Red 2026-10-10: `if unique is not None and total != unique` replaced by `if False`;
    failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 3}, root=git_repo)
    with pytest.raises(ps.RecordRefused, match="3 unique"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 1, "uncertain": 0, "duplicate": 0},
                  root=git_repo)


def test_ledger_must_hold_one_row_per_verdict(git_repo):
    """Mutation: skip the ledger comparison. Fails: no RecordRefused.

    Red 2026-10-10: `if rows != total` replaced by `if False`; failed at pytest.raises
    (DID NOT RAISE RecordRefused).
    """
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 2}, root=git_repo)
    _ledger(git_repo, state.run_id, 1)
    with pytest.raises(ps.RecordRefused, match="ledger holds 1"):
        ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
                  root=git_repo)


def test_ledger_count_ignores_outcome_rows(git_repo):
    """Review Focus 4. Mutation: count every row with the run_id. Fails: RecordRefused.

    Red 2026-10-10: `"verdict" in row` dropped from _ledger_rows; failed at the L2.5
    record call (RecordRefused: ledger holds 4 rows, 2 expected).
    """
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 2}, root=git_repo)
    _ledger(git_repo, state.run_id, 2, outcome_rows=2)
    ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)


def test_missing_count_flags_are_refused(git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="--unique"):
        ps.record("L2", root=git_repo)


def test_l3_cannot_plan_more_fixes_than_were_confirmed(git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", root=git_repo)
    ps.record("L2", counts={"unique": 1}, root=git_repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              root=git_repo)
    with pytest.raises(ps.RecordRefused, match="--to-fix"):
        ps.record("L3", counts={"to_fix": 2}, root=git_repo)


def test_a_hunt_that_failed_viability_cannot_start_auditing(git_repo):
    ps.start("DEEP", mode="HUNT", root=git_repo)
    ps.record("L0.4", detail="REJECT", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="PASS"):
        ps.record("L1", root=git_repo)


def test_layers_alone_do_not_pass_without_an_approving_verdict(git_repo):
    _through_l3(git_repo, "FAST")
    for verdict in ("REVISE", "BLOCKED"):
        state = _fix_through_l6(git_repo, "FAST", verdict)
        decision = ps.verify_commit_allowed(state, root=git_repo)
        assert not decision.allowed and verdict in " ".join(decision.reasons)


def test_no_fix_run_may_never_commit(git_repo):
    state = _through_l3(git_repo, "DEEP", no_fix=True)
    decision = ps.verify_commit_allowed(ps.load(git_repo), root=git_repo)
    assert not decision.allowed and any("no-fix" in r for r in decision.reasons)
    assert state.no_fix


def test_unreadable_state_raises_rather_than_reading_as_no_run(tmp_path):
    path = ps.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        ps.load(tmp_path)


def test_a_second_start_is_refused_and_the_first_run_survives(git_repo):
    """start() must not overwrite an open run, readable or not.

    Invariant: with a state file present, start() raises RecordRefused naming `pipeline
    abort`, and the open run's layers, base_tree, mode and stop_refusals are untouched.
    A silent overwrite would re-baseline base_tree over edits (a run with no fixes then
    owes nothing past L3), drop L0.4, and leave no trail.

    Red 2026-10-10: baseline a8d7445 (second start succeeds); failed at
    `pytest.raises(ps.RecordRefused)` (DID NOT RAISE).
    """
    first = ps.start("DEEP", mode="HUNT", root=git_repo)
    ps.record("L0.4", root=git_repo)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="pipeline abort"):
        ps.start("FAST", root=git_repo)
    kept = ps.load(git_repo)
    assert kept.run_id == first.run_id and kept.mode == "HUNT" and kept.tier == "DEEP"
    assert kept.base_tree == first.base_tree and "L0.4" in kept.layers

    path = ps.state_path(git_repo)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="pipeline abort"):
        ps.start("FAST", root=git_repo)
    assert path.read_text(encoding="utf-8") == "{not json"


def test_unknown_tier_and_layer_are_refused(git_repo):
    with pytest.raises(ValueError):
        ps.start("THOROUGH", root=git_repo)
    ps.start("DEEP", root=git_repo)
    with pytest.raises(ValueError):
        ps.record("L2_5", root=git_repo)


def test_recording_without_an_open_run_is_an_error(tmp_path):
    with pytest.raises(ValueError):
        ps.record("L1", root=tmp_path)


def test_abort_clears_the_block_and_leaves_a_dated_trail(git_repo):
    ps.start("DEEP", root=git_repo)
    log = ps.abort("unrelated hotfix", root=git_repo)
    assert ps.load(git_repo) is None
    text = log.read_text(encoding="utf-8")
    assert "ABORTED" in text and "unrelated hotfix" in text


def test_abort_works_even_on_an_unparseable_state(tmp_path):
    path = ps.state_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{{{", encoding="utf-8")
    assert ps.abort("corrupt state", root=tmp_path) is not None
    assert not path.exists()


def test_a_legacy_state_round_trips_without_gaining_a_schema(git_repo):
    """A 0.10.x run must stay legacy through record(). Mutation: default schema to 2. Fails.

    Red 2026-10-10: from_json defaults schema to 2; failed at
    `assert raw.get("schema", 1) != 2` (assert 2 != 2).
    """
    path = ps.state_path(git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "old", "tier": "FAST", "mode": "DIFF",
                                "no_fix": False, "fixes_applied": 0, "layers": {}}),
                    encoding="utf-8")
    ps.record("L1", root=git_repo)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw.get("schema", 1) != 2
