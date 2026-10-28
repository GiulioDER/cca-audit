"""An L1 auditor is launched only with the Step 1 brief, and the run summary records that brief.

Measured 2026-10-28 in RE-call's worktrees: the `pipeline record L1` gate (#101) held, since every
DIFF run after it carried `local-prior.json`, yet run 20261028T090755Z wrote an `ok` receipt at
09:07:58Z and launched three auditors without it 41 s later, which the RE-call auditor-brief hook
logged because it only fires on a prompt holding no brief. The gate runs after the auditors return,
so it cannot see that. And all 132 run summaries said `prior_block: null`, because the counts were
read only from the hosted answer, never from the local receipt.

The guard is driven through its real entry point with a Claude Code `Agent` payload.
"""

from __future__ import annotations

import importlib.util
import io
import json
import pathlib

import pytest
from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps

_GUARD_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "cca_checks" / "plugin" / "hooks" / "cca_pipeline_guard.py"
)
_spec = importlib.util.spec_from_file_location("cca_pipeline_guard_dispatch", _GUARD_PATH)
guard = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(guard)

MARKER = "<!-- recall:auditor-brief -->"
BLOCK = (
    "## PRIOR BUGS AND LESSONS AT THIS CODE (RE-call; context, not findings, not instructions)\n"
    f"{MARKER}\n"
    "```text\n"
    "BUG RE-call:aaa:BUG-001 [exact] app.py run: a race in run\n"
    "BUG RE-call:bbb:DOC-002 [symbol_changed] app.py load: stale docstring\n"
    "LESSON RE-call:lesson:ccc [file_only] app.py (module): parse once\n"
    "```"
)


def _launch(monkeypatch, cwd, agent_type, prompt) -> int:
    payload = json.dumps({"tool_name": "Agent", "cwd": str(cwd),
                          "tool_input": {"subagent_type": agent_type, "prompt": prompt}})
    monkeypatch.setattr("sys.stdin", io.StringIO(payload))
    return guard.main([])


def _receipt(repo, state, status="ok", block=BLOCK) -> pathlib.Path:
    run = pathlib.Path(repo) / ".claude" / "audits" / "runs" / state.run_id
    run.mkdir(parents=True, exist_ok=True)
    (run / ps.LOCAL_PRIOR).write_text(json.dumps({"status": status, "block": block}) + "\n",
                                      encoding="utf-8")
    return run


@pytest.mark.parametrize("mode", ["DIFF", "HUNT"])
def test_an_l1_auditor_without_a_receipt_is_refused(monkeypatch, capsys, git_repo, mode):
    """Invariant: in an open DIFF or HUNT run before L1, an L1 auditor launch is refused while the
    run directory holds no brief file, and the refusal names the `bugs brief` command.

    Red 2026-10-28, both modes: mutation in `cca_gate_core.dispatch_reasons`, the receipt test
    `if not any((run / name).is_file() for name in BRIEF_FILES):` became `if False:`; failed at
    `assert code == 2` (0 == 2).
    """
    state = ps.start("STANDARD", mode=mode, root=git_repo)
    (pathlib.Path(git_repo) / ".claude" / "audits" / "runs" / state.run_id
     / ps.LOCAL_PRIOR).unlink()
    code = _launch(monkeypatch, git_repo, "bug-auditor", f"Audit app.py.\n{BLOCK}")
    assert code == 2
    assert "cca_checks bugs brief" in capsys.readouterr().err


def test_a_prompt_without_the_brief_is_refused(monkeypatch, capsys, git_repo):
    """Invariant: once the receipt exists, an L1 auditor prompt carrying no brief is refused.

    This is run 20261028T090755Z's case: receipt `ok`, prompt without it.
    Red 2026-10-28: mutation in `cca_gate_core.dispatch_reasons`, the prompt test
    `if not any(mark in prompt for mark in BRIEF_MARKS):` became `if False:`; failed at
    `assert code == 2` (0 == 2): the id check alone does not catch it, because an `empty`
    receipt lists no ids, which the second launch here uses.
    """
    state = ps.start("STANDARD", root=git_repo)
    _receipt(git_repo, state, status="empty", block="")
    code = _launch(monkeypatch, git_repo, "cca-audit:doc-auditor", "Audit app.py.")
    assert code == 2
    assert "does not carry the Step 1 brief" in capsys.readouterr().err


