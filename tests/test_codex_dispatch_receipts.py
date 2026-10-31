"""The Codex plaintext prompt gate is explicit about its attestation boundary."""

import json

import pytest
from conftest import findings_output, verdicts_output, write_output

from cca_checks import dispatch_receipts as dr
from cca_checks import pipeline_state as ps
from cca_checks.__main__ import main

ROLES = ["code_auditor", "bug_auditor", "security_auditor"]
BLOCK = "LOCAL AUDITOR MEMORY\nBUG cca-audit:abc:NUM-001 [exact] app.py run: historical bug\n"


def prepared(repo, mode="DIFF"):
    state = ps.start("FAST", mode=mode, no_fix=True, client="codex", auditors=ROLES, root=repo)
    run = repo / ".claude/audits/runs" / state.run_id
    (run / ps.LOCAL_PRIOR).write_text(json.dumps({"status": "ok", "block": BLOCK}),
                                       encoding="utf-8")
    prompt = repo / ".claude/audits/prompt.txt"
    prompt.write_text("Audit the diff.\n" + BLOCK, encoding="utf-8")
    if mode == "HUNT":
        ps.record("L0.4", detail="PASS", root=repo)
    return state, run, prompt


@pytest.mark.parametrize("mode", ["DIFF", "HUNT"])
def test_l1_requires_every_declared_dispatch(git_repo, mode):
    """Red 2026-10-28: replace dispatch_receipts.require(...) with None in record.

    Intended failure: DID NOT RAISE RecordRefused for an unchecked final auditor.
    This checks coverage of every declared role even when the brief exists.
    """
    _, _, prompt = prepared(git_repo, mode)
    for role in ROLES[:-1]:
        dr.check(role, str(prompt), git_repo)
    with pytest.raises(ps.RecordRefused, match="check-dispatch receipts"):
        ps.record("L1", output=write_output(git_repo), root=git_repo)


@pytest.mark.parametrize("prompt_text", ["Audit the diff.", "LOCAL AUDITOR MEMORY\n"])
def test_cli_checks_the_shared_prompt_rules(git_repo, capsys, prompt_text):
    """Red 2026-10-28: replace core.dispatch_reasons(...) in dispatch_receipts.check with [].

    Intended failure: assert code == 2, actual 0, for absent heading or missing id.
    The CLI must refuse before it writes a receipt.
    """
    _, run, prompt = prepared(git_repo)
    prompt.write_text(prompt_text, encoding="utf-8")
    code = main(["pipeline", "check-dispatch", "--role", "bug_auditor",
                 "--prompt-file", str(prompt), "--root", str(git_repo)])
    assert code == 2
    assert json.loads(capsys.readouterr().err)["refused"] == "check-dispatch"
    assert not (run / "dispatch-bug-auditor.json").exists()


@pytest.mark.parametrize("changed", ["prompt", "brief", "tree", "run", "role"])
def test_l1_rejects_changed_dispatch_inputs(git_repo, changed):
    """Red 2026-10-28: replace the final mismatch condition in dispatch_receipts.require with False.

    Intended failure: DID NOT RAISE RecordRefused after changing prompt bytes, brief
    bytes, source tree, receipt run identity or receipt role. The old receipt survives.
    """
    _, run, prompt = prepared(git_repo)
    for role in ROLES:
        dr.check(role, str(prompt), git_repo)
    if changed == "prompt":
        prompt.write_text(prompt.read_text(encoding="utf-8") + "changed", encoding="utf-8")
    elif changed == "brief":
        with (run / ps.LOCAL_PRIOR).open("a", encoding="utf-8") as stream:
            stream.write("\n")
    elif changed == "tree":
        (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    else:
        path = run / "dispatch-code-auditor.json"
        receipt = json.loads(path.read_text(encoding="utf-8"))
        receipt["run_id" if changed == "run" else "role"] = "different"
        path.write_text(json.dumps(receipt), encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="stale or mismatched"):
        ps.record("L1", output=write_output(git_repo), root=git_repo)


def test_codex_cli_persists_scope_and_closes_with_brief_ids(git_repo, capsys):
    """Red 2026-10-28: replace client=a.client with client=None at the CLI start boundary.

    Intended failure: assert start code == 0, actual 2, because auditor flags need
    the Codex client. Green also exercises serialized scope and the real close summary.
    """
    args = ["pipeline", "start", "--tier", "FAST", "--client", "codex", "--no-fix",
            "--root", str(git_repo)]
    for role in ROLES:
        args += ["--auditor", role]
    assert main(args) == 0
    capsys.readouterr()
    state = ps.load(git_repo)
    assert state.client == "codex" and state.auditors == [r.replace("_", "-") for r in ROLES]
    run = git_repo / ".claude/audits/runs" / state.run_id
    (run / ps.LOCAL_PRIOR).write_text(json.dumps({"status": "ok", "block": BLOCK}),
                                       encoding="utf-8")
    prompt = git_repo / ".claude/audits/prompt.txt"
    prompt.write_text(BLOCK, encoding="utf-8")
    for role in ROLES:
        assert main(["pipeline", "check-dispatch", "--role", role,
                     "--prompt-file", str(prompt), "--root", str(git_repo)]) == 0
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.close(git_repo)
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["client"] == "codex" and summary["auditors"] == state.auditors
    assert summary["prior_block"]["exact"] == 1
    assert summary["prior_brief"]["ids"] == ["cca-audit:abc:NUM-001"]


@pytest.mark.parametrize("roles", [[], ["bug_auditor"], ROLES + ["bug_auditor"]])
def test_codex_start_requires_a_complete_unique_core_scope(git_repo, roles):
    """Red 2026-10-28: replace the scope validation call in pipeline_state.start with auditors or [].

    Intended failure: DID NOT RAISE RecordRefused for missing, incomplete or duplicate
    core dispatch lists. An empty declared scope must not satisfy all receipts vacuously.
    """
    with pytest.raises(ps.RecordRefused, match="auditor"):
        ps.start("FAST", client="codex", auditors=roles, root=git_repo)


def test_external_prompt_is_refused(git_repo):
    """Red 2026-10-28: replace _prompt's containment check with False.

    Intended failure: DID NOT RAISE ValueError when a correctly briefed prompt lives
    outside the checkout audit directory. Keep preparation outside the source tree.
    """
    prepared(git_repo)
    prompt = git_repo / "prompt.txt"
    prompt.write_text(BLOCK, encoding="utf-8")
    with pytest.raises(ValueError, match="prompt-file must stay"):
        dr.check("bug_auditor", str(prompt), git_repo)
