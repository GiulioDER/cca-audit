"""The pipeline CLI: count flags, refusals as exit 2, status --require-complete."""

from __future__ import annotations

import json

from conftest import findings_output, verdicts_output, write_output

from cca_checks.__main__ import main


def _cli(capsys, *args) -> tuple[int, dict, dict]:
    code = main(["pipeline", *args])
    out, err = capsys.readouterr()
    return code, json.loads(out) if out.strip() else {}, json.loads(err) if err.strip() else {}


def test_out_of_order_record_exits_2_with_the_reason(capsys, git_repo):
    """A refused record is exit 2 with the layer and reason on stderr.

    Red 2026-10-10: baseline d2317de (record never refuses, exit 0); failed at
    `assert code == 2`.
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    code, _, err = _cli(capsys, "record", "L6", "--detail", "APPROVED", "--root", r)
    assert code == 2 and err["refused"] == "L6" and "L3" in err["reason"]


def test_a_second_start_exits_2_with_the_reason_and_keeps_the_run(capsys, git_repo):
    """A second `pipeline start` is {"refused": "start"} with exit 2 and changes nothing.

    Red 2026-10-10: baseline a8d7445 (second start exits 0 and replaces the run); failed at
    `assert code == 2 and err["refused"] == "start"`.
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "DEEP", "--mode", "HUNT", "--root", r)
    _cli(capsys, "record", "L0.4", "--root", r)
    code, _, err = _cli(capsys, "start", "--tier", "FAST", "--root", r)
    assert code == 2 and err["refused"] == "start"
    assert "pipeline abort" in err["reason"]
    raw = json.loads((git_repo / ".claude/audits/run-state.json").read_text(encoding="utf-8"))
    assert raw["tier"] == "DEEP" and "L0.4" in raw["layers"]


def test_count_flags_reach_the_state(capsys, git_repo):
    """--unique reaches the state file, and a recorded zero is kept, not dropped.

    Red 2026-10-10: mutation `"unique": a.unique` -> `"unique": None` in `_pipeline`
    (record); failed at `assert code == 0 and out["recorded"] == "L2"` (exit 2).
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    _cli(capsys, "record", "L1", "--output", write_output(r), "--root", r)
    code, out, _ = _cli(capsys, "record", "L2", "--unique", "0", "--output",
                        findings_output(r), "--root", r)
    assert code == 0 and out["recorded"] == "L2"
    raw = json.loads((git_repo / ".claude/audits/run-state.json").read_text(encoding="utf-8"))
    assert raw["layers"]["L2"]["counts"] == {"unique": 0}


def test_status_require_complete_exits_2_while_layers_are_owed(capsys, git_repo):
    """status --require-complete is the Codex stand-in for the Stop hook.

    Red 2026-10-10: mutation `return 2 if (a.require_complete and owed) else 0` -> `return 0`
    in `_pipeline` (status); failed at `assert code == 2 and out["outstanding_to_close"]`.
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    code, out, _ = _cli(capsys, "status", "--require-complete", "--root", r)
    assert code == 2 and out["outstanding_to_close"]


def test_close_on_an_incomplete_run_exits_2(capsys, git_repo):
    """close refuses an incomplete run with {"refused": "close"} and exit 2.

    Red 2026-10-10: mutation `return 2` -> `return 0` in the RecordRefused handler around
    `pipeline_state.close` in `_pipeline`; failed at
    `assert code == 2 and err["refused"] == "close"`.
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    code, _, err = _cli(capsys, "close", "--root", r)
    assert code == 2 and err["refused"] == "close"


def test_n_a_is_still_accepted_for_a_layer_this_run_does_not_require(capsys, git_repo):
    """--status n/a is passed through, so status can say the layer is owed.

    Red 2026-10-10: mutation `status=a.status` -> `status="done"` in `_pipeline` (record);
    failed at `assert any("L2.5 is recorded as n/a" in x for x in out["outstanding_to_close"])`.
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    _cli(capsys, "record", "L1", "--output", write_output(r), "--root", r)
    _cli(capsys, "record", "L2", "--unique", "0", "--output", findings_output(r),
         "--root", r)
    code, _, _ = _cli(capsys, "record", "L2.5", "--status", "n/a", "--confirmed", "0",
                      "--fp", "0", "--uncertain", "0", "--duplicate", "0",
                      "--output", verdicts_output(r), "--root", r)
    assert code == 0
    code, out, _ = _cli(capsys, "status", "--require-complete", "--root", r)
    assert any("L2.5 is recorded as n/a" in x for x in out["outstanding_to_close"])


