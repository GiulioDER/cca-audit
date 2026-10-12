"""The pipeline CLI: count flags, refusals as exit 2, status --require-complete."""

from __future__ import annotations

import json

import pytest
from conftest import closed_parent, findings_output, plan_output, verdicts_output, write_output

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
    """--unverified reaches record's counts, so L2.5 can account for a DEFERRED finding.

    Red 2026-10-11: mutation `"deferred": a.deferred` became `"deferred": None` in _pipeline;
    failed at `assert code == 0` (2 == 0: accounts for 1 findings but L2 recorded 2 unique).
    (key renamed to unverified at the master merge)
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
                      "--uncertain", "0", "--duplicate", "0", "--unverified", "1",
                      "--output", verdicts_output(r, confirmed=1, unverified=1), "--root", r)
    assert code == 0


def test_base_reaches_the_state(capsys, git_repo):
    """--base reaches `start`: a revision that names no commit is refused with exit 2.

    Red 2026-10-11: mutation `base=a.base` dropped from the start call in _pipeline; failed at
    `assert code == 2` (start used HEAD and succeeded: 0 == 2).
    """
    code, _, err = _cli(capsys, "start", "--tier", "FAST", "--base", "no-such-rev",
                        "--root", str(git_repo))
    assert code == 2 and "does not name a commit" in err["reason"]


def test_from_reaches_start(capsys, git_repo):
    """--from reaches `start`: a DEFERRED run is opened on the completed run it names.

    Red 2026-10-11: mutation `parent=a.parent` dropped from the start call in _pipeline; failed
    at `assert code == 0` (2 == 0: the run was refused, --mode DEFERRED needs --from).
    """
    closed_parent(git_repo)
    code, out, _ = _cli(capsys, "start", "--tier", "STANDARD", "--mode", "DEFERRED",
                        "--from", "parent", "--root", str(git_repo))
    assert code == 0 and out["started"]["parent"] == "parent"


# --- pipeline deferred ---------------------------------------------------------------

def _corrupt_summary(repo, run_id="broken") -> str:
    """A summary file that cannot be parsed; returns its path as `load_summaries` names it."""
    path = repo / ".claude" / "audits" / "runs" / run_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    return path.as_posix()


def test_deferred_reports_the_newest_run_and_recommends(capsys, git_repo):
    """Red 2026-10-11: mutation `summary = summaries[-1]` became `summaries[0]` in
    _deferred_context, with two parents; failed at `assert code == 0 and out["run_id"] ==
    "second"` ('first' == 'second')."""
    closed_parent(git_repo, run_id="first", p3=1)
    closed_parent(git_repo, run_id="second", p3=3)
    code, out, _ = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 0 and out["run_id"] == "second"
    assert out["offered"] is True and out["recommendation"] == "RUN"
    assert out["counts"]["P3"] == 3 and out["other_pools"] == ["first"]


def test_deferred_prints_the_parents_audited_tree_and_base(capsys, git_repo):
    """D.4 hands fp-check the parent's `base_tree` and `base_commit`, read from this command's
    JSON, with or without `--write`.

    Red 2026-10-11: mutation `base_tree=...` became `base_tree=None` in deferred.assess; failed
    at `assert code == 0 and out["base_tree"] == summary["base_tree"]` (None against the sha).
    """
    closed_parent(git_repo, p3=3)
    summary = json.loads((git_repo / ".claude/audits/runs/parent/summary.json")
                         .read_text(encoding="utf-8"))
    assert summary["base_tree"]
    code, out, _ = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 0 and out["base_tree"] == summary["base_tree"]
    assert out["base_commit"] == summary["base_commit"]
    code, out, _ = _cli(capsys, "deferred", "--write", ".claude/audits/out/L1.json",
                        "--root", str(git_repo))
    assert code == 0 and out["base_tree"] == summary["base_tree"]


def test_deferred_run_selects_a_named_run_and_refuses_an_unknown_one(capsys, git_repo):
    """Red 2026-10-11: mutation `if run_id:` became `if False:` in _deferred_context; failed at
    `assert code == 0 and out["run_id"] == "first" ...` ('second' == 'first': the newest run was
    reported).
    Red 2026-10-11: mutation the `raise RecordRefused` under `if summary is None:` in
    _deferred_context became `summary = summaries[-1]`; failed at `assert code == 2 and
    err["refused"] == "deferred" ...` (0 == 2: the newest run was reported for the unknown id)."""
    closed_parent(git_repo, run_id="first", p3=1)
    closed_parent(git_repo, run_id="second", p3=3)
    code, out, _ = _cli(capsys, "deferred", "--run", "first", "--root", str(git_repo))
    assert code == 0 and out["run_id"] == "first" and out["counts"]["P3"] == 1
    code, _, err = _cli(capsys, "deferred", "--run", "ghost", "--root", str(git_repo))
    assert code == 2 and err["refused"] == "deferred" and "'ghost'" in err["reason"]


def test_deferred_write_copies_exactly_the_pool(capsys, git_repo):
    """The L1 file holds the parent's stored L2 objects for exactly the pool ids, in pool order,
    whole: the tier rule and the scorecard read `auditor`, `category` and `high_stakes`, which the
    five key projection in `Offer.pool` does not carry.

    Red 2026-10-11: mutation `json.dumps(findings, ...)` became a dump of the parent's whole
    stored L2.json in write_deferred; failed at `assert code == 0 and [f["id"] for f in written]
    == ["F-2", "F-3"]` (got F-1, F-2, F-3).
    Red 2026-10-11 (fix round 1): mutation `json.dumps(findings, ...)` became
    `json.dumps(offer.pool, ...)` in write_deferred; failed at `assert written == [stored[i] for
    i in pool]` (the written objects lacked auditor, category and high_stakes).
    """
    closed_parent(git_repo, p3=2)
    run = git_repo / ".claude/audits/runs/parent"
    stored = {f["id"]: f for f in json.loads((run / "L2.json").read_bytes())}
    pool = json.loads((run / "summary.json").read_text(encoding="utf-8"))["pool"]
    assert {"auditor", "category", "high_stakes"} <= set(stored["F-2"])
    target = git_repo / ".claude/audits/out/L1.json"
    code, out, _ = _cli(capsys, "deferred", "--write", str(target), "--root", str(git_repo))
    written = json.loads(target.read_text(encoding="utf-8"))
    assert code == 0 and [f["id"] for f in written] == ["F-2", "F-3"]
    assert all(f["priority"] == "P3" and "claim" in f for f in written)
    assert written == [stored[i] for i in pool]
    assert out["written"] == str(target) and out["run_id"] == "parent"


def test_deferred_write_replaces_a_stale_l1_file(capsys, git_repo):
    """Red 2026-10-11: mutation `path.write_text(...)` guarded by `if not path.exists():` in
    write_deferred (an existing file is kept); failed at `assert "OLD-1" not in
    target.read_text(encoding="utf-8")`."""
    closed_parent(git_repo, p3=3)
    target = git_repo / ".claude/audits/out/L1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('[{"id": "OLD-1"}]', encoding="utf-8")
    _cli(capsys, "deferred", "--write", str(target), "--root", str(git_repo))
    assert "OLD-1" not in target.read_text(encoding="utf-8")


def test_deferred_write_is_refused_outside_claude_audits(capsys, git_repo):
    """Red 2026-10-11: mutation the `_inside` check in write_deferred became `if False:`; failed
    at `assert code == 2 and "outside .claude/audits/" in err["reason"]` (0 == 2)."""
    closed_parent(git_repo, p3=3)
    code, _, err = _cli(capsys, "deferred", "--write", str(git_repo / "L1.json"),
                        "--root", str(git_repo))
    assert code == 2 and "outside .claude/audits/" in err["reason"]
    assert not (git_repo / "L1.json").exists()


def test_deferred_write_takes_a_path_relative_to_the_root(capsys, git_repo, monkeypatch):
    """The form `/audit-fix deferred` uses: a path relative to `--root`, not to the cwd.

    Red 2026-10-11 (fix round 1): mutation the `path = pathlib.Path(root) / path` join of a
    relative `output` deleted in write_deferred; failed at `assert code == 0 and
    target.is_file() and out["written"] == str(target)` (2 == 0: the cwd-relative path was
    refused as outside .claude/audits/).
    """
    closed_parent(git_repo, p3=3)
    monkeypatch.chdir(git_repo.parent)
    code, out, _ = _cli(capsys, "deferred", "--write", ".claude/audits/out/L1.json",
                        "--root", str(git_repo))
    target = git_repo / ".claude/audits/out/L1.json"
    assert code == 0 and target.is_file() and out["written"] == str(target)
    assert [f["id"] for f in json.loads(target.read_text(encoding="utf-8"))] == [
        "F-2", "F-3", "F-4"]


def test_deferred_write_with_an_empty_name_is_refused(capsys, git_repo):
    """`--write ""` reaches write_deferred, where confinement refuses it; it is not a report.

    Red 2026-10-11 (fix round 1): mutation `if a.write is not None:` became `if a.write:` in
    _pipeline; failed at `assert code == 2` (0 == 2: the plain report was printed).
    """
    closed_parent(git_repo, p3=3)
    code, out, err = _cli(capsys, "deferred", "--write", "", "--root", str(git_repo))
    assert code == 2 and "outside .claude/audits/" in err["reason"] and out == {}


@pytest.mark.parametrize("name", [
    ".claude/audits/AUDITOR_SCORECARD.jsonl", ".claude/audits/EXECUTION_LOG.md",
    ".claude/audits/run-state.json", ".claude/audits/runs/parent/L2.json",
    ".claude/audits/out/../run-state.json", ".claude/audits/outside.json",
])
def test_deferred_write_refuses_anything_outside_claude_audits_out(capsys, git_repo, name):
    """`--write` writes only under `.claude/audits/out/`, resolved. The rest of
    `.claude/audits/` holds the disposition ledger, the execution log, the run state and the
    stored runs, and an L1 file written over any of them destroys the record; each is refused
    by name, naming the allowed folder, and left as it was.

    Red 2026-10-12 (I4): mutation `_WRITE_DIR = f"{core.EXCLUDED}/out"` became
    `_WRITE_DIR = core.EXCLUDED` in pipeline_state (the allowlist widened to all of
    `.claude/audits/`); all six cases failed at `assert code == 2 and name in err["reason"] and
    ".claude/audits/out/" in err["reason"]` (0 == 2: each file was written with the pool, the
    ledger and the log included, since the allowlist is now the only guard).
    """
    closed_parent(git_repo, p3=3)
    target = git_repo / name
    before = target.read_bytes() if target.exists() else None
    code, out, err = _cli(capsys, "deferred", "--write", name, "--root", str(git_repo))
    assert code == 2 and name in err["reason"] and ".claude/audits/out/" in err["reason"]
    assert out == {}
    assert (target.read_bytes() if target.exists() else None) == before


def test_deferred_write_accepts_claude_audits_out_absolute_and_relative(capsys, git_repo):
    """The allowlist admits the one file D.1 writes, `.claude/audits/out/L1.json`, named
    absolutely and relative to the root.

    Red 2026-10-12 (I4), two mutations in write_deferred's module. `_WRITE_DIR =
    f"{core.EXCLUDED}/out"` became `_WRITE_DIR = f"{core.EXCLUDED}/in"`; failed at `assert code
    == 0 and out["written"] == str(target)` on the absolute case (2 == 0: refused as outside).
    `path = pathlib.Path(root) / path` became `pass` (a relative name read against the working
    directory, not the root); failed at `assert code == 0 and target.is_file()` on the relative
    case (2 == 0).
    """
    closed_parent(git_repo, p3=3)
    target = git_repo / ".claude/audits/out/L1.json"
    code, out, _ = _cli(capsys, "deferred", "--write", str(target), "--root", str(git_repo))
    assert code == 0 and out["written"] == str(target)
    target.unlink()
    code, out, _ = _cli(capsys, "deferred", "--write", ".claude/audits/out/L1.json",
                        "--root", str(git_repo))
    assert code == 0 and target.is_file()


def test_deferred_write_to_a_directory_is_refused_not_a_traceback(capsys, git_repo):
    """Red 2026-10-11: mutation `except OSError` became `except KeyError` around the write in
    write_deferred; failed at `assert code == 2 and "could not be written" in err["reason"]`
    ('escaped' == 2: the OSError got out of the CLI)."""
    closed_parent(git_repo, p3=3)
    target = git_repo / ".claude/audits/out"
    target.mkdir(parents=True)
    try:
        code, _, err = _cli(capsys, "deferred", "--write", str(target), "--root", str(git_repo))
    except OSError as exc:
        code, err = "escaped", {"reason": repr(exc)}
    assert code == 2 and "could not be written" in err["reason"]


def test_deferred_write_is_refused_when_the_pass_is_not_offered(capsys, git_repo):
    """Red 2026-10-11: mutation the `if not offer.offered:` check in write_deferred became
    `if False:`; failed at `assert code == 2 and "nothing was deferred" in err["reason"]`
    (0 == 2)."""
    closed_parent(git_repo, p3=0)
    code, _, err = _cli(capsys, "deferred", "--write",
                        str(git_repo / ".claude/audits/out/L1.json"), "--root", str(git_repo))
    assert code == 2 and "nothing was deferred" in err["reason"]


def test_deferred_write_on_an_empty_newest_run_points_at_the_other_pool(capsys, git_repo):
    """R10: an empty newest run must not dead-end `/audit-fix deferred` while another pool is
    on offer ("other", not "older": with `--run` naming a non-newest run the list can include
    newer ones). Red 2026-10-11: mutation the `+ older` suffix of the not-offered refusal in
    write_deferred deleted; failed at `assert "older pools still on offer: first" in
    err["reason"]` (the reason ended at "nothing was deferred"). Red 2026-10-11 (fix round 1):
    the same deletion, after the wording became "other pools"; failed at `assert "other pools
    still on offer: first" in err["reason"]`."""
    closed_parent(git_repo, run_id="first", p3=3)
    closed_parent(git_repo, run_id="second", p3=0)
    target = str(git_repo / ".claude/audits/out/L1.json")
    code, _, err = _cli(capsys, "deferred", "--write", target, "--root", str(git_repo))
    assert code == 2 and "nothing was deferred" in err["reason"]
    assert "other pools still on offer: first" in err["reason"]
    assert "pass --run first" in err["reason"]
    code, out, _ = _cli(capsys, "deferred", "--run", "first", "--write", target,
                        "--root", str(git_repo))
    assert code == 0 and out["run_id"] == "first" and out["written"] == target