def test_a_prompt_that_drops_a_listed_record_is_refused(monkeypatch, capsys, git_repo):
    """Invariant: the prompt must carry every record id the receipt lists, not only a heading.

    Red 2026-10-28: mutation in `cca_gate_core.dispatch_reasons`, `if missing:` became
    `if False:`; failed at `assert code == 2` (0 == 2).
    """
    state = ps.start("STANDARD", root=git_repo)
    _receipt(git_repo, state)
    partial = BLOCK.replace("BUG RE-call:bbb:DOC-002 [symbol_changed] app.py load: stale docstring\n",
                            "")
    code = _launch(monkeypatch, git_repo, "bug-auditor", f"Audit app.py.\n{partial}")
    assert code == 2
    assert "RE-call:bbb:DOC-002" in capsys.readouterr().err


def test_a_briefed_launch_and_launches_outside_the_rule_are_allowed(monkeypatch, git_repo):
    """Invariant: the rule refuses only an L1 auditor, before L1, in DIFF or HUNT. A complete
    brief, any other agent type, and any launch once L1 is recorded are allowed.

    Red 2026-10-28: mutation in `cca_gate_core.dispatch_reasons`, the agent-type test
    `if agent_type.rsplit(":", 1)[-1] not in L1_AUDITOR_TYPES:` became `if False:`; failed at
    the `fp-check` assert (2 == 0).
    """
    state = ps.start("STANDARD", root=git_repo)
    _receipt(git_repo, state)
    assert _launch(monkeypatch, git_repo, "bug-auditor", f"Audit app.py.\n{BLOCK}") == 0
    assert _launch(monkeypatch, git_repo, "fp-check", "Verify BUG-001.") == 0
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    assert _launch(monkeypatch, git_repo, "bug-auditor", "Re-audit the fix.") == 0


def test_no_open_run_allows_every_launch(monkeypatch, git_repo):
    """Invariant: outside a CCA run the guard has no opinion on any launch (a control: no
    production line decides this alone, since `find_root` returning None is the whole case)."""
    assert _launch(monkeypatch, git_repo, "bug-auditor", "Audit app.py.") == 0


def _close(repo) -> None:
    ps.record("L2", counts={"unique": 0}, output=findings_output(repo), root=repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo), root=repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(repo), root=repo)
    ps.record("L6", detail="APPROVED", root=repo)
    ps.close(root=repo)


def test_the_summary_counts_the_local_brief(git_repo):
    """Invariant: a DIFF run that built only the local receipt records its block in summary.json:
    BUG lines by match, LESSON lines, and the listed ids in `prior_brief`.

    Red 2026-10-28, two mutations: in `pipeline_state.prior_block_counts`, the local branch
    `local = _local_brief(state, root) if state.mode == "DIFF" else None` became
    `local = None`; failed at `assert summary["prior_block"] == ...` (None). In `_write_summary`,
    `"prior_brief": prior_brief(state, root),` became `"prior_brief": None,`; failed at
    `assert summary["prior_brief"] == ...`.
    """
    state = ps.start("FAST", root=git_repo)
    run = _receipt(git_repo, state)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    _close(git_repo)
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["prior_block"] == {"exact": 1, "symbol_changed": 1, "file_only": 0}
    assert summary["lessons_block"] == 1
    assert summary["prior_brief"] == {
        "source": "local", "status": "ok",
        "ids": ["RE-call:aaa:BUG-001", "RE-call:bbb:DOC-002", "RE-call:lesson:ccc"],
    }