def test_status_require_complete_exits_2_for_a_complete_run_that_is_not_closed(
    capsys, git_repo
):
    """A run with nothing owed is still open until `pipeline close`, so Codex must not report.

    Invariant: `--require-complete` is 0 only when no run is open; an open run with an empty
    `outstanding` exits 2 and `outstanding_to_close` names `pipeline close`.

    Red 2026-10-10: baseline a8d7445 (`owed` empty, so exit 0); failed at `assert code == 2`.
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--no-fix", "--root", r)
    _cli(capsys, "record", "L1", "--output", write_output(r), "--root", r)
    _cli(capsys, "record", "L2", "--unique", "0", "--output", findings_output(r),
         "--root", r)
    _cli(capsys, "record", "L2.5", "--confirmed", "0", "--fp", "0", "--uncertain", "0",
         "--duplicate", "0", "--output", verdicts_output(r), "--root", r)
    code, out, _ = _cli(capsys, "status", "--require-complete", "--root", r)
    assert code == 2
    assert len(out["outstanding_to_close"]) == 1
    assert "pipeline close" in out["outstanding_to_close"][0]
    # Without the flag the same state is reported but not an error.
    code, out, _ = _cli(capsys, "status", "--root", r)
    assert code == 0
    # Once closed, no run is open and the check passes.
    assert _cli(capsys, "close", "--root", r)[0] == 0
    code, out, _ = _cli(capsys, "status", "--require-complete", "--root", r)
    assert code == 0 and out["open"] is False


def test_record_output_reaches_the_state_and_is_echoed(capsys, git_repo):
    """`--output` is passed through, copied into the run, and printed back.

    Red 2026-10-11: mutation `output=a.output` became `output=None` in `_pipeline` (record);
    failed at `assert code == 0 and out["output"] == ...` (exit 2: L1 needs --output).
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--run-id", "r7", "--root", r)
    code, out, _ = _cli(capsys, "record", "L1", "--output", write_output(r, [[]]), "--root", r)
    assert code == 0 and out["output"] == ".claude/audits/runs/r7/L1.json"
    assert (git_repo / ".claude/audits/runs/r7/L1.json").is_file()


def test_status_shows_the_pause_and_require_complete_exits_2(capsys, git_repo):
    """A paused run is reported at the top level and is never complete.

    Red 2026-10-11: mutation the `if state is not None and state.paused:` block in `_pipeline`
    (status) became `if False:`, on a complete FAST no-fix run whose only other item would be
    `pipeline close`; failed at `assert any("run is paused" in x for x in
    out["outstanding_to_close"])`.
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--no-fix", "--root", r)
    _cli(capsys, "record", "L1", "--output", write_output(r), "--root", r)
    _cli(capsys, "record", "L2", "--unique", "0", "--output", findings_output(r),
         "--root", r)
    _cli(capsys, "record", "L2.5", "--confirmed", "0", "--fp", "0", "--uncertain", "0",
         "--duplicate", "0", "--output", verdicts_output(r), "--root", r)
    assert _cli(capsys, "pause", "--reason", "end of day", "--root", r)[0] == 0
    code, out, _ = _cli(capsys, "status", "--require-complete", "--root", r)
    assert code == 2
    assert out["paused"]["reason"] == "end of day"
    assert any("run is paused" in x for x in out["outstanding_to_close"])


def test_pause_and_resume_through_the_cli(capsys, git_repo):
    """pause prints the pause, a second pause and a paused record exit 2, resume prints JSON.

    Red 2026-10-11: mutation `return 2` became `return 0` in the RecordRefused handler of the
    pause/resume branch of `_pipeline`; failed at `assert code == 2 and err["refused"] ==
    "pause" and "run is paused" in err["reason"]` (0 == 2).
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--run-id", "r9", "--root", r)
    _cli(capsys, "record", "L1", "--output", write_output(r), "--root", r)
    code, out, _ = _cli(capsys, "pause", "--reason", "machine restart", "--root", r)
    assert code == 0 and out["paused"] == "r9" and out["reason"] == "machine restart"
    code, _, err = _cli(capsys, "pause", "--reason", "again", "--root", r)
    assert code == 2 and err["refused"] == "pause" and "run is paused" in err["reason"]
    code, _, err = _cli(capsys, "record", "L2", "--unique", "0", "--output",
                        findings_output(r), "--root", r)
    assert code == 2 and err["refused"] == "L2" and "pipeline resume" in err["reason"]
    code, out, _ = _cli(capsys, "resume", "--root", r)
    assert code == 0
    assert out == {"resumed": "r9", "tree_changed": False, "next": "L2", "heading": "Step 2",
                   "outputs": {"L1": ".claude/audits/runs/r9/L1.json"},
                   "outstanding": out["outstanding"]}
    assert out["outstanding"]
    code, _, err = _cli(capsys, "resume", "--root", r)
    assert code == 2 and err["refused"] == "resume"



