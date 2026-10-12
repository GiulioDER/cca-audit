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
import pathlib
import subprocess

import pytest
from conftest import (
    append_ledger,
    closed_parent,
    finding_id,
    findings_output,
    plan_output,
    pool_output,
    tree_of,
    verdicts_output,
    write_output,
)

from cca_checks import pipeline_state as ps


def _through_l3(repo, tier, *, confirmed=1, no_fix=False):
    state = ps.start(tier, no_fix=no_fix, root=repo)
    ps.record("L1", detail="6 auditors", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": confirmed}, output=findings_output(repo, confirmed),
              root=repo)
    if tier != "FAST":
        append_ledger(repo, state.run_id, confirmed)
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": 0,
                              "duplicate": 0},
              output=verdicts_output(repo, confirmed=confirmed), root=repo)
    if tier != "FAST":
        ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    if not no_fix:
        ps.record("L3", counts={"to_fix": confirmed},
                  output=plan_output(repo, to_fix=confirmed), root=repo)
    return state


def _fix_through_l6(repo, tier, verdict="APPROVED"):
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(repo), root=repo)
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
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="L3"):
        ps.record("L6", detail="APPROVED", root=git_repo)


def test_tool_layers_cannot_be_recorded_from_the_cli_source(git_repo):
    _through_l3(git_repo, "STANDARD")
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(git_repo), root=git_repo)
    ps.record("L5", root=git_repo)
    # The refusal names the packaged command since 0.11, not the script under .claude/tools/.
    with pytest.raises(ps.RecordRefused, match="cca_checks tautology verify"):
        ps.record("L5.6", detail="1 RED", root=git_repo)


def test_l2_5_counts_must_account_for_every_unique_finding(git_repo):
    """Mutation: skip the sum comparison. Fails: no RecordRefused.

    Red 2026-10-10: `if unique is not None and total != unique` replaced by `if False`;
    failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 3}, output=findings_output(git_repo, 3), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="3 unique"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 1, "uncertain": 0, "duplicate": 0},
                  output=verdicts_output(git_repo, confirmed=1, fp=1), root=git_repo)


def test_ledger_must_hold_one_row_per_verdict(git_repo):
    """Mutation: skip the ledger comparison. Fails: no RecordRefused.

    Red 2026-10-10: `if rows != total` replaced by `if False`; failed at pytest.raises
    (DID NOT RAISE RecordRefused).
    """
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2}, output=findings_output(git_repo, 2), root=git_repo)
    append_ledger(git_repo, state.run_id, 1)
    with pytest.raises(ps.RecordRefused, match="ledger holds 1"):
        ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
                  output=verdicts_output(git_repo, confirmed=2), root=git_repo)


def test_ledger_count_ignores_outcome_rows(git_repo):
    """Review Focus 4. Mutation: count every row with the run_id. Fails: RecordRefused.

    Red 2026-10-10: `"verdict" in row` dropped from _ledger_rows; failed at the L2.5
    record call (RecordRefused: ledger holds 4 rows, 2 expected).
    """
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2}, output=findings_output(git_repo, 2), root=git_repo)
    append_ledger(git_repo, state.run_id, 2, outcome_rows=2)
    ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo, confirmed=2), root=git_repo)


def test_missing_count_flags_are_refused(git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="--unique"):
        ps.record("L2", output=findings_output(git_repo), root=git_repo)


def test_l3_cannot_plan_more_fixes_than_were_confirmed(git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 1}, output=findings_output(git_repo, 1), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo, confirmed=1), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="--to-fix"):
        ps.record("L3", counts={"to_fix": 2}, output=plan_output(git_repo, to_fix=2),
                  root=git_repo)


def test_a_hunt_that_failed_viability_cannot_start_auditing(git_repo):
    ps.start("DEEP", mode="HUNT", root=git_repo)
    ps.record("L0.4", detail="REJECT", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="PASS"):
        ps.record("L1", output=write_output(git_repo), root=git_repo)


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
        ps.record("L1", output=write_output(tmp_path), root=tmp_path)


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
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw.get("schema", 1) != 2


# --- layer outputs on disk ---------------------------------------------------------

def test_record_l2_without_an_output_file_is_refused(git_repo):
    """Invariant: a schema 2 run cannot record L2 without the file that holds its work.

    Red 2026-10-11: mutation `if layer in OUTPUT_LAYERS:` before `_read_output` in record()
    became `if layer in OUTPUT_LAYERS and output is not None:`; failed at
    `pytest.raises(ps.RecordRefused, match="L2 needs --output")` (DID NOT RAISE).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="L2 needs --output"):
        ps.record("L2", counts={"unique": 0}, root=git_repo)
    assert "L2" not in ps.load(git_repo).layers


