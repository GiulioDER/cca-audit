"""The prose of the fix audit layer L5.4 must ask for exactly what its code enforces.

Agents follow audit-fix.md (and the Codex SKILL.md, which projects it); the gate and
`fixaudit record` enforce the code. A drift between them either fails every run or hides a step.

Red proof (run 2026-11-22, before the L5.4 prose existed, at abdb25d): `python -m pytest
tests/test_fixaudit_prose.py -q -p no:cacheprovider` gave every test failed on its `assert`
(AssertionError, no collection or fixture error). The production target is audit-fix.md (the
Contract row, Steps 4, 5, 5.4, 5.5, 5.6 and 6, the tier table, D.5), the Codex SKILL.md, and the
agent files cca-fp-check.md, cca-differential-review.md and cca-architect-reviewer.md.
"""

from __future__ import annotations

import contextlib
import io
import pathlib
import re

import pytest

from cca_checks import fixaudit, fixaudit_record, layer_outputs
from cca_checks.plugin.hooks import cca_gate_core as core

ROOT = pathlib.Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "cca_checks" / "plugin"
PIPELINE = (PLUGIN / "commands" / "audit-fix.md").read_text(encoding="utf-8")
CODEX = (PLUGIN / "codex" / "cca-audit" / "SKILL.md").read_text(encoding="utf-8")
FP_CHECK = (PLUGIN / "agents" / "cca-fp-check.md").read_text(encoding="utf-8")
DIFF_REVIEW = (PLUGIN / "agents" / "cca-differential-review.md").read_text(encoding="utf-8")
ARCHITECT = (PLUGIN / "agents" / "cca-architect-reviewer.md").read_text(encoding="utf-8")


def _section(text: str, start: str, end: str) -> str:
    i = text.find(start)
    assert i >= 0, f"no {start!r}"
    j = text.find(end, i + len(start))
    return text[i:j if j >= 0 else len(text)]


def _flat(text: str) -> str:
    return " ".join(text.split())


STEP_54 = "## Step 5.4"


def _step_54() -> str:
    return _section(PIPELINE, STEP_54, "## Step 5.5")


@pytest.mark.parametrize("name,text", [("audit-fix.md", PIPELINE), ("SKILL.md", CODEX)],
                         ids=["audit-fix.md", "SKILL.md"])
def test_both_clients_name_step_54_and_its_record_command(name, text):
    """Invariant: both clients tell the agent to run Step 5.4 and record it with its tool.
    Failure caught: a client that never mentions L5.4 reaches L5.5, whose record the gate then
    refuses (L5.5 needs L5.4), or Codex reports a run its status check refuses."""
    assert "Step 5.4" in text, name
    assert "fixaudit check" in text, name
    assert "fixaudit record" in text, name


def test_the_heading_the_gate_names_exists():
    """`record` and `resume` send the agent to `core.HEADINGS["L5.4"]` of audit-fix.md, so that
    heading must open a section, between Step 5 and Step 5.5."""
    assert core.HEADINGS["L5.4"] == "Step 5.4"
    assert re.search(r"^## Step 5\.4: ", PIPELINE, re.M)
    assert 0 <= PIPELINE.find("## Step 5: ") < PIPELINE.find(STEP_54) < PIPELINE.find("## Step 5.5")


def test_the_contract_table_lists_l54_between_l5_and_l55():
    """The Contract is the run's checklist: L5.4 sits between rows 5 and 5.5 and names the
    tool that records it (the gate refuses a hand recorded L5.4)."""
    contract = _section(PIPELINE, "## The Contract", "## Arguments")
    rows = [ln for ln in contract.splitlines() if ln.startswith("| ")]
    order = [ln.split("|")[1].strip() for ln in rows]
    assert "5.4" in order, order
    assert order.index("5") < order.index("5.4") < order.index("5.5")
    row = rows[order.index("5.4")]
    assert core.TOOL_LAYERS["L5.4"] in row


def test_step_5_ends_with_the_re_read_cue():
    """The point where the tests go green is where runs stop early; Step 5 must send the agent
    to Step 5.4 before anything else."""
    step5 = _flat(_section(PIPELINE, "## Step 5: ", STEP_54))
    assert ("the tests went green; the fix code has not been audited yet: re-read § Step 5.4"
            in step5)
    step4 = _flat(_section(PIPELINE, "## Step 4:", "## Step 5: "))
    assert "§ Step 5.4" in step4


def test_step_4_documents_the_l4_fields_the_fix_audit_reads():
    """`fixaudit check` and `record` read these fields from the stored L4 file; an agent that
    never heard of them is refused with no idea what to write."""
    step4 = _section(PIPELINE, "## Step 4:", "## Step 5: ")
    for field in ("fix_id", "finding", "files", "sibling_patterns", "sibling_reason",
                  "failure_test", "size_reason"):
        assert f"`{field}`" in step4, field
    assert "`re:`" in step4
    assert "record L4 again" in _flat(step4)


def test_step_54_names_the_files_and_paths_the_tool_reads():
    step = _step_54()
    for path in (fixaudit_record.SIBLINGS.as_posix(), fixaudit_record.AUDIT.as_posix(),
                 fixaudit.REPORT, fixaudit_record.JOURNAL):
        assert path in step, path
    assert "--audit .claude/audits/out/L5.4.json" in step
    assert "FA-<k>-<n>" in step
    assert "`resolves`" in step
    assert '"not_applicable"' in step