def test_deferred_write_is_refused_while_a_summary_is_unreadable(capsys, git_repo):
    """R21(a): `start --mode DEFERRED` refuses while a summary cannot be read, so the writer
    refuses too, rather than hand over an L1 file the start will not accept. Red 2026-10-11:
    mutation the `if offer.unreadable:` refusal in write_deferred became `if False:`; failed at
    `assert code == 2 and err["refused"] == "deferred"` (0 == 2: the L1 file was written)."""
    closed_parent(git_repo, p3=3)
    broken = _corrupt_summary(git_repo)
    target = git_repo / ".claude/audits/out/L1.json"
    code, _, err = _cli(capsys, "deferred", "--write", str(target), "--root", str(git_repo))
    assert code == 2 and err["refused"] == "deferred"
    assert broken in err["reason"] and "block a deferred start" in err["reason"]
    assert not target.exists()


def test_deferred_report_still_prints_but_offers_nothing_while_a_summary_is_unreadable(
        capsys, git_repo):
    """R21(b), I5: the plain report is read-only and still prints, but `start --mode DEFERRED`
    refuses while a summary cannot be read, so the report must not offer or advise a pass: it
    says `offered: false`, no recommendation, and gives the unreadable files as its first
    reason, ahead of the run's own.

    Red 2026-10-12 (I5), three mutations in deferred_report. Its `if offer.unreadable:` became
    `if False:`; failed at `assert out["offered"] is False and out["recommendation"] is None`
    (True is False). `offer.offered = False` deleted;
    failed at `assert out["offered"] is False and out["recommendation"] is None` (True is
    False). `offer.reasons.insert(0, ...)` became `offer.reasons.append(...)`; failed at
    `assert "block a deferred start" in out["reasons"][0] and broken in out["reasons"][0]`
    (the first reason was the run's own RUN reason).
    """
    closed_parent(git_repo, p3=3)
    broken = _corrupt_summary(git_repo)
    code, out, _ = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 0 and out["run_id"] == "parent" and out["unreadable"] == [broken]
    assert out["offered"] is False and out["recommendation"] is None
    assert "block a deferred start" in out["reasons"][0] and broken in out["reasons"][0]
    assert any("P3 findings, at or above the threshold" in r for r in out["reasons"][1:])


