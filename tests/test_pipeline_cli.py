"""The pipeline CLI: count flags, refusals as exit 2, status --require-complete."""

from __future__ import annotations

import json

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
    _cli(capsys, "record", "L1", "--root", r)
    code, out, _ = _cli(capsys, "record", "L2", "--unique", "0", "--root", r)
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
    _cli(capsys, "record", "L1", "--root", r)
    _cli(capsys, "record", "L2", "--unique", "0", "--root", r)
    code, _, _ = _cli(capsys, "record", "L2.5", "--status", "n/a", "--confirmed", "0",
                      "--fp", "0", "--uncertain", "0", "--duplicate", "0", "--root", r)
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
    _cli(capsys, "record", "L1", "--root", r)
    _cli(capsys, "record", "L2", "--unique", "0", "--root", r)
    _cli(capsys, "record", "L2.5", "--confirmed", "0", "--fp", "0", "--uncertain", "0",
         "--duplicate", "0", "--root", r)
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
