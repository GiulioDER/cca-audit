"""L1 is refused until Step 1 has built the prior-bugs brief, in a DIFF or HUNT run only.

Measured 2026-10-27 over the previous 48 hours: 13 Claude Code runs skipped the Step 1 brief
command while every Codex run built it, and nothing noticed, because the step was prose. The
gate reads the run directory for either file Step 1 leaves: `local-prior.json` (the receipt of
`bugs brief --out`) or `prior-records.json` (the hosted answer). The conftest gives every
started run the receipt, so each test here deletes it first.
"""

from __future__ import annotations

import json
import pathlib

import pytest
from conftest import closed_parent, pool_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.__main__ import main


def _run_dir(repo, state) -> pathlib.Path:
    return pathlib.Path(repo) / ".claude" / "audits" / "runs" / state.run_id


def _without_brief(repo, state) -> pathlib.Path:
    run = _run_dir(repo, state)
    (run / ps.LOCAL_PRIOR).unlink()
    return run


@pytest.mark.parametrize("mode", ["DIFF", "HUNT"])
def test_l1_is_refused_without_a_brief_and_names_the_command(git_repo, mode):
    """Invariant: a DIFF or HUNT run's L1 is refused while its run directory holds neither
    brief file, and the refusal names the `bugs brief` command and this run's receipt path.

    Red 2026-10-27, both modes: mutation deleting the call `_check_prior_brief(state, root)`
    in `pipeline_state.record`; failed at `pytest.raises(ps.RecordRefused)` (DID NOT RAISE).
    """
    state = ps.start("DEEP", mode=mode, root=git_repo)
    _without_brief(git_repo, state)
    if mode == "HUNT":
        ps.record("L0.4", detail="PASS", root=git_repo)
    with pytest.raises(ps.RecordRefused) as refused:
        ps.record("L1", output=write_output(git_repo), root=git_repo)
    reason = str(refused.value)
    assert "python -P -m cca_checks bugs brief" in reason
    assert f".claude/audits/runs/{state.run_id}/local-prior.json" in reason
    assert "L1" not in ps.load(git_repo).layers


def test_the_local_receipt_lets_l1_record(git_repo):
    """Invariant: `local-prior.json` alone satisfies the gate (the local reader path).

    Red 2026-10-27: mutation in `_check_prior_brief`, the test reads only
    `(run / PRIOR_RECORDS).is_file()`; failed with RecordRefused "L1 needs the prior-bugs
    brief first" raised from `ps.record("L1", ...)`.
    """
    state = ps.start("STANDARD", root=git_repo)
    run = _without_brief(git_repo, state)
    (run / ps.LOCAL_PRIOR).write_text(
        json.dumps({"status": "empty", "block": ""}) + "\n", encoding="utf-8")
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    assert "L1" in ps.load(git_repo).layers


def test_the_hosted_answer_lets_l1_record(git_repo):
    """Invariant: `prior-records.json` alone satisfies the gate (the hosted path).

    Red 2026-10-27: mutation in `_check_prior_brief`, the test reads only
    `(run / LOCAL_PRIOR).is_file()`; failed with RecordRefused "L1 needs the prior-bugs
    brief first" raised from `ps.record("L1", ...)`.
    """
    state = ps.start("STANDARD", root=git_repo)
    run = _without_brief(git_repo, state)
    (run / ps.PRIOR_RECORDS).write_text("{}\n", encoding="utf-8")
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    assert "L1" in ps.load(git_repo).layers


def test_a_deferred_pass_records_l1_without_a_brief(git_repo):
    """Invariant: a DEFERRED run dispatches no L1 auditors, so its L1 needs no brief.

    Red 2026-10-27: mutation in `_check_prior_brief`, the mode test
    `if state.mode not in ("DIFF", "HUNT"):` became `if False:`; failed with RecordRefused
    "L1 needs the prior-bugs brief first" raised from `ps.record("L1", ...)`.
    """
    closed_parent(git_repo)
    child = ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="child",
                     root=git_repo)
    _without_brief(git_repo, child)
    l2, _ = pool_output(git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    assert "L1" in ps.load(git_repo).layers
    assert l2  # the parent's pool exists, so this is a real deferred pass, not an empty one


def test_the_cli_refuses_l1_with_exit_2_and_the_command(capsys, git_repo):
    """Invariant: `pipeline record L1` without a brief exits 2, naming the command on stderr.

    Red 2026-10-27: mutation deleting the call `_check_prior_brief(state, root)` in
    `pipeline_state.record`; failed at `assert code == 2` (0 == 2).
    """
    r = str(git_repo)
    main(["pipeline", "start", "--tier", "STANDARD", "--root", r])
    capsys.readouterr()
    _without_brief(git_repo, ps.load(git_repo))
    code = main(["pipeline", "record", "L1", "--output", write_output(r), "--root", r])
    stderr = capsys.readouterr().err
    assert code == 2
    err = json.loads(stderr)
    assert err["refused"] == "L1" and "bugs brief" in err["reason"]