def test_deferred_without_a_summary_exits_2(capsys, git_repo):
    """Red 2026-10-11: mutation `--from commit:<sha>` became `--from a sha` in the no-summary
    refusal in _deferred_context (audit-fix.md's D.1 routes on the phrase); failed at `assert
    code == 2 and "commit:<sha>" in err["reason"]` (2 == 2 and the phrase absent)."""
    code, _, err = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 2 and "commit:<sha>" in err["reason"]


def test_deferred_without_a_readable_summary_names_the_unreadable_files(capsys, git_repo):
    """Only unreadable summaries exist: the refusal names them and says to fix or move them
    aside, and does NOT offer the `commit:<sha>` fallback (D.1 routes on that phrase, and
    `start` refuses while any summary is unreadable).

    Red 2026-10-11 (fix round 1): mutation the `elif unreadable:` branch of _deferred_context
    deleted, so that the unreadable files fall to the plain no-summary refusal; failed at
    `assert code == 2 and broken in err["reason"] and "moved aside" in err["reason"]` (the
    path of the corrupt file was absent).
    Red 2026-10-11 (fix round 1): mutation `--from commit:<sha>` appended to the message of
    that branch; failed at `assert "commit:<sha>" not in err["reason"]`.
    """
    broken = _corrupt_summary(git_repo)
    code, _, err = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 2 and broken in err["reason"] and "moved aside" in err["reason"]
    assert "commit:<sha>" not in err["reason"]