def test_record_output_outside_claude_audits_exits_2(capsys, git_repo):
    """A --output file outside .claude/audits/ is {"refused": "L1"} with exit 2.

    Red 2026-10-11: mutation the containment check in `pipeline_state._read_output` became
    `if False:`; failed at `assert code == 2 and err["refused"] == "L1"` (0 == 2).
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    outside = git_repo / "findings.json"
    outside.write_text("[]", encoding="utf-8")
    code, _, err = _cli(capsys, "record", "L1", "--output", str(outside), "--root", r)
    assert code == 2 and err["refused"] == "L1"
    assert "outside .claude/audits/" in err["reason"]


def test_a_relative_output_is_read_from_the_root_not_the_cwd(capsys, git_repo, monkeypatch,
                                                              tmp_path_factory):
    """`--output .claude/audits/out/l1.json` means the one under `--root`, whatever the cwd.

    Red 2026-10-11: baseline bacf6f0 (resolved against the cwd, so refused as outside);
    failed at `assert code == 0 and out["recorded"] == "L1"` (2 == 0).
    """
    r = str(git_repo)
    target = git_repo / ".claude" / "audits" / "out" / "l1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('[["finding"]]', encoding="utf-8")
    monkeypatch.chdir(tmp_path_factory.mktemp("elsewhere"))
    _cli(capsys, "start", "--tier", "FAST", "--run-id", "rel", "--root", r)
    code, out, _ = _cli(capsys, "record", "L1", "--output", ".claude/audits/out/l1.json",
                        "--root", r)
    assert code == 0 and out["recorded"] == "L1"
    copied = git_repo / ".claude" / "audits" / "runs" / "rel" / "L1.json"
    assert copied.read_text(encoding="utf-8") == '[["finding"]]'


def test_start_with_a_bad_run_id_exits_2(capsys, git_repo):
    """`pipeline start --run-id` with a forbidden character is {"refused": "start"}, exit 2.

    Red 2026-10-11: baseline bacf6f0 (start accepts it, exit 0); failed at
    `assert code == 2 and err["refused"] == "start"` (0 == 2).
    """
    code, _, err = _cli(capsys, "start", "--tier", "FAST", "--run-id", "run:1",
                        "--root", str(git_repo))
    assert code == 2 and err["refused"] == "start"


def test_deferred_and_stale_flags_reach_the_counts(capsys, git_repo):
    """--deferred reaches record's counts, so L2.5 can account for a deferred finding.

    Red 2026-10-11: mutation `"deferred": a.deferred` became `"deferred": None` in _pipeline;
    failed at `assert code == 0` (2 == 0: accounts for 1 findings but L2 recorded 2 unique).
    --stale reaches it too: a first run refuses it, and the reason says so.
    Red 2026-10-11: mutation `"stale": a.stale` became `"stale": None` in _pipeline; failed at
    `assert "--stale is for a DEFERRED run only" in err["reason"]` (the reason instead read
    "accounts for 1 findings but L2 recorded 2 unique").
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    _cli(capsys, "record", "L1", "--output", write_output(r), "--root", r)
    _cli(capsys, "record", "L2", "--unique", "2", "--output", findings_output(r, 2), "--root", r)
    code, _, err = _cli(capsys, "record", "L2.5", "--confirmed", "1", "--fp", "0",
                        "--uncertain", "0", "--duplicate", "0", "--stale", "1",
                        "--output", verdicts_output(r, confirmed=1, stale=1), "--root", r)
    assert code == 2
    assert "--stale is for a DEFERRED run only" in err["reason"]
    code, _, _ = _cli(capsys, "record", "L2.5", "--confirmed", "1", "--fp", "0",
                      "--uncertain", "0", "--duplicate", "0", "--deferred", "1",
                      "--output", verdicts_output(r, confirmed=1, deferred=1), "--root", r)
    assert code == 0


def test_base_reaches_the_state(capsys, git_repo):
    """--base reaches `start`: a revision that names no commit is refused with exit 2.

    Red 2026-10-11: mutation `base=a.base` dropped from the start call in _pipeline; failed at
    `assert code == 2` (start used HEAD and succeeded: 0 == 2).
    """
    code, _, err = _cli(capsys, "start", "--tier", "FAST", "--base", "no-such-rev",
                        "--root", str(git_repo))
    assert code == 2 and "does not name a commit" in err["reason"]