def test_record_l2_with_an_output_that_is_not_json_is_refused(git_repo):
    """Invalid JSON and a missing file are both refused, naming what the file must hold.

    Red 2026-10-11: mutation `json.loads(data)` removed from `_read_output`; failed at
    `pytest.raises(ps.RecordRefused, match="not readable JSON")` (DID NOT RAISE).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    bad = pathlib.Path(write_output(git_repo))
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="not readable JSON") as info:
        ps.record("L2", counts={"unique": 0}, output=bad, root=git_repo)
    assert "consolidated unique findings" in str(info.value)
    with pytest.raises(ps.RecordRefused, match="does not exist"):
        ps.record("L2", counts={"unique": 0}, output=bad.with_name("absent.json"),
                  root=git_repo)
    assert "L2" not in ps.load(git_repo).layers


def test_record_l2_copies_a_valid_output_into_the_run_and_names_it(git_repo):
    """The file is copied to .claude/audits/runs/<run_id>/L2.json and the entry names it.

    Red 2026-10-11: mutation `entry["output"] = _store_output(...)` replaced by `pass` in
    record(); failed at `assert entry.get("output") == f".claude/audits/runs/{run}/L2.json"`.
    """
    state = ps.start("FAST", run_id="r42", root=git_repo)
    run = state.run_id
    ps.record("L1", output=write_output(git_repo, [[{"id": "BUG-1"}]]), root=git_repo)
    findings = [{"id": "BUG-1", "priority": "P2", "file": "app.py", "line": 1}]
    ps.record("L2", counts={"unique": 1}, output=write_output(git_repo, findings),
              root=git_repo)
    entry = ps.load(git_repo).layers["L2"]
    assert entry.get("output") == f".claude/audits/runs/{run}/L2.json"
    copied = git_repo / entry["output"]
    assert json.loads(copied.read_text(encoding="utf-8")) == findings


def test_a_layer_that_stores_no_output_refuses_one(git_repo):
    """`--output` on L5 is refused rather than silently ignored.

    Red 2026-10-11: mutation (accept and ignore) in record(): `if output is not None and layer
    not in OUTPUT_LAYERS:` became `if False:` and the legacy fallback `if data is None and
    output is not None:` gained `and layer in OUTPUT_LAYERS`; failed at
    `pytest.raises(ps.RecordRefused, match="L5 does not take --output")` (DID NOT RAISE).
    """
    _through_l3(git_repo, "FAST")
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="L5 does not take --output"):
        ps.record("L5", detail="green", output=write_output(git_repo), root=git_repo)
    assert "L5" not in ps.load(git_repo).layers


# --- pause and resume ----------------------------------------------------------------

def test_a_paused_run_refuses_record_close_and_a_second_pause(git_repo):
    """While paused nothing advances the run: record, close and pause are all refused.

    Red 2026-10-11: mutation `if state.paused: raise RecordRefused(_PAUSED)` removed from
    record(); failed at the first `pytest.raises(ps.RecordRefused, match="run is paused")`
    (DID NOT RAISE).
    """
    _through_l3(git_repo, "FAST", confirmed=0)
    ps.pause("machine restart", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="run is paused"):
        ps.record("L6", detail="APPROVED", root=git_repo)
    assert "L6" not in ps.load(git_repo).layers
    with pytest.raises(ps.RecordRefused, match="run is paused"):
        ps.close(root=git_repo)
    with pytest.raises(ps.RecordRefused, match="run is paused"):
        ps.pause("again", root=git_repo)
    assert ps.load(git_repo).paused["reason"] == "machine restart"


def test_close_is_refused_while_paused_even_when_the_run_is_complete(git_repo):
    """A complete run that is paused still cannot close until it is resumed.

    Red 2026-10-11: mutation `if state.paused: raise RecordRefused(_PAUSED)` removed from
    close(); failed at `pytest.raises(ps.RecordRefused, match="run is paused")` (DID NOT
    RAISE: close logged COMPLETE).
    """
    _through_l3(git_repo, "FAST", confirmed=0)
    ps.record("L6", detail="APPROVED", root=git_repo)
    ps.pause("end of day", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="run is paused"):
        ps.close(root=git_repo)
    assert ps.load(git_repo) is not None


def test_pause_needs_an_open_schema_2_run(git_repo):
    """No run and a 0.10.x run are both refused.

    Red 2026-10-11: mutation `if state.legacy:` became `if False:` in pause(); failed at
    `pytest.raises(ps.RecordRefused, match="0.10.x")` (DID NOT RAISE).
    """
    with pytest.raises(ps.RecordRefused, match="no CCA run is open"):
        ps.pause("nothing", root=git_repo)
    path = ps.state_path(git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "old", "tier": "FAST", "layers": {}}),
                    encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="0.10.x"):
        ps.pause("legacy", root=git_repo)
    assert ps.load(git_repo).paused is None


def test_pause_records_when_why_and_the_tree_and_logs_it(git_repo):
    """The pause is in the state (with the fingerprint) and on the log.

    Red 2026-10-11: mutation the `core.append_log(...)` call in pause() removed; failed at
    `assert f"run {state.run_id} | PAUSED | machine restart |" in log`.
    """
    state = ps.start("FAST", root=git_repo)
    ps.pause("machine restart", root=git_repo)
    paused = ps.load(git_repo).paused
    assert paused["reason"] == "machine restart" and paused["at"]
    assert paused["tree"] == tree_of(git_repo)
    log_path = git_repo / ".claude" / "audits" / "EXECUTION_LOG.md"
    log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""
    assert f"run {state.run_id} | PAUSED | machine restart |" in log


def test_resume_without_an_edit_names_the_next_layer_and_the_outputs(git_repo):
    """After L2: tree unchanged, next L2.5 at Step 2.5, outputs for L1 and L2 in order.

    Red 2026-10-11: mutation `"next": nxt` became `"next": None` in resume(); failed at
    `assert result["next"] == "L2.5" and result["heading"] == "Step 2.5"`.
    """
    state = ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.pause("machine restart", root=git_repo)
    result = ps.resume(root=git_repo)
    assert result["resumed"] == state.run_id
    assert result["tree_changed"] is False
    assert result["next"] == "L2.5" and result["heading"] == "Step 2.5"
    run_dir = f".claude/audits/runs/{state.run_id}"
    assert list(result["outputs"].items()) == [("L1", f"{run_dir}/L1.json"),
                                               ("L2", f"{run_dir}/L2.json")]
    assert ps.load(git_repo).paused is None
    log = (git_repo / ".claude" / "audits" / "EXECUTION_LOG.md").read_text(encoding="utf-8")
    assert f"run {state.run_id} | RESUMED | next L2.5 |" in log
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)


def test_resume_after_an_edit_made_while_paused_lists_the_stale_verdicts(git_repo):
    """An edit while paused, after L5 to L6 were recorded: tree changed, verdicts stale.

    Red 2026-10-11: mutation `"tree_changed": tree is None or stored is None or tree !=
    stored` became `"tree_changed": False` in resume(); failed at
    `assert result["tree_changed"] is True`.
    """
    _through_l3(git_repo, "STANDARD")
    _fix_through_l6(git_repo, "STANDARD")
    ps.pause("end of day", root=git_repo)
    (git_repo / "app.py").write_text("x = 3\n", encoding="utf-8")
    result = ps.resume(root=git_repo)
    assert result["tree_changed"] is True
    stale = {r.split()[0] for r in result["outstanding"] if "latest edit" in r}
    assert stale == {"L5", "L5.5", "L5.6", "L6"}
    assert result["next"] == "L5" and result["heading"] == "Step 5"
    assert list(result["outputs"]) == ["L1", "L2", "L2.5", "L3", "L4"]


def test_resume_needs_a_paused_run(git_repo):
    """Resume is refused with no run and on a run that is not paused.

    Red 2026-10-11: mutation (resume made idempotent) in resume(): `if not state.paused:`
    became `if False:` and `state.paused.get("tree")` became `(state.paused or {}).get("tree")`;
    failed at `pytest.raises(ps.RecordRefused, match="not paused")` (DID NOT RAISE).
    """
    with pytest.raises(ps.RecordRefused, match="no CCA run is open"):
        ps.resume(root=git_repo)
    ps.start("FAST", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="not paused"):
        ps.resume(root=git_repo)


def test_abort_works_while_paused(git_repo):
    """abort retires a paused run, pause and all, on the record.

    Red 2026-10-11: mutation `path.unlink()` in abort() became
    `None if state and state.paused else path.unlink()` (a paused run survives abort);
    failed at `assert log is not None and ps.load(git_repo) is None`.
    """
    ps.start("FAST", root=git_repo)
    ps.pause("end of day", root=git_repo)
    log = ps.abort("giving up", root=git_repo)
    assert log is not None and ps.load(git_repo) is None
    assert "ABORTED" in log.read_text(encoding="utf-8")


def test_a_paused_run_may_not_commit_through_pipeline_state(git_repo):
    """verify_commit_allowed reads the core rule, so it agrees with the guard on a pause.

    Red 2026-10-11: mutation `[PAUSED_REASON] if state.get("paused") else []` became `[]`
    in core.commit_reasons; failed at `assert not decision.allowed`.
    """
    _through_l3(git_repo, "FAST", confirmed=0)
    ps.record("L6", detail="APPROVED", root=git_repo)
    assert ps.verify_commit_allowed(ps.load(git_repo), root=git_repo).allowed
    ps.pause("end of day", root=git_repo)
    decision = ps.verify_commit_allowed(ps.load(git_repo), root=git_repo)
    assert not decision.allowed
    assert any("paused" in r for r in decision.reasons)


def test_the_pause_survives_a_round_trip_through_the_state_file(git_repo):
    """`paused` is kept by from_json and to_json, so a reload does not unpause the run.

    Red 2026-10-11: mutation `paused=...` dropped from PipelineState.from_json; failed at
    `assert ps.load(git_repo).paused == paused`.
    """
    ps.start("FAST", root=git_repo)
    paused = ps.pause("end of day", root=git_repo).paused
    assert ps.load(git_repo).paused == paused
    raw = json.loads(ps.state_path(git_repo).read_text(encoding="utf-8"))
    assert raw["paused"] == paused


def test_an_output_outside_claude_audits_is_refused_and_one_inside_is_kept(git_repo):
    """The --output source must lie inside <root>/.claude/audits/, the one place the fingerprint
    ignores; a file anywhere else in the working tree is an edit that makes L4 onward owed.

    Red 2026-10-11: mutation the containment check in `_read_output`
    (`if not core._inside(str(path), str(audits)):`) became `if False:`; failed at
    `pytest.raises(ps.RecordRefused, match="outside .claude/audits/")` (DID NOT RAISE).
    """
    ps.start("FAST", root=git_repo)
    outside = git_repo / "L1.json"
    outside.write_text("[]", encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="outside .claude/audits/") as info:
        ps.record("L1", output=outside, root=git_repo)
    assert ".claude/audits/out/L1.json" in str(info.value)
    assert "L1" not in ps.load(git_repo).layers
    inside = git_repo / ".claude" / "audits" / "out" / "L1.json"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("[]", encoding="utf-8")
    ps.record("L1", output=inside, root=git_repo)
    assert ps.load(git_repo).layers["L1"]["output"].endswith("/L1.json")


# --- fix round 2026-10-11 --------------------------------------------------------------

def _through_l3_fast(repo, to_fix):
    ps.start("FAST", root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 1}, output=findings_output(repo, 1), root=repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo, confirmed=1), root=repo)
    ps.record("L3", counts={"to_fix": to_fix, "deferred": 1 - to_fix},
              output=plan_output(repo, to_fix=to_fix, deferred=1 - to_fix), root=repo)


def test_resume_after_l3_planned_fixes_names_l4_not_l6(git_repo):
    """Paused between L3 and L4, a run that planned fixes resumes at Step 4.

    The gate requires L4 only once the tree changes, so without this the next owed layer
    after L3 was L6 and a resumed run skipped every planned fix.
    Red 2026-10-11: baseline bacf6f0 (next_owed has no planned fix rule); failed at
    `assert result["next"] == "L4" and result["heading"] == "Step 4"` (L6 == L4).
    """
    _through_l3_fast(git_repo, to_fix=1)
    ps.pause("end of day", root=git_repo)
    result = ps.resume(root=git_repo)
    assert result["next"] == "L4" and result["heading"] == "Step 4"


def test_resume_after_l3_planned_no_fixes_names_l6(git_repo):
    """Control: an L3 with nothing to fix goes straight to the architect gate.

    Control, not a red proof: it passes at bacf6f0 and after. It guards the planned fix rule
    against over reaching; checked 2026-10-11 with the mutation `_fixes_planned` returning
    True, which failed at `assert result["next"] == "L6" and ...` (L4 == L6).
    """
    _through_l3_fast(git_repo, to_fix=0)
    ps.pause("end of day", root=git_repo)
    result = ps.resume(root=git_repo)
    assert result["next"] == "L6" and result["heading"] == "Step 6"


def test_a_run_id_with_characters_a_filesystem_forbids_is_refused_at_start(git_repo):
    """The run id names a directory; `:` crashed `record` with WinError 123 at L1.

    Red 2026-10-11: baseline bacf6f0 (start accepts any run id); failed at
    `pytest.raises(ps.RecordRefused, match="letters, digits")` (DID NOT RAISE).
    """
    for bad in ("run:1", "../escape", "a/b", ".."):
        with pytest.raises(ps.RecordRefused, match="letters, digits"):
            ps.start("FAST", run_id=bad, root=git_repo)
        assert ps.load(git_repo) is None
    assert ps.start("FAST", run_id="run-1_a.b", root=git_repo).run_id == "run-1_a.b"


def test_resume_gives_the_stop_hook_its_refusals_back(git_repo):
    """A resumed session is protected by the Stop hook again.

    Red 2026-10-11: baseline bacf6f0 (resume keeps the spent counter); failed at
    `assert state.stop_refusals == 0 and state.stop_logged is False` (3 == 0).
    """
    ps.start("FAST", root=git_repo)
    path = ps.state_path(git_repo)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw.update({"stop_refusals": 3, "stop_logged": True})
    path.write_text(json.dumps(raw), encoding="utf-8")
    ps.pause("end of day", root=git_repo)
    ps.resume(root=git_repo)
    state = ps.load(git_repo)
    assert state.stop_refusals == 0 and state.stop_logged is False


def test_dot_dot_inside_claude_audits_is_accepted_and_escaping_it_is_refused(git_repo):
    """Containment is judged on the resolved path, not on the spelling.

    Red 2026-10-11: mutation the containment test `core._inside(str(path), str(audits))`
    became `str(path).startswith(str(audits))` (a string prefix); failed at
    `pytest.raises(ps.RecordRefused, match="outside .claude/audits/")` (DID NOT RAISE).
    """
    ps.start("FAST", root=git_repo)
    audits = git_repo / ".claude" / "audits"
    (audits / "out").mkdir(parents=True, exist_ok=True)
    (audits / "kept.json").write_text("[]", encoding="utf-8")
    (git_repo / "escaped.json").write_text("[]", encoding="utf-8")
    ps.record("L1", output=audits / "out" / ".." / "kept.json", root=git_repo)
    assert "L1" in ps.load(git_repo).layers
    with pytest.raises(ps.RecordRefused, match="outside .claude/audits/"):
        ps.record("L2", counts={"unique": 0},
                  output=audits / "out" / ".." / ".." / ".." / "escaped.json", root=git_repo)


# --- deferred findings are counted, never given a verdict they did not receive ----------

def _to_l2(repo, tier, unique, run_id="r1", priorities=None):
    state = ps.start(tier, run_id=run_id, root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": unique},
              output=findings_output(repo, unique, priorities=priorities), root=repo)
    return state


def test_deferred_findings_complete_the_l2_5_sum(git_repo):
    """A DEFERRED finding counts toward the L2.5 sum, so L2.5 accounts for every L2 finding.

    Red 2026-10-11: mutation `total = judged + n_deferred` became `total = judged` in
    _check_counts; failed at the second L2.5 record (uncaught RecordRefused: accounts for 1
    findings but L2 recorded 3 unique). (key renamed to unverified at the master merge)
    """
    _to_l2(git_repo, "FAST", 3)
    with pytest.raises(ps.RecordRefused, match="3 unique"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
                  output=verdicts_output(git_repo, confirmed=1), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "unverified": 2},
              output=verdicts_output(git_repo, confirmed=1, unverified=2), root=git_repo)
    assert ps.load(git_repo).layers["L2.5"]["counts"]["unverified"] == 2


def test_a_deferred_finding_gets_no_ledger_row(git_repo):
    """The ledger needs a row per judged finding, never one for a DEFERRED finding.

    Red 2026-10-11: mutation `if rows != judged:` became `if rows != total:` in _check_counts;
    failed at the L2.5 record (uncaught RecordRefused from the ledger check: 1 row against
    the 3 findings of `total`).
    Red 2026-10-11 (closing assertion): mutation `entry["counts"] = counts` became
    `entry["counts"] = {k: v for k, v in counts.items() if k != "deferred"}` in record; failed
    at the closing `assert ... == {..., "deferred": 2}` (the stored counts lack "deferred").
    (key renamed to unverified at the master merge)
    """
    _to_l2(git_repo, "STANDARD", 3, priorities={"F-2": "P3", "F-3": "P3"})
    append_ledger(git_repo, "r1", 1)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "unverified": 2},
              output=verdicts_output(git_repo, confirmed=1, unverified=2), root=git_repo)
    assert ps.load(git_repo).layers["L2.5"]["counts"] == {
        "confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0, "unverified": 2,
    }


def test_a_ledger_row_for_a_deferred_finding_is_refused(git_repo):
    """Three ledger rows for one judged finding and two deferred ones is a surplus, refused.

    Red 2026-10-11: mutation `if rows != judged:` became `if rows != total:` in _check_counts;
    failed at pytest.raises (DID NOT RAISE RecordRefused: 3 rows equal the 3 findings of
    `total`).
    """
    _to_l2(git_repo, "STANDARD", 3, priorities={"F-2": "P3", "F-3": "P3"})
    append_ledger(git_repo, "r1", 3)
    with pytest.raises(ps.RecordRefused, match="ledger holds 3"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "unverified": 2},
                  output=verdicts_output(git_repo, confirmed=1, unverified=2), root=git_repo)


def test_stale_is_refused_outside_a_deferred_run(git_repo):
    """A first run has no earlier run, so it cannot record a STALE verdict.

    Red 2026-10-11: mutation `if stale and state.mode != "DEFERRED":` became `if False:` in
    _check_counts; failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    _to_l2(git_repo, "FAST", 2)
    with pytest.raises(ps.RecordRefused, match="--stale is for a DEFERRED run only"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "stale": 1},
                  output=verdicts_output(git_repo, confirmed=1, stale=1), root=git_repo)