def test_only_the_no_summary_refusal_names_the_commit_fallback(capsys, git_repo):
    """audit-fix.md D.1 takes the commit-message fallback exactly when a refusal contains
    `--from commit:<sha>`, so only the true no-summary refusal of `pipeline deferred` may hold
    that literal. An unknown `--run` (summaries exist) and a `start` naming an unknown parent
    are worded apart, and the unknown `--run` names the runs that do exist.

    Red 2026-10-12 (M7), against the code before the fix: the `start` refusal read "... is
    taken with --from commit:<sha>"; failed at `assert code == 2 and "no completed run
    'absent'" in err["reason"] and fallback not in err["reason"]`. Mutation the unknown `--run`
    message in _deferred_context worded as a no-summary refusal ("no completed run 'ghost' has
    a summary (completed runs: parent); for a run that predates summaries, start with --from
    commit:<sha>"); failed at `assert code == 2 and "'ghost'" in err["reason"] and fallback not in
    err["reason"]`.
    """
    fallback = "--from commit:<sha>"
    r = str(git_repo)
    code, _, err = _cli(capsys, "deferred", "--root", r)
    assert code == 2 and fallback in err["reason"]
    closed_parent(git_repo, p3=3)
    code, _, err = _cli(capsys, "deferred", "--run", "ghost", "--root", r)
    assert code == 2 and "'ghost'" in err["reason"] and fallback not in err["reason"]
    assert "completed runs: parent" in err["reason"]
    code, _, err = _cli(capsys, "start", "--tier", "STANDARD", "--mode", "DEFERRED",
                        "--from", "absent", "--root", r)
    assert code == 2 and "no completed run 'absent'" in err["reason"] \
        and fallback not in err["reason"]