def test_step_54_lists_every_fix_audit_verdict_and_the_disposition_rules():
    """The verdict file is refused for any verdict outside FA_VERDICTS, and a sibling hit for
    any disposition other than not_applicable with a reason."""
    step = _flat(_step_54())
    for verdict in layer_outputs.FA_VERDICTS:
        assert f"`{verdict}`" in step, verdict
    assert f"at most {fixaudit.MAX_AUDITORS}" in step
    assert f"over {fixaudit.MAX_HITS_PER_PATTERN}" in step
    assert "`too_broad`" in step
    assert "`bug-auditor:DAT`" in step
    assert "`delta_hunks`" in step


def _help(cmd: str) -> str:
    out = io.StringIO()
    with contextlib.redirect_stdout(out), pytest.raises(SystemExit):
        fixaudit_record.main([cmd, "--help"])
    return out.getvalue()


def test_every_fixaudit_flag_the_prose_uses_exists():
    """Each `python -m cca_checks fixaudit <cmd> --flag` in Step 5.4 is a real option of that
    subcommand, and the journal command names every required one."""
    step = _step_54().replace("\\\n", " ")  # a shell line continuation joins its two lines
    commands = re.findall(r"python -m cca_checks fixaudit (check|journal|record)([^\n`]*)", step)
    assert {c for c, _ in commands} == {"check", "journal", "record"}
    for cmd, rest in commands:
        known = _help(cmd)
        for flag in re.findall(r"(--[a-z-]+)", rest):
            assert flag in known, (cmd, flag)
    journal = next(rest for cmd, rest in commands if cmd == "journal")
    for flag in ("--finding", "--fix-id", "--pass", "--verdict", "--reason"):
        assert flag in journal, flag
    assert "--verdict PARKED" in step
    assert f"{fixaudit_record.CAP}" in step


def test_red_proof_of_a_fix_audit_fix_uses_its_pass_snapshot():
    """A fix audit repair is proved against the tree its pass audited, never the L4 snapshot,
    where the bug did not exist and every honest test reads TAUTOLOGICAL."""
    snap = ".claude/audits/.fixaudit_snapshot/pass-<k>"
    step = _flat(_step_54())
    assert f"tautology snapshot --snapdir {snap}" in step
    step56 = _flat(_section(PIPELINE, "## Step 5.6", "## Step 6"))
    assert f"--proof FA-<k>-<n>=<nodeid>@{snap}" in step56


def test_fast_runs_the_checks_only():
    tiers = _section(PIPELINE, "**What each tier runs:**", "**No finding is ever edited")
    row = next((ln for ln in tiers.splitlines() if ln.startswith("| L5.4")), "")
    cells = [c.strip() for c in row.split("|")[1:-1]]
    assert len(cells) == 4, row
    assert "fixaudit check" in cells[1] and "--audit" not in cells[1]
    assert "auditor" in cells[2] and "auditor" in cells[3]


def test_regression_diff_and_architect_take_the_sibling_list_as_in_scope():
    """A sibling edit is part of its fix: L5.5 must not call it SCOPE_CREEP, nor L6 a phantom fix."""
    for name, text in (
        ("Step 5.5", _section(PIPELINE, "## Step 5.5", "## Step 5.6")),
        ("Step 6", _section(PIPELINE, "## Step 6", "## Step 7")),
        ("differential-review", DIFF_REVIEW),
        ("architect-reviewer", ARCHITECT),
    ):
        flat = _flat(text)
        assert "sibling" in flat, name
        assert "in scope" in flat, name
    assert "not SCOPE_CREEP" in _flat(DIFF_REVIEW)
    assert "not a phantom fix" in _flat(ARCHITECT)
    assert "`size_reason`" in DIFF_REVIEW


def test_the_deferred_pass_owes_l54():
    second = _section(PIPELINE, "## Second Pass", "**D.6 Step 5.6")
    d5 = _flat(second[second.find("**D.5 Steps"):])
    assert "Step 5.4" in d5


def test_fp_check_documents_the_resolved_verdict():
    """fp-check is the verifier of the fix audit pass; it must know MODE=FIXAUDIT, emit
    RESOLVED with the id it resolves, and OUT_OF_SCOPE for findings outside the fix hunks."""
    status = _section(FP_CHECK, "## Status Block", "## Input")
    assert "FIXAUDIT" in status
    assert "MODE=FIXAUDIT" in _section(FP_CHECK, "## Input", "## Verdict per Finding")
    verdicts = _section(FP_CHECK, "Emit exactly one verdict", "## Deterministic-first")
    assert "**RESOLVED** *(MODE=FIXAUDIT only)*" in verdicts
    assert "**OUT_OF_SCOPE** *(MODE=FIXAUDIT only)*" in verdicts
    assert "`resolves`" in verdicts
    description = next(ln for ln in FP_CHECK.splitlines() if ln.startswith("description:"))
    assert "RESOLVED" in description
    output_format = _section(FP_CHECK, "## Output Format", "## Execution Logging")
    assert re.search(r"^\| FA-\d+-\d+ \| RESOLVED \| ", output_format, re.M)
    rule = FP_CHECK[FP_CHECK.find("**Artifact-or-UNCERTAIN rule:**"):].split("\n\n")[0]
    assert "`RESOLVED`" in rule


def test_codex_writes_the_l54_files_and_queues_the_auditors():
    """Codex omits the optional trail, but `fixaudit record` reads the sibling dispositions and
    the verdict file, so the adapter must say they are written; and its auditors go through the
    six slot queue like any other dispatch."""
    flat = _flat(CODEX)
    assert ".claude/audits/out/L5.4-siblings.json" in flat
    assert ".claude/audits/out/L5.4.json" in flat
    assert "MODE=FIXAUDIT" in flat
    apply = _flat(_section(CODEX, "## Apply fixes safely", "## Gate before reporting"))
    assert "Step 5.4" in apply and "six slot queue" in apply