def test_the_l2_file_must_hold_the_unique_findings(git_repo):
    """The L2 file holds exactly as many findings as --unique says.

    Red 2026-10-11: mutation the `if layer == "L2":` branch of _check_shape deleted; failed at
    pytest.raises (DID NOT RAISE RecordRefused).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="holds 1 findings but --unique is 2"):
        ps.record("L2", counts={"unique": 2}, output=findings_output(git_repo, 1),
                  root=git_repo)


def test_the_l2_5_file_must_agree_with_its_counts(git_repo):
    """The L2.5 file's verdicts tally to the recorded counts.

    Red 2026-10-11: mutation the `elif layer == "L2.5":` branch of _check_shape deleted; failed
    at pytest.raises (DID NOT RAISE RecordRefused).
    """
    _to_l2(git_repo, "FAST", 2)
    with pytest.raises(ps.RecordRefused, match="disagrees with its counts"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 1, "uncertain": 0, "duplicate": 0},
                  output=verdicts_output(git_repo, confirmed=2), root=git_repo)


def test_l3_must_plan_or_defer_every_confirmed_finding(git_repo):
    """Every confirmed finding is either planned or deferred, so a short plan is refused.

    Red 2026-10-11: mutation `to_fix + n_deferred != confirmed` became `to_fix > confirmed` in
    _check_counts; failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    _to_l2(git_repo, "FAST", 2)
    ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo, confirmed=2), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="either planned or deferred"):
        ps.record("L3", counts={"to_fix": 1}, output=plan_output(git_repo, to_fix=1),
                  root=git_repo)
    ps.record("L3", counts={"to_fix": 1, "deferred": 1},
              output=plan_output(git_repo, to_fix=1, deferred=1), root=git_repo)


def test_l3_refuses_an_id_l2_5_did_not_confirm(git_repo):
    """The L3 plan names only findings L2.5 confirmed.

    Red 2026-10-11: mutation the `elif layer == "L3":` branch of _check_shape deleted; failed at
    pytest.raises (DID NOT RAISE RecordRefused).
    """
    _to_l2(git_repo, "FAST", 2)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 1, "duplicate": 0},
              output=verdicts_output(git_repo, confirmed=1, uncertain=1), root=git_repo)
    plan = write_output(git_repo, {"to_fix": [finding_id(2)], "deferred": []})
    with pytest.raises(ps.RecordRefused, match="did not confirm"):
        ps.record("L3", counts={"to_fix": 1}, output=plan, root=git_repo)


# --- fix round 1: what a later layer reads back is held to the same shapes --------------

def _bom(path) -> str:
    """Prefix the JSON file at `path` with a UTF-8 BOM, as Windows PowerShell 5.1 writes it."""
    target = pathlib.Path(path)
    target.write_bytes(b"\xef\xbb\xbf" + target.read_bytes())
    return str(target)


def _stored(repo, layer, run_id="r1") -> pathlib.Path:
    return repo / ".claude" / "audits" / "runs" / run_id / f"{layer}.json"