def test_deferred_with_a_malformed_summary_is_refused_not_a_traceback(capsys, git_repo):
    """A pool holding a non-string, or a `human_pending` that is not an int, once escaped from
    `pipeline deferred` as a TypeError traceback; such a summary is now unreadable.

    Red 2026-10-11 (fix round 1): mutation the pool element test
    `not all(isinstance(i, str) for i in raw["pool"])` deleted in deferred.load_summaries;
    failed at `assert code == 2 and path.as_posix() in err["reason"]` on the pool case
    ('escaped' == 2: TypeError, unhashable type 'list', from findings_for). Mutation the
    `human_pending` type test deleted; failed at the same assert on the human_pending case
    ('escaped' == 2: TypeError from int(['x']) in assess).
    """
    closed_parent(git_repo, p3=3)
    path = git_repo / ".claude/audits/runs/parent/summary.json"
    original = json.loads(path.read_text(encoding="utf-8"))
    for over in ({"pool": ["F-2", ["F-3"]]}, {"human_pending": ["x"]}):
        path.write_text(json.dumps({**original, **over}), encoding="utf-8")
        try:
            code, _, err = _cli(capsys, "deferred", "--root", str(git_repo))
        except TypeError as exc:
            code, err = "escaped", {"reason": repr(exc)}
        assert code == 2 and path.as_posix() in err["reason"]


