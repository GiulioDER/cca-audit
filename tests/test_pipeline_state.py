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
from conftest import tree_of, write_output

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
    ps.record("L1", detail="6 auditors", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": confirmed}, output=write_output(repo), root=repo)
    if tier != "FAST":
        _ledger(repo, state.run_id, confirmed)
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": 0,
                              "duplicate": 0}, output=write_output(repo), root=repo)
    if tier != "FAST":
        ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    if not no_fix:
        ps.record("L3", counts={"to_fix": confirmed}, output=write_output(repo), root=repo)
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
    with pytest.raises(ps.RecordRefused, match="cca_tautology_check"):
        ps.record("L5.6", detail="1 RED", root=git_repo)


def test_l2_5_counts_must_account_for_every_unique_finding(git_repo):
    """Mutation: skip the sum comparison. Fails: no RecordRefused.

    Red 2026-10-10: `if unique is not None and total != unique` replaced by `if False`;
    failed at pytest.raises (DID NOT RAISE RecordRefused).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 3}, output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="3 unique"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 1, "uncertain": 0, "duplicate": 0},
                  output=write_output(git_repo), root=git_repo)


def test_ledger_must_hold_one_row_per_verdict(git_repo):
    """Mutation: skip the ledger comparison. Fails: no RecordRefused.

    Red 2026-10-10: `if rows != total` replaced by `if False`; failed at pytest.raises
    (DID NOT RAISE RecordRefused).
    """
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2}, output=write_output(git_repo), root=git_repo)
    _ledger(git_repo, state.run_id, 1)
    with pytest.raises(ps.RecordRefused, match="ledger holds 1"):
        ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
                  output=write_output(git_repo), root=git_repo)


def test_ledger_count_ignores_outcome_rows(git_repo):
    """Review Focus 4. Mutation: count every row with the run_id. Fails: RecordRefused.

    Red 2026-10-10: `"verdict" in row` dropped from _ledger_rows; failed at the L2.5
    record call (RecordRefused: ledger holds 4 rows, 2 expected).
    """
    state = ps.start("STANDARD", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2}, output=write_output(git_repo), root=git_repo)
    _ledger(git_repo, state.run_id, 2, outcome_rows=2)
    ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(git_repo), root=git_repo)


def test_missing_count_flags_are_refused(git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="--unique"):
        ps.record("L2", output=write_output(git_repo), root=git_repo)


def test_l3_cannot_plan_more_fixes_than_were_confirmed(git_repo):
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 1}, output=write_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="--to-fix"):
        ps.record("L3", counts={"to_fix": 2}, output=write_output(git_repo), root=git_repo)


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
    findings = [{"id": "BUG-1", "file": "app.py", "line": 1}]
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
    ps.record("L2", counts={"unique": 0}, output=write_output(git_repo), root=git_repo)
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
              output=write_output(git_repo), root=git_repo)


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
    ps.record("L2", counts={"unique": 1}, output=write_output(repo), root=repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(repo), root=repo)
    ps.record("L3", counts={"to_fix": to_fix}, output=write_output(repo), root=repo)


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