_TWO_CONFIRMED = {"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0}


def test_a_bom_prefixed_work_file_does_not_lock_out_the_next_layer(git_repo):
    """A work file a UTF-8 BOM precedes is stored as written and must stay readable.

    Without this, `record` accepted the file (bytes parse) and the next layer then refused it
    for ever ("record L2 again" loops), because the stored copy was re-read as text.

    Red 2026-10-11: mutation `.read_bytes()` became `.read_text(encoding="utf-8")` in
    _stored_json; failed at the L2.5 record (uncaught RecordRefused: L2's stored file is
    missing or unreadable).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2}, output=_bom(findings_output(git_repo, 2)),
              root=git_repo)
    ps.record("L2.5", counts=_TWO_CONFIRMED,
              output=_bom(verdicts_output(git_repo, confirmed=2)), root=git_repo)
    ps.record("L3", counts={"to_fix": 2}, output=plan_output(git_repo, to_fix=2), root=git_repo)
    assert "L3" in ps.load(git_repo).layers


def test_an_old_shape_stored_l2_is_refused_naming_l2(git_repo):
    """A run whose stored L2 predates the finding shape is refused at L2.5, naming L2.

    Without the check the refusal blamed the L2.5 ids ("which L2 did not record").

    Red 2026-10-11: baseline 6531408 (no stored-L2 validation); failed at pytest.raises
    (regex did not match; the reason read "L2.5 gives a verdict for F-1, which L2 did not
    record"). Mutation `return layer_outputs.l2_findings(l2, len(l2))` became `return l2` in
    _stored_l2 (try block removed); failed at the L2.5 record (uncaught TypeError).
    """
    _to_l2(git_repo, "FAST", 2)
    _stored(git_repo, "L2").write_text('["a", "b"]', encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="L2's stored file does not have the finding"):
        ps.record("L2.5", counts=_TWO_CONFIRMED,
                  output=verdicts_output(git_repo, confirmed=2), root=git_repo)


def test_a_stored_l2_with_a_list_valued_id_is_refused_not_a_crash(git_repo):
    """Unhashable ids in a stored L2 are a refusal, not an escaping TypeError.

    Red 2026-10-11: baseline 6531408 (no stored-L2 validation); failed at the L2.5 record
    (uncaught TypeError: cannot use 'list' as a set element). Mutation `return
    layer_outputs.l2_findings(l2, len(l2))` became `return l2` in _stored_l2; the same
    uncaught TypeError.
    """
    _to_l2(git_repo, "FAST", 2)
    _stored(git_repo, "L2").write_text(
        '[{"id": ["a"], "priority": "P2"}, {"id": ["b"], "priority": "P2"}]', encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="L2's stored file"):
        ps.record("L2.5", counts=_TWO_CONFIRMED,
                  output=verdicts_output(git_repo, confirmed=2), root=git_repo)


def test_an_old_shape_stored_l2_5_is_refused_naming_l2_5(git_repo):
    """A run whose stored L2.5 predates the verdict shape is refused at L3, naming L2.5.

    Without the check the refusal blamed the plan ("which L2.5 did not confirm").

    Red 2026-10-11: mutation `layer_outputs.l25_verdicts(l25, recorded or {}, l2_ids)` became
    the id-to-verdict comprehension over the raw rows in _stored_l25; failed at pytest.raises
    (regex did not match; the reason read "L3 plans F-1, F-2, which L2.5 did not confirm").
    """
    _to_l2(git_repo, "FAST", 2)
    ps.record("L2.5", counts=_TWO_CONFIRMED, output=verdicts_output(git_repo, confirmed=2),
              root=git_repo)
    _stored(git_repo, "L2.5").write_text('["a", "b"]', encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="L2.5's stored file does not have the verdict"):
        ps.record("L3", counts={"to_fix": 2}, output=plan_output(git_repo, to_fix=2),
                  root=git_repo)


def test_a_p1_cannot_be_passed_on_unverified(git_repo):
    """A P1 is never DEFERRED at L2.5: it would skip the fp-check, the ledger and the fix.

    Red 2026-10-11: mutation `allowed = ("P2", "P3") if state.tier == "FAST" else ("P3",)`
    became `allowed = ("P1", "P2", "P3")` in _check_shape; failed at pytest.raises (DID NOT
    RAISE RecordRefused).
    """
    _to_l2(git_repo, "FAST", 2, priorities={"F-2": "P1"})
    with pytest.raises(ps.RecordRefused, match=r"L2\.5 defers F-2 \(P1\)"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "unverified": 1},
                  output=verdicts_output(git_repo, confirmed=1, unverified=1), root=git_repo)


def test_a_p2_cannot_be_passed_on_unverified_on_standard(git_repo):
    """Only P3 findings may be DEFERRED on a tier that verifies its P2s.

    Red 2026-10-11: mutation `allowed = ("P2", "P3") if state.tier == "FAST" else ("P3",)`
    became `allowed = ("P2", "P3")` in _check_shape; failed at pytest.raises (DID NOT RAISE
    RecordRefused).
    """
    _to_l2(git_repo, "STANDARD", 2)
    append_ledger(git_repo, "r1", 1)
    with pytest.raises(ps.RecordRefused, match=r"L2\.5 defers F-2 \(P2\)"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "unverified": 1},
                  output=verdicts_output(git_repo, confirmed=1, unverified=1), root=git_repo)


def test_a_p2_may_be_passed_on_unverified_on_fast(git_repo):
    """FAST has no fp-check for a P2, so a P2 may be DEFERRED there.

    Red 2026-10-11: mutation `allowed = ("P2", "P3") if state.tier == "FAST" else ("P3",)`
    became `allowed = ("P3",)` in _check_shape; failed at the L2.5 record (uncaught
    RecordRefused: L2.5 defers F-2 (P2)).
    """
    _to_l2(git_repo, "FAST", 2)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "unverified": 1},
              output=verdicts_output(git_repo, confirmed=1, unverified=1), root=git_repo)
    assert ps.load(git_repo).layers["L2.5"]["counts"]["unverified"] == 1


def test_l3_defers_a_confirmed_p2_but_never_a_p1(git_repo):
    """A confirmed P1 is always fixed: L3 may defer the P2 beside it, not the P1.

    Red 2026-10-11: mutation `barred = [i for i in plan["deferred"] if priority[i] == "P1"]`
    became `barred = []` in _check_shape; failed at pytest.raises (DID NOT RAISE
    RecordRefused).
    """
    _to_l2(git_repo, "FAST", 2, priorities={"F-1": "P1"})
    ps.record("L2.5", counts=_TWO_CONFIRMED, output=verdicts_output(git_repo, confirmed=2),
              root=git_repo)
    swapped = write_output(git_repo, {"to_fix": [finding_id(2)], "deferred": [finding_id(1)]})
    with pytest.raises(ps.RecordRefused, match=r"L3 defers F-1 \(P1\)"):
        ps.record("L3", counts={"to_fix": 1, "deferred": 1}, output=swapped, root=git_repo)
    ps.record("L3", counts={"to_fix": 1, "deferred": 1},
              output=plan_output(git_repo, to_fix=1, deferred=1), root=git_repo)
    assert "L3" in ps.load(git_repo).layers


# --- the base commit -----------------------------------------------------------------

def _head(repo) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_start_records_the_commit_the_diff_is_taken_against(git_repo):
    """`start` stores the sha of `--base` (HEAD by default) as the run's base_commit.

    Red 2026-10-11: mutation `base_commit=base_commit` became `base_commit=None` in start();
    failed at `assert state.base_commit == _head(git_repo)` (None against the HEAD sha).
    """
    state = ps.start("FAST", root=git_repo)
    assert state.base_commit == _head(git_repo)
    raw = json.loads(ps.state_path(git_repo).read_text(encoding="utf-8"))
    assert raw["base_commit"] == state.base_commit and raw["parent"] is None


def test_start_resolves_a_relative_base(git_repo):
    """A relative `--base` such as HEAD~1 is stored as the full sha it names.

    Red 2026-10-11: mutation `_commit_of(root, base)` became `_commit_of(root, "HEAD")` in
    start(); failed at `assert ps.start(..., base="HEAD~1", ...).base_commit == first` (the
    sha of the second commit against the first).
    """
    first = _head(git_repo)
    (git_repo / "app.py").write_text("x = 5\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "two"], check=True)
    assert ps.start("FAST", base="HEAD~1", root=git_repo).base_commit == first


def test_start_refuses_a_base_that_names_no_commit(git_repo):
    """A `--base` that names no commit is refused, and no run is opened.

    Red 2026-10-11: mutation `if base_commit is None and base != "HEAD":` became `if False:`
    in start(); failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    with pytest.raises(ps.RecordRefused, match="does not name a commit"):
        ps.start("FAST", base="no-such-rev", root=git_repo)
    assert ps.load(git_repo) is None


def test_start_in_a_repository_without_commits_stores_no_base(tmp_path):
    """A repository with no commit has no HEAD to record, which is not a refusal.

    Red 2026-10-11: mutation `if base_commit is None and base != "HEAD":` became
    `if base_commit is None:` in start(); failed at the start call (uncaught RecordRefused:
    --base HEAD does not name a commit).
    """
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert ps.start("FAST", root=tmp_path).base_commit is None


def test_start_refuses_an_unknown_mode(git_repo):
    """A mode outside MODES is refused, not stored for a later layer to misread.

    Red 2026-10-11: mutation `if mode not in MODES:` became `if False:` in start(); failed at
    pytest.raises (DID NOT RAISE ValueError).
    """
    with pytest.raises(ValueError, match="unknown mode"):
        ps.start("FAST", mode="SIDEWAYS", root=git_repo)


def test_the_base_commit_survives_a_record(git_repo):
    """`record` loads the state and writes it back, so from_json must carry base_commit.

    Red 2026-10-11: mutation `base_commit=...` dropped from PipelineState.from_json; failed at
    `assert ps.load(git_repo).base_commit == _head(git_repo)` (None against the HEAD sha, the
    first record having written `base_commit: null`).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    assert ps.load(git_repo).base_commit == _head(git_repo)
    raw = json.loads(ps.state_path(git_repo).read_text(encoding="utf-8"))
    assert raw["base_commit"] == _head(git_repo) and raw["parent"] is None


def test_a_parent_survives_a_record(git_repo):
    """`parent` is carried by from_json too: a record must not drop a parent run it was given.

    `start` stores a parent only for a DEFERRED run, so this DIFF run's state file is given
    one by hand.

    Red 2026-10-11: mutation `parent=...` dropped from PipelineState.from_json; failed at
    `assert ps.load(git_repo).parent == "20261011T000000Z"` (None against the parent id).
    """
    ps.start("FAST", root=git_repo)
    path = ps.state_path(git_repo)
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["parent"] = "20261011T000000Z"
    path.write_text(json.dumps(raw), encoding="utf-8")
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    assert ps.load(git_repo).parent == "20261011T000000Z"
    assert json.loads(path.read_text(encoding="utf-8"))["parent"] == "20261011T000000Z"


def test_a_bom_prefixed_ledger_is_still_counted(git_repo):
    """A ledger Windows PowerShell 5.1 created starts with a BOM; its first row still counts.

    Red 2026-10-11: mutation `read_text(encoding="utf-8-sig")` became
    `read_text(encoding="utf-8")` in _ledger_rows; failed at the L2.5 record (uncaught
    RecordRefused from the ledger check: 0 rows against 1 verdict, 1 malformed line skipped).
    At the master merge the read moved into `_ledger`, which every ledger reader of the gate
    now shares; the same mutation there, run 2026-10-11, fails the same way.
    """
    _to_l2(git_repo, "STANDARD", 1)
    ledger = git_repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    row = {"run_id": "r1", "auditor": "cca-bug-auditor", "category": "x",
           "verdict": "CONFIRMED", "fix_id": "FIX-0"}
    ledger.write_bytes(b"\xef\xbb\xbf" + json.dumps(row).encode("utf-8") + b"\n")
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo, confirmed=1), root=git_repo)
    assert ps.load(git_repo).layers["L2.5"]["counts"]["confirmed"] == 1


# --- the run summary -----------------------------------------------------------------

def _summary_of(repo, run_id):
    return json.loads((repo / ".claude/audits/runs" / run_id / "summary.json")
                      .read_text(encoding="utf-8"))


def test_close_writes_a_summary_whose_pool_is_both_deferral_points(git_repo):
    """Red 2026-10-11: mutation the l25 half deleted in deferred.pool_ids (L3 half only);
    failed at `assert s["pool"] == ["F-3", "F-4", "F-2"]` (got ['F-2']). Also: mutation
    `"commit": _commit_of(root, "HEAD")` became `"commit": state.base_commit` in
    _write_summary; failed at `assert s["base_commit"] == base and s["commit"] == _head(...)`.
    """
    base = _head(git_repo)
    closed_parent(git_repo, p2_deferred=1, p3=2)
    s = _summary_of(git_repo, "parent")
    assert s["pool"] == ["F-3", "F-4", "F-2"]
    assert s["base_commit"] == base and s["commit"] == _head(git_repo)
    assert s["mode"] == "DIFF" and s["parent"] is None and s["schema"] == 1


def test_the_summary_keeps_the_tree_the_auditors_read(git_repo):
    """The summary records `base_tree`, the start fingerprint: the tree the auditors read, before
    the run's own fixes. A deferred pass judges STALE against it, and git can show a file in it.

    Red 2026-10-11, two mutations in _write_summary: the `"base_tree": state.base_tree` entry
    deleted; failed at `assert s.get("base_tree") == audited` (None). It became
    `"base_tree": core.fingerprint(root)`, the tree at close; failed at the same assert (the
    tree after the fix, not the audited one).
    """
    audited = tree_of(git_repo)
    closed_parent(git_repo)
    s = _summary_of(git_repo, "parent")
    assert s.get("base_tree") == audited
    assert s["base_tree"] != tree_of(git_repo) and s["schema"] == 1
    shown = subprocess.run(["git", "-C", str(git_repo), "show", f"{s['base_tree']}:app.py"],
                           capture_output=True, text=True, check=True).stdout
    assert shown == "x = 1\n"


def test_human_pending_counts_uncertain_and_parked_findings(git_repo):
    """Red 2026-10-11: mutation `+ deferred.parked_count(...)` removed in _write_summary;
    failed at `assert _summary_of(git_repo, "parent")["human_pending"] == 2` (1 == 2)."""
    closed_parent(git_repo, human=1, parked=1)
    assert _summary_of(git_repo, "parent")["human_pending"] == 2


def test_abort_writes_no_summary(git_repo):
    """Red 2026-10-11: mutation `_write_summary(state, root)` added to abort(); failed at
    `assert not (git_repo / ".claude/audits/runs/gone/summary.json").exists()`."""
    ps.start("FAST", run_id="gone", root=git_repo)
    ps.abort("test", root=git_repo)
    assert not (git_repo / ".claude/audits/runs/gone/summary.json").exists()


def test_a_no_fix_run_summary_pools_what_l2_5_deferred(git_repo):
    """A run with no L3 (no-fix) still pools what L2.5 deferred.

    Red 2026-10-11: mutation `pool` made `[]` unless "L3" is a recorded layer, in
    _write_summary; failed at `assert s["no_fix"] is True and s["pool"] == ["F-2"]` ([] against
    ['F-2']).
    """
    ps.start("STANDARD", no_fix=True, run_id="nf", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2},
              output=findings_output(git_repo, 2, priorities={"F-2": "P3"}), root=git_repo)
    append_ledger(git_repo, "nf", 1)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "unverified": 1},
              output=verdicts_output(git_repo, confirmed=1, unverified=1), root=git_repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=git_repo)
    ps.close(root=git_repo)
    s = _summary_of(git_repo, "nf")
    assert s["no_fix"] is True and s["pool"] == ["F-2"]


def test_start_refuses_a_run_id_that_names_a_completed_run(git_repo):
    """Review Focus 4. Red 2026-10-11: mutation the summary-exists check deleted in start();
    failed at pytest.raises (DID NOT RAISE RecordRefused)."""
    closed_parent(git_repo)
    with pytest.raises(ps.RecordRefused, match="already names a completed run"):
        ps.start("FAST", run_id="parent", root=git_repo)


def test_closing_a_legacy_run_writes_no_summary(git_repo):
    """A 0.10.x run (no schema 2) closes as before and leaves no summary for a deferred pass.

    Red 2026-10-11: mutation `if not state.legacy:` became `if True:` in close(); failed at
    `assert not summary.exists()`.
    """
    path = ps.state_path(git_repo)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"run_id": "old", "tier": "FAST", "mode": "DIFF",
                                "no_fix": False, "fixes_applied": 0, "layers": {}}),
                    encoding="utf-8")
    assert ps.close(root=git_repo) is not None
    assert not path.exists()
    summary = git_repo / ".claude/audits/runs/old/summary.json"
    assert not summary.exists()


def test_a_summary_that_cannot_be_written_keeps_the_run_open(git_repo):
    """A failed summary write refuses the close, names the path, and leaves the run closable.

    The obstacle is a directory where summary.json must go, so os.replace fails for real
    (PermissionError on Windows, IsADirectoryError on Linux).

    Red 2026-10-11: mutation `raise RecordRefused(...) from exc` replaced by `pass` in the
    OSError handler of _write_summary; failed at `pytest.raises(ps.RecordRefused)` (DID NOT
    RAISE).
    """
    state = _through_l3(git_repo, "DEEP", no_fix=True)
    summary = git_repo / ".claude/audits/runs" / state.run_id / "summary.json"
    summary.mkdir(parents=True)
    with pytest.raises(ps.RecordRefused) as refused:
        ps.close(root=git_repo)
    assert summary.as_posix() in str(refused.value)
    assert ps.load(git_repo) is not None
    summary.rmdir()
    assert ps.close(root=git_repo) is not None
    assert ps.load(git_repo) is None
    assert json.loads(summary.read_text(encoding="utf-8"))["run_id"] == state.run_id


# --- the DEFERRED run ----------------------------------------------------------------

def _write_summary_file(repo, run_id, **over):
    s = {"schema": 1, "run_id": run_id, "tier": "STANDARD", "mode": "DIFF", "no_fix": False,
         "base_commit": None, "commit": None, "parent": None,
         "completed_utc": "2026-10-11T00:00:00Z", "pool": ["F-1"], "human_pending": 0}
    s.update(over)
    path = repo / ".claude/audits/runs" / run_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(s), encoding="utf-8")


def _child_to_l5(repo, parent, *, run_id="child", n=3):
    """A DEFERRED run through L5 that confirms and plans every finding. Its L2 is the parent's
    pool (a run id parent holds it to that), or `n` P3 findings after a `commit:<sha>` parent."""
    ps.start("STANDARD", mode="DEFERRED", parent=parent, run_id=run_id, root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    if parent.startswith("commit:"):
        ids = [finding_id(i) for i in range(1, n + 1)]
        l2 = findings_output(repo, n, priorities=dict.fromkeys(ids, "P3"))
    else:
        l2, ids = pool_output(repo, parent)
    n = len(ids)
    ps.record("L2", counts={"unique": n}, output=l2, root=repo)
    append_ledger(repo, run_id, n)
    ps.record("L2.5", counts={"confirmed": n, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo, confirmed=n, ids=ids), root=repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": n}, output=plan_output(repo, to_fix=n, ids=ids),
              root=repo)
    app = repo / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + f"# {run_id}\n", encoding="utf-8")
    ps.record("L4", fixes=n, output=write_output(repo), root=repo)
    return ps.record("L5", detail="green", root=repo)


def _close_child(repo, run_id="child"):
    ps.record("L5.5", detail="all SAFE", root=repo)
    ps.record("L5.6", detail="no proofs", source="tool", root=repo)
    ps.record("L6", detail="APPROVED", root=repo)
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", f"fix: {run_id}"], check=True)
    ps.close(root=repo)


@pytest.mark.parametrize("kwargs, summary, match", [
    ({"tier": "STANDARD"}, None, "needs --from"),
    ({"tier": "FAST", "parent": "p"}, {}, "cannot be FAST"),
    ({"tier": "STANDARD", "parent": "p", "no_fix": True}, {}, "exists to fix"),
    ({"tier": "STANDARD", "parent": "absent"}, None, "no completed run 'absent'"),
    ({"tier": "STANDARD", "parent": "p"}, {"mode": "DEFERRED"}, "third pass"),
    ({"tier": "STANDARD", "parent": "p"}, {"mode": "HUNT"}, "hunt"),
    ({"tier": "STANDARD", "parent": "p"}, {"no_fix": True}, "no-fix"),
    ({"tier": "STANDARD", "parent": "p"}, {"pool": []}, "nothing was deferred"),
])
def test_start_deferred_refuses_a_parent_it_cannot_follow(git_repo, kwargs, summary, match):
    """Each case is refused for its own reason, and no run is opened.

    Red 2026-10-11, one mutation per case; every case failed at
    `pytest.raises(ps.RecordRefused, match=match)`. Case 0: `if not parent:` became
    `if False:` in _deferred_parent (the reason read "no completed run ''", which does not
    match "needs --from"). Case 1: the FAST check became `if False:` in _deferred_parent (DID
    NOT RAISE RecordRefused). Case 2: the no_fix check became `if False:` there (DID NOT
    RAISE). Case 3: `return parent, None` added before the `summary is None` refusal there
    (DID NOT RAISE). Cases 4 to 7: the matching check in deferred.why_not_offered (mode
    DEFERRED, mode HUNT, no_fix, empty pool) became `if False:` (DID NOT RAISE each).
    """
    if summary is not None:
        _write_summary_file(git_repo, "p", **summary)
    kw = dict(kwargs)  # a copy: the parametrize dict is shared across reruns
    with pytest.raises(ps.RecordRefused, match=match):
        ps.start(kw.pop("tier"), mode="DEFERRED", root=git_repo, **kw)
    assert ps.load(git_repo) is None


def test_from_is_refused_outside_a_deferred_run(git_repo):
    """`--from` is refused with any other mode, and the refusal opens no run.

    Red 2026-10-11: mutation `if parent and mode != "DEFERRED":` became `if False:` in
    start(); failed at pytest.raises (DID NOT RAISE RecordRefused). Also (fix round 1, for the
    `ps.load` assertion): the check moved to just after `_write(state, root)` in start();
    failed at `assert ps.load(git_repo) is None` (the run was left open).
    """
    with pytest.raises(ps.RecordRefused, match="--from"):
        ps.start("STANDARD", parent="p", root=git_repo)
    assert ps.load(git_repo) is None


def test_a_deferred_run_takes_its_parents_base_and_names_its_parent(git_repo):
    """The child takes the parent's base_commit, not the current HEAD, and names its parent.

    Red 2026-10-11: mutation `return parent, summary.get("base_commit")` became
    `return parent, _commit_of(root, "HEAD")` in _deferred_parent; failed at
    `assert child.parent == "parent" and child.base_commit == parent_base` (the base was the
    HEAD sha, not the parent's). Also: mutation `parent=stored_parent` dropped from the
    PipelineState(...) call in start(); failed at the same assert (parent was None).
    """
    parent_base = _head(git_repo)
    closed_parent(git_repo)
    child = ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c", root=git_repo)
    assert child.parent == "parent" and child.base_commit == parent_base


def test_the_commit_fallback_needs_a_commit_that_is_an_ancestor_of_head(git_repo):
    """A commit that is no ancestor of HEAD (or is unknown) cannot be followed.

    Red 2026-10-11: mutation `sha is None or not _is_ancestor(root, sha)` became
    `sha is None` in _deferred_parent; failed at the orphan pytest.raises (DID NOT RAISE
    RecordRefused). The unknown sha cannot prove it: `_commit_of` already returns None for it.
    """
    def git(*args):
        return subprocess.run(["git", "-C", str(git_repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    with pytest.raises(ps.RecordRefused, match="ancestor of HEAD"):
        ps.start("STANDARD", mode="DEFERRED", parent="commit:deadbeef", root=git_repo)
    orphan = git("commit-tree", "-m", "orphan", git("rev-parse", "HEAD^{tree}"))
    with pytest.raises(ps.RecordRefused, match="ancestor of HEAD"):
        ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{orphan}", root=git_repo)
    head = _head(git_repo)
    state = ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{head[:8]}", root=git_repo)
    assert state.parent == f"commit:{head}" and state.base_commit is None


def test_is_ancestor_never_hands_git_a_value_that_reads_as_an_option(git_repo, monkeypatch):
    """A summary's `commit` is untrusted text; a value starting with `-` must not reach git as
    an option. The stub `_git` answers "ancestor" (exit 0) to anything it is asked, so the only
    thing that can make the answer False is the guard.

    Red 2026-10-11 (fix round 1): mutation the `if sha.startswith("-"): return False` guard of
    _is_ancestor deleted; failed at `assert ps._is_ancestor(git_repo, "--all") is False and
    calls == []` (True is False). The second assert pins that an ordinary sha still goes to git
    as `merge-base --is-ancestor <sha> HEAD`.
    """
    calls = []

    def fake_git(root, *args):
        calls.append(args)
        return subprocess.CompletedProcess(args, 0, b"", b"")

    monkeypatch.setattr(ps, "_git", fake_git)
    assert ps._is_ancestor(git_repo, "--all") is False and calls == []
    assert ps._is_ancestor(git_repo, "abc1234") is True
    assert calls == [("merge-base", "--is-ancestor", "abc1234", "HEAD")]


@pytest.mark.parametrize("rev", [
    "HEAD", "-x", "HEAD~0", "{head6}", "{head}" + "0" * 25,
])
def test_the_commit_fallback_takes_only_a_hexadecimal_object_name(git_repo, rev):
    """`commit:<x>` takes 7 to 64 hexadecimal characters and nothing git would read otherwise.

    A revision expression (`HEAD`, `HEAD~0`), an option (`-x`), a prefix too short to be
    safe and a name one character longer than a SHA-256 object name (65) are all refused
    before git sees them, whatever git would make of them.

    Red 2026-10-11: mutation `if not _COMMIT_SHA.fullmatch(rev):` became `if False:` in
    _deferred_parent; every case failed at pytest.raises. HEAD, HEAD~0 and {head6}: DID NOT
    RAISE RecordRefused (git resolved each to the HEAD commit). -x and the 65 character name:
    the regex did not match (the reason was "does not name a commit that is an ancestor of
    HEAD"). Re-run after the 64 character limit (M6): the same mutation, the same failures,
    the 65 character case replacing the old 41 character one.
    """
    head = _head(git_repo)
    parent = "commit:" + rev.format(head=head, head6=head[:6])
    with pytest.raises(ps.RecordRefused, match="7 to 64 hexadecimal characters"):
        ps.start("STANDARD", mode="DEFERRED", parent=parent, root=git_repo)
    assert ps.load(git_repo) is None


def test_the_commit_fallback_takes_a_sha_256_object_name(git_repo):
    """A SHA-256 repository names a commit with 64 hexadecimal characters, so such a name gets
    past the form check to git, which here (a SHA-1 repository) finds no such commit.

    Red 2026-10-11: mutation `{7,64}` became `{7,40}` in _COMMIT_SHA; failed at
    pytest.raises(match="does not name a commit that is an ancestor") (Regex pattern did not
    match: the reason was the form refusal, "is not of the form commit:<sha>").
    """
    with pytest.raises(ps.RecordRefused, match="does not name a commit that is an ancestor"):
        ps.start("STANDARD", mode="DEFERRED", parent="commit:" + "a" * 64, root=git_repo)
    assert ps.load(git_repo) is None


def test_a_commit_is_taken_by_one_deferred_run_only(git_repo):
    """A commit named with `--from commit:<sha>` is taken by one deferred run only.

    Red 2026-10-11: mutation `if child:` became `if False:` after the consumed_by call in the
    commit branch of _deferred_parent; failed at the second start's pytest.raises (DID NOT
    RAISE RecordRefused). Re-run 2026-10-11 after `_child_to_l5` was split by parent kind
    (I1): the same mutation, the same failure.
    """
    head = _head(git_repo)
    _child_to_l5(git_repo, f"commit:{head}")
    _close_child(git_repo)
    assert _summary_of(git_repo, "child")["parent"] == f"commit:{head}"
    with pytest.raises(ps.RecordRefused, match="already took the findings"):
        ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{head[:10]}", run_id="again",
                 root=git_repo)


def test_a_deferred_run_cannot_defer(git_repo):
    """A DEFERRED run may not defer, at L2.5 or at L3; STALE is still allowed at L2.5.

    The parent deferred F-3 (P3) at L2.5 and F-2 (a confirmed P2) at L3, so the child's L2 is
    that pool, and the finding it tries to pass on unverified is the P2.

    Red 2026-10-11: mutation `if n_deferred and state.mode == "DEFERRED":` became `if False:`
    in the L2.5 branch of _check_counts; failed at the first pytest.raises (the regex did not
    match: the reason was "L2.5 defers F-2 (P2)", the shape rule). Also: the same mutation in
    the L3 branch; failed at the second pytest.raises (DID NOT RAISE RecordRefused).
    (key renamed to unverified at the master merge)
    The second ledger row is a STALE row since the master merge, which tallies the ledger per
    verdict: a second CONFIRMED row against `--stale 1` would now be refused.
    Re-run 2026-10-11 after the child's L2 became the parent's pool (I1): both mutations, the
    same two failures, the first again naming "L2.5 defers F-2 (P2)".
    """
    closed_parent(git_repo, p2_deferred=1, p3=1)
    ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    l2, ids = pool_output(git_repo)
    assert ids == ["F-3", "F-2"]
    ps.record("L2", counts={"unique": 2}, output=l2, root=git_repo)
    append_ledger(git_repo, "c", 1)
    with pytest.raises(ps.RecordRefused, match="cannot defer"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "unverified": 1},
                  output=verdicts_output(git_repo, confirmed=1, unverified=1, ids=ids),
                  root=git_repo)
    append_ledger(git_repo, "c", 1, verdict="STALE")
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "stale": 1},
              output=verdicts_output(git_repo, confirmed=1, stale=1, ids=ids), root=git_repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="cannot defer"):
        ps.record("L3", counts={"to_fix": 0, "deferred": 1},
                  output=plan_output(git_repo, to_fix=0, deferred=1, ids=ids), root=git_repo)


def test_a_deferred_runs_stale_rows_tally_against_stale(git_repo):
    """A STALE finding gets a ledger row whose verdict is STALE, and the per-verdict ledger
    tally L2.5 runs on STANDARD and DEEP counts that row against `--stale`.

    Red 2026-10-11: mutation `"STALE": "stale"` dropped from _VERDICT_COUNTS; failed at the
    L2.5 record (uncaught RecordRefused: the disposition ledger for run c disagrees with the
    L2.5 counts (unknown verdict 'STALE')). Re-run 2026-10-11 after the child's L2 became the
    parent's pool (I1): the same mutation, the same failure.
    """
    closed_parent(git_repo, p3=2)
    ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    l2, ids = pool_output(git_repo)
    ps.record("L2", counts={"unique": 2}, output=l2, root=git_repo)
    append_ledger(git_repo, "c", 1)
    append_ledger(git_repo, "c", 1, verdict="STALE")
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "stale": 1},
              output=verdicts_output(git_repo, confirmed=1, stale=1, ids=ids), root=git_repo)
    assert ps.load(git_repo).layers["L2.5"]["counts"]["stale"] == 1


def test_a_deferred_run_owes_every_fix_layer_until_l6_approves(git_repo):
    """A DEFERRED run owes L5.5, L5.6 and L6 like any fix run, and is not done until L6 approves.

    Red 2026-10-11: mutation `if state.get("mode") == "DEFERRED": return ("L1", "L2", "L2.5",
    "L3", "L5")` added as the first lines of cca_gate_core.required_layers; failed at
    `assert {"L5.5", "L5.6", "L6"} <= owed` (owed was the empty set). Re-run 2026-10-11 after
    `_child_to_l5` began recording the parent's pool as L2 (I1): the same mutation, the same
    failure.
    """
    closed_parent(git_repo)
    state = _child_to_l5(git_repo, "parent")
    owed = {r.split(" ")[0] for r in ps.verify_commit_allowed(state, root=git_repo).reasons}
    assert {"L5.5", "L5.6", "L6"} <= owed
    ps.record("L5.5", detail="all SAFE", root=git_repo)
    ps.record("L5.6", detail="no proofs", source="tool", root=git_repo)
    state = ps.record("L6", detail="REVISE", root=git_repo)
    assert not ps.verify_commit_allowed(state, root=git_repo).allowed
    state = ps.record("L6", detail="APPROVED", root=git_repo)
    assert ps.verify_commit_allowed(state, root=git_repo).allowed


def test_a_closed_deferred_run_takes_its_parents_pool(git_repo):
    """A closed DEFERRED run is recorded as its parent's child, so the pool is taken once.

    Red 2026-10-11: mutation `return None` as the first line of deferred.consumed_by; failed at
    the final pytest.raises (DID NOT RAISE RecordRefused). Also: mutation
    `parent=stored_parent` dropped from the PipelineState(...) call in start(); failed at
    `assert _summary_of(git_repo, "child")["parent"] == "parent"` (None == 'parent').
    Re-run 2026-10-11 after `_child_to_l5` began recording the parent's pool as L2 (I1): the
    consumed_by mutation fails as before. The dropped `parent=stored_parent` now fails earlier,
    at the child's L2 record (uncaught RecordRefused: the parent run None has no readable
    summary), because L2 is held to the pool of `state.parent`; the stored parent is pinned in
    its own assertion by test_a_deferred_run_takes_its_parents_base_and_names_its_parent, which
    that mutation still turns red there (None == 'parent').
    """
    closed_parent(git_repo)
    _child_to_l5(git_repo, "parent")
    _close_child(git_repo)
    assert _summary_of(git_repo, "child")["parent"] == "parent"
    with pytest.raises(ps.RecordRefused, match="already took this pool"):
        ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="again", root=git_repo)


def test_an_aborted_deferred_run_leaves_the_pool_on_offer(git_repo):
    """Review Focus 3: an aborted deferred run writes no summary, so the pool stays on offer.

    Red 2026-10-11: mutation `_write_summary(load(root), root)` added before the unlink in
    abort(); failed at the second start (uncaught RecordRefused: run parent cannot be followed
    up: deferred run c1 already took this pool).
    """
    closed_parent(git_repo)
    ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c1", root=git_repo)
    ps.abort("blocked", root=git_repo)
    assert ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c2",
                    root=git_repo).parent == "parent"


# --- fix round 1: the commit spelling, --base and the inherited base cannot dodge the rules --

def test_a_commit_that_has_a_summary_is_followed_by_its_run_id(git_repo):
    """A sha that is the commit of a run with a summary is refused, naming the run to use.

    Without it `--from commit:<sha>` skips every rule a run id is held to.

    Red 2026-10-11: mutation `if owners:` became `if False:` in the commit branch of
    _deferred_parent; failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    closed_parent(git_repo)
    commit = _summary_of(git_repo, "parent")["commit"]
    assert commit == _head(git_repo)
    with pytest.raises(ps.RecordRefused, match=r"commit of completed run parent.*--from parent"):
        ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{commit}", root=git_repo)
    assert ps.load(git_repo) is None


def test_a_taken_pool_cannot_be_taken_again_by_spelling_its_commit(git_repo):
    """A pool already taken is refused by run id and by the commit of the run that holds it.

    Red 2026-10-11: mutation `if owners:` became `if False:` in the commit branch of
    _deferred_parent; failed at the second pytest.raises (DID NOT RAISE RecordRefused); the
    first, by run id, is the existing consumed_by rule and still raised. Re-run 2026-10-11
    after `_child_to_l5` began recording the parent's pool as L2 (I1): the same mutation, the
    same failure.
    """
    closed_parent(git_repo)
    parent_commit = _summary_of(git_repo, "parent")["commit"]
    _child_to_l5(git_repo, "parent")
    _close_child(git_repo)
    with pytest.raises(ps.RecordRefused, match="already took this pool"):
        ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="again", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="commit of completed run parent"):
        ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{parent_commit}",
                 run_id="again", root=git_repo)
    assert ps.load(git_repo) is None