def test_deferred_names_a_run_directory_without_its_l2_file(capsys, git_repo):
    """Review Focus 1. Red 2026-10-11: mutation the `except (OSError, ValueError)` in
    deferred.findings_for returns [] instead of raising; failed at `assert code == 2 and
    "L2.json" in err["reason"]` (0 == 2: the report printed an empty pool)."""
    closed_parent(git_repo)
    (git_repo / ".claude/audits/runs/parent/L2.json").unlink()
    code, _, err = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 2 and "L2.json" in err["reason"]


def test_deferred_reports_a_parent_commit_that_left_history(capsys, git_repo):
    """Review Focus 5. Red 2026-10-11: mutation `reachable = True` in _deferred_context; failed
    at `assert code == 0 and out["commit_reachable"] is False` (True is False)."""
    import subprocess
    closed_parent(git_repo)
    subprocess.run(["git", "-C", str(git_repo), "reset", "-q", "--hard", "HEAD~1"], check=True)
    code, out, _ = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 0 and out["commit_reachable"] is False
    assert any("not an ancestor of HEAD" in r for r in out["reasons"])


def test_round_trip_from_closed_parent_to_a_started_deferred_run(capsys, git_repo):
    """Red 2026-10-11: mutation `parent=a.parent` dropped from the start call in _pipeline;
    failed at `assert code == 0 and started["started"]["parent"] == "parent"` on the start
    (2 == 0: needs --from). The write, start and record legs are chained so that the file
    `deferred --write` produces is the one `record L1` accepts."""
    r = str(git_repo)
    closed_parent(git_repo, p3=3)
    out_file = str(git_repo / ".claude/audits/out/L1.json")
    code, offer, _ = _cli(capsys, "deferred", "--write", out_file, "--root", r)
    assert code == 0 and offer["offered"]
    code, started, _ = _cli(capsys, "start", "--tier", "STANDARD", "--mode", "DEFERRED",
                            "--from", offer["run_id"], "--run-id", "child", "--root", r)
    assert code == 0 and started["started"]["parent"] == "parent"
    code, rec, _ = _cli(capsys, "record", "L1", "--detail", "loaded 3 from parent",
                        "--output", out_file, "--root", r)
    assert code == 0 and rec["output"] == ".claude/audits/runs/child/L1.json"


def _count_fingerprints(monkeypatch) -> list[int]:
    from cca_checks import pipeline_state

    calls = [0]
    real = pipeline_state.core.fingerprint

    def counted(root):
        calls[0] += 1
        return real(root)

    monkeypatch.setattr(pipeline_state.core, "fingerprint", counted)
    return calls


def test_status_fingerprints_the_tree_once(capsys, git_repo, monkeypatch):
    """`pipeline status` runs `git add -A` on a copied index once, not once per question.

    Invariant: one CLI call judges the run against one tree. Failure mode: status asked
    `verify_commit_allowed`, `outstanding` and `required_layers` separately, each of which
    fingerprinted the whole repository again (three `git add -A` runs per status).

    Red 2026-10-11: baseline 471030f; failed at `assert calls[0] == 1` (3 == 1).
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "STANDARD", "--root", r)
    calls = _count_fingerprints(monkeypatch)
    code, out, _ = _cli(capsys, "status", "--root", r)
    assert code == 0 and out["open"]
    assert calls[0] == 1


def test_record_fingerprints_the_tree_once(capsys, git_repo, monkeypatch):
    """`pipeline record` judges the record and the commit decision against one tree.

    Failure mode: `record` fingerprinted the tree, then the CLI's `verify_commit_allowed`
    fingerprinted it again, although recording only writes under `.claude/audits/`, which
    the fingerprint excludes.

    Red 2026-10-11: baseline 471030f; failed at `assert calls[0] == 1` (2 == 1).
    """
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    out_file = write_output(git_repo)
    calls = _count_fingerprints(monkeypatch)
    code, out, _ = _cli(capsys, "record", "L1", "--output", out_file, "--root", r)
    assert code == 0 and out["recorded"] == "L1"
    assert calls[0] == 1
def _through_l2_5(root) -> None:
    from cca_checks import pipeline_state as ps
    ps.start("STANDARD", root=root)
    ps.record("L1", output=write_output(root), root=root)
    ps.record("L2", counts={"unique": 0}, output=findings_output(root), root=root)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(root), root=root)


def test_scorecard_subcommand_records_l2_6_from_the_packaged_tool(capsys, git_repo,
                                                                   monkeypatch):
    """`python -m cca_checks scorecard --json --record` passes both flags to the tool.

    Red 2026-10-11: mutation `_run_tool` calls `module.main(args[:-1])` (drops the last
    argument, `--record`); failed at `assert "L2.6" in state.layers`.
    """
    from cca_checks import pipeline_state as ps
    _through_l2_5(git_repo)
    monkeypatch.chdir(git_repo)
    capsys.readouterr()
    assert main(["scorecard", "--json", "--record"]) == 0
    state = ps.load(git_repo)
    assert state is not None
    assert "L2.6" in state.layers
    assert state.layers["L2.6"]["source"] == "tool" and state.layers["L2.6"]["status"] == "done"
    assert "rows_scored" in json.loads(capsys.readouterr().out)


def test_tautology_subcommand_records_l5_6_with_no_proofs(capsys, git_repo, monkeypatch):
    """`python -m cca_checks tautology verify --no-proofs --record` records L5.6.

    Red 2026-10-11: mutation `_run_tool` calls `module.main(args[:-1])` (drops `--record`);
    failed at `assert "L5.6" in state.layers`.
    """
    from cca_checks import pipeline_state as ps
    _through_l2_5(git_repo)
    ps.record("L2.6", source="tool", root=git_repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(git_repo),
              root=git_repo)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(git_repo), root=git_repo)
    ps.record("L5", root=git_repo)
    monkeypatch.chdir(git_repo)
    assert main(["tautology", "verify", "--no-proofs", "--record"]) == 0
    state = ps.load(git_repo)
    assert state is not None
    assert "L5.6" in state.layers
    assert state.layers["L5.6"]["source"] == "tool"
    assert state.layers["L5.6"]["detail"] == "0 proofs claimed (no P1 to prove)"


def test_a_tool_subcommand_passes_the_tool_exit_code_through(capsys, git_repo, monkeypatch):
    """No run is open, so the scorecard cannot record L2.6 and exits 2; so must we.

    Red 2026-10-11: mutation `_run_tool` returns 0 after calling the tool; failed at
    `assert main(["scorecard", "--record"]) == 2` (0 == 2).
    """
    monkeypatch.chdir(git_repo)
    assert main(["scorecard", "--record"]) == 2
    assert "cannot record L2.6" in capsys.readouterr().err


def test_start_warns_about_stale_installed_copies_and_still_starts(capsys, git_repo,
                                                                    _private_user_claude_dir):
    """A project's or the user's stale pipeline file is named, with the command to refresh it.

    Red 2026-10-11: mutation `_stale_copy_warnings` returns `[]`; failed at
    `assert len(out["warnings"]) == 2`.
    """
    stale = git_repo / ".claude" / "commands" / "audit-fix.md"
    stale.parent.mkdir(parents=True)
    stale.write_text("# July's pipeline\n", encoding="utf-8")
    user_tool = _private_user_claude_dir / "tools" / "cca_scorecard.py"
    user_tool.parent.mkdir(parents=True)
    user_tool.write_text("print('old')\n", encoding="utf-8")
    code = main(["pipeline", "start", "--tier", "FAST", "--root", str(git_repo)])
    out_text, err = capsys.readouterr()
    out = json.loads(out_text)
    assert code == 0 and out["started"]["tier"] == "FAST"
    assert (git_repo / ".claude/audits/run-state.json").exists()
    assert len(out["warnings"]) == 2
    assert str(stale) in out["warnings"][0] and "cca-audit install --target" in out["warnings"][0]
    assert str(user_tool) in out["warnings"][1]
    assert err.count("warning: ") == 2


def test_start_does_not_warn_about_identical_or_absent_copies(capsys, git_repo):
    """An identical copy (even with CRLF endings) and an absent one are not warnings.

    Red 2026-10-11: mutation `_lf` returns its input unchanged (no CRLF normalisation);
    failed at `assert out["warnings"] == []`.
    """
    from cca_checks import plugin
    text = dict(plugin.iter_commands())["audit-fix.md"]
    same = git_repo / ".claude" / "commands" / "audit-fix.md"
    same.parent.mkdir(parents=True)
    same.write_bytes(text.replace("\n", "\r\n").encode("utf-8"))
    code = main(["pipeline", "start", "--tier", "FAST", "--root", str(git_repo)])
    out_text, err = capsys.readouterr()
    out = json.loads(out_text)
    assert code == 0
    assert out["warnings"] == []
    assert "warning" not in err