@pytest.mark.parametrize("over", [{"mode": "HUNT"}, {"no_fix": True}, {"mode": "DEFERRED"}])
def test_a_parent_that_cannot_be_followed_cannot_be_followed_by_its_commit(git_repo, over):
    """A HUNT, no-fix or DEFERRED run is not followed up, whichever way its commit is spelled.

    Red 2026-10-11: mutation `if owners:` became `if False:` in the commit branch of
    _deferred_parent; each of the three cases failed at pytest.raises (DID NOT RAISE
    RecordRefused).
    """
    head = _head(git_repo)
    _write_summary_file(git_repo, "p", commit=head, **over)
    with pytest.raises(ps.RecordRefused, match="commit of completed run p"):
        ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{head}", root=git_repo)
    assert ps.load(git_repo) is None


def test_base_is_refused_with_a_deferred_run(git_repo):
    """`--base` is refused with a DEFERRED run, which takes its parent's base.

    Red 2026-10-11: mutation `if mode == "DEFERRED" and base != "HEAD":` became `if False:` in
    start(); failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    closed_parent(git_repo)
    with pytest.raises(ps.RecordRefused, match="takes its parent's base"):
        ps.start("STANDARD", mode="DEFERRED", parent="parent", base="HEAD~1", root=git_repo)
    assert ps.load(git_repo) is None


@pytest.mark.parametrize("stored", ["--output=x", "deadbeef", 7])
def test_an_inherited_base_that_does_not_resolve_is_dropped(git_repo, stored):
    """An inherited base that is not a commit of this repository is stored as None.

    The summary is a file a person can edit; a string such as `--output=x` would otherwise
    reach `git diff` as an option.

    Red 2026-10-11: mutation the three lines resolving the inherited base were replaced by
    `return parent, summary.get("base_commit")`
    in _deferred_parent; each of the three cases failed at `assert child.base_commit is None`
    (got '--output=x', 'deadbeef' and 7).
    """
    _write_summary_file(git_repo, "p", base_commit=stored)
    child = ps.start("STANDARD", mode="DEFERRED", parent="p", run_id="c", root=git_repo)
    assert child.base_commit is None
    raw = json.loads(ps.state_path(git_repo).read_text(encoding="utf-8"))
    assert raw["base_commit"] is None and raw["parent"] == "p"


def test_an_inherited_base_is_stored_as_the_full_sha(git_repo):
    """An inherited base that resolves is stored as the full sha `git` names, not as written.

    Red 2026-10-11: mutation the three lines resolving the inherited base were replaced by
    `return parent, summary.get("base_commit")`
    in _deferred_parent; failed at `assert child.base_commit == head` (the 10 character
    prefix against the full sha).
    """
    head = _head(git_repo)
    _write_summary_file(git_repo, "p", base_commit=head[:10])
    child = ps.start("STANDARD", mode="DEFERRED", parent="p", run_id="c", root=git_repo)
    assert child.base_commit == head


@pytest.mark.parametrize("parent", ["run_id", "commit"])
def test_an_unreadable_summary_refuses_a_deferred_start(git_repo, parent):
    """A summary that cannot be read refuses the start, naming the file, for both spellings.

    It might be the child that already took the pool, so reading past it would fail open.

    Red 2026-10-11: mutation `if unreadable:` became `if False:` in _deferred_parent; both
    cases failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    head = _head(git_repo)
    _write_summary_file(git_repo, "p")
    broken = git_repo / ".claude/audits/runs/broken/summary.json"
    broken.parent.mkdir(parents=True)
    broken.write_text("{not json", encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="cannot be read") as refused:
        ps.start("STANDARD", mode="DEFERRED", root=git_repo,
                 parent="p" if parent == "run_id" else f"commit:{head}")
    assert "runs/broken/summary.json" in str(refused.value)
    assert ps.load(git_repo) is None


def test_a_deferred_run_cannot_defer_a_p3_finding_either(git_repo):
    """A DEFERRED run cannot defer a P3 finding either.

    The first-run priority rule accepts a P3 deferral on STANDARD, so the DEFERRED check is
    the only guard here.

    Red 2026-10-11: mutation `if n_deferred and state.mode == "DEFERRED":` became `if False:`
    in the L2.5 branch of _check_counts; failed at pytest.raises (DID NOT RAISE RecordRefused).
    (key renamed to unverified at the master merge) Re-run 2026-10-11 after the child's L2
    became the parent's pool, F-2 and F-3, both P3 (I1): the same mutation, the same failure.
    """
    closed_parent(git_repo, p3=2)
    ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    l2, ids = pool_output(git_repo)
    ps.record("L2", counts={"unique": 2}, output=l2, root=git_repo)
    append_ledger(git_repo, "c", 1)
    with pytest.raises(ps.RecordRefused, match="cannot defer"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "unverified": 1},
                  output=verdicts_output(git_repo, confirmed=1, unverified=1, ids=ids),
                  root=git_repo)
    assert "L2.5" not in ps.load(git_repo).layers


# --- the final review: a deferred run is held to its parent's pool (I1) ---------------------

def _pool_rows(repo, parent="parent") -> list[dict]:
    """Copies of the parent's stored L2 objects for its pool, in pool order."""
    run = repo / ".claude/audits/runs" / parent
    pool = json.loads((run / "summary.json").read_text(encoding="utf-8"))["pool"]
    stored = {f["id"]: f for f in json.loads((run / "L2.json").read_bytes())}
    return [dict(stored[i]) for i in pool]


@pytest.mark.parametrize("change, match", [
    ("drop", r"lacks F-4; .*merging none \(a duplicate gets the DUPLICATE verdict"),
    ("add", "adds F-9"),
    ("lower", r"changes F-2 \(P3, the parent's P2\)"),
])
def test_a_deferred_runs_l2_is_held_to_its_parents_pool(git_repo, change, match):
    """A DEFERRED run's L2 holds exactly the parent's pool ids, each at the parent's priority:
    dropping one (as merging a duplicate would), adding one, or lowering a P2 to P3 is refused,
    and the same pool in another order is accepted.

    The parent fixed F-1, deferred the confirmed P2 F-2 at L3 and the P3s F-3 and F-4 at L2.5.

    Red 2026-10-11, four mutations in pipeline_state. The `_held_to_pool(state, findings, root)`
    call removed from _check_shape: all three cases failed at pytest.raises (DID NOT RAISE
    RecordRefused). `if missing or extra:` became `if False:` in _held_to_pool: "drop" failed
    with DID NOT RAISE; "add" failed at pytest.raises' regex (the reason was the priority rule,
    "changes F-9 (P3, the parent's None)"). `if changed:` became `if False:` there: "lower"
    failed with DID NOT RAISE.
    """
    closed_parent(git_repo, p2_deferred=1, p3=2)
    ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    rows = _pool_rows(git_repo)
    assert [(r["id"], r["priority"]) for r in rows] == [("F-3", "P3"), ("F-4", "P3"),
                                                        ("F-2", "P2")]
    if change == "drop":
        rows = [r for r in rows if r["id"] != "F-4"]
    elif change == "add":
        rows.append({**rows[0], "id": "F-9"})
    else:
        rows[2]["priority"] = "P3"
    with pytest.raises(ps.RecordRefused, match=match):
        ps.record("L2", counts={"unique": len(rows)}, output=write_output(git_repo, rows),
                  root=git_repo)
    assert "L2" not in ps.load(git_repo).layers
    ps.record("L2", counts={"unique": 3},
              output=write_output(git_repo, list(reversed(_pool_rows(git_repo)))), root=git_repo)
    assert "L2" in ps.load(git_repo).layers


def test_a_commit_parents_l2_is_not_held_to_a_pool(git_repo):
    """A `commit:<sha>` parent left no summary and no stored L2, so there is no pool to hold the
    run to: its L2 is whatever D.1 parsed from the commit message.

    Red 2026-10-11: mutation `and not (state.parent or "").startswith("commit:")` removed from
    the L2 branch of _check_shape; failed at the L2 record (uncaught RecordRefused: the parent
    run 'commit:<sha>' has no readable summary under .claude/audits/runs/).
    """
    head = _head(git_repo)
    ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{head}", run_id="c", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2}, output=findings_output(git_repo, 2), root=git_repo)
    assert "L2" in ps.load(git_repo).layers


@pytest.mark.parametrize("damage, match", [
    ("summary", "has no readable summary"),
    ("l2", "cannot be loaded"),
])
def test_a_deferred_runs_l2_is_refused_when_the_parents_pool_cannot_be_read(git_repo, damage,
                                                                            match):
    """The pool is read from the parent's summary and stored L2 when L2 is recorded; when either
    has become unreadable since the start, L2 is refused (fail closed), not checked against
    nothing.

    Red 2026-10-11, two mutations in _held_to_pool: the `raise` under `if summary is None:`
    replaced by `return`; the "summary" case failed at pytest.raises (DID NOT RAISE
    RecordRefused). The `raise` in `except LookupError` replaced by `return`; the "l2" case
    failed the same way.
    """
    closed_parent(git_repo)
    ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    l2, ids = pool_output(git_repo)
    run = git_repo / ".claude/audits/runs/parent"
    if damage == "summary":
        (run / "summary.json").write_text("{torn", encoding="utf-8")
    else:
        (run / "L2.json").unlink()
    with pytest.raises(ps.RecordRefused, match=match):
        ps.record("L2", counts={"unique": len(ids)}, output=l2, root=git_repo)
    assert "L2" not in ps.load(git_repo).layers
