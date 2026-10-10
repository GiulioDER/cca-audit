"""The pipeline text must ask for exactly what the gate enforces.

Red at d2317de: every test here fails on its assertion against the 0.10.1 text.

Red proof (run 2026-10-10): this file was copied to ../cca-baseline (d2317de) and run with
`python -m pytest tests/test_pipeline_prose.py -q -p no:cacheprovider`. Result: 7 failed, each on an
`assert` (AssertionError, no ValueError, no collection error). The production target is the prose of
cca_checks/plugin/commands/audit-fix.md and cca_checks/plugin/codex/cca-audit/SKILL.md; at d2317de
they lack the 0.4 row, still say `pipeline record L2.6` and `"run_id": <short>`, omit the count flags,
do not close a no-fix run, do not open a hunt at Step 0.4, and the Codex skill has no
`pipeline status --require-complete`. The copy was deleted afterwards (baseline status clean).
"""

from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
PIPELINE = (ROOT / "cca_checks/plugin/commands/audit-fix.md").read_text(encoding="utf-8")
CODEX = (ROOT / "cca_checks/plugin/codex/cca-audit/SKILL.md").read_text(encoding="utf-8")


def test_contract_lists_l0_4():
    assert re.search(r"^\| 0\.4 \|", PIPELINE, re.M)


def test_tool_layers_are_never_recorded_by_hand():
    assert "pipeline record L2.6" not in PIPELINE
    assert "pipeline record L5.6" not in PIPELINE
    assert "--json --record" in PIPELINE
    assert "verify --no-proofs --record" in PIPELINE


def test_count_flags_appear_where_the_gate_requires_them():
    assert "pipeline record L2 --unique" in PIPELINE
    assert "--confirmed" in PIPELINE and "--duplicate" in PIPELINE
    assert "pipeline record L3 --to-fix" in PIPELINE


def test_ledger_run_id_is_the_pipeline_run_id():
    assert '"run_id": <short>' not in PIPELINE
    assert "the `run_id` printed by `pipeline start`" in PIPELINE


def test_no_fix_runs_close_after_recording_l2_5():
    # find(), not index(): at d2317de the red must be this assertion, not a ValueError.
    section = PIPELINE[PIPELINE.find("### Step 2.5b"):PIPELINE.find("## Step 2.6")]
    record_at = section.find("pipeline record L2.5")
    stop_at = section.find("If `no-fix`")
    assert 0 <= record_at < stop_at
    assert "pipeline close" in section[stop_at:]


def test_hunt_opens_the_run_at_step_0_4():
    section = PIPELINE[PIPELINE.find("## Step 0.4"):PIPELINE.find("## Step 0.5")]
    assert "pipeline start --tier DEEP --mode HUNT" in section
    assert "pipeline record L0.4" in section


def test_codex_checks_completeness_before_reporting():
    assert "pipeline status --require-complete" in CODEX


def test_step_2_5_never_tells_the_agent_to_skip_a_required_layer():
    """Round 1 fix. Invariant: the prose must not tell the agent to skip L2.5 on an empty list.

    The gate refuses every later layer without L2.5, so an empty run records zero counts.
    Red receipt: run before the edit against cb62417, where the section read "skip it" and had
    no `--confirmed 0`; the failure is on the first assertion below.
    Round 2 (the gate requires L2.5 at every tier; FAST runs a P1 only fp-check): against de34cde
    the section still said "FAST tier skips this layer" and its heading ended in STANDARD/DEEP,
    so the "skips this layer" assertion fails (receipt: pytest output quoted in task-7-report.md).
    """
    section = PIPELINE[PIPELINE.find("## Step 2.5:"):PIPELINE.find("### Step 2.5b")]
    assert section
    assert "skip it" not in section
    assert "skips this layer" not in section
    assert not section.splitlines()[0].rstrip().endswith("STANDARD/DEEP")
    assert "--confirmed 0" in section


def test_no_fix_argument_line_names_l2_6_and_the_close():
    """Round 1 fix. Red receipt: against cb62417 the line read "run L1→L2.5" with no L2.6."""
    lines = PIPELINE.splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("- `no-fix` ="))
    bullet = " ".join(lines[start:start + 2])  # the bullet wraps onto one continuation line
    assert "L2.6" in bullet
    assert "closes the run" in bullet


def test_hunt_start_passes_no_fix_because_step_0_6_is_skipped():
    """A hunt reports, it does not fix, and Step 0.6 (where `--no-fix` is normally passed) is
    skipped for it, so the Step 0.4 start and the 0.4 Contract row must carry `--no-fix`.

    Red 2026-10-10: baseline a8d7445 text; failed at
    `assert "pipeline start --tier DEEP --mode HUNT [--no-fix]" in section`.
    """
    section = PIPELINE[PIPELINE.find("## Step 0.4"):PIPELINE.find("## Step 0.5")]
    row = next(ln for ln in PIPELINE.splitlines() if ln.startswith("| 0.4 |"))
    assert "pipeline start --tier DEEP --mode HUNT [--no-fix]" in section
    assert "--no-fix" in section
    assert "--no-fix" in row


def test_second_start_is_described_as_refused():
    """`start` refuses while a run is open, so the Step 0.6 note must say so.

    Red 2026-10-10: baseline a8d7445 text ("would replace the open run"); failed at
    `assert "refused" in section`.
    """
    section = PIPELINE[PIPELINE.find("## Step 0.6"):PIPELINE.find("## Findings Schema")]
    assert "second `pipeline start`" in section
    assert "refused" in section
    assert "would\nreplace" not in section and "would replace" not in section


def test_revise_loop_reverifies_l5_5_and_l5_6():
    """The gate owes L5.6 after any edit, including a REVISE edit.

    Red 2026-10-10: baseline a8d7445 text ("re-verify (L5 + L5.5)"); failed at
    `assert "L5.6" in revise`.
    """
    step6 = PIPELINE[PIPELINE.find("## Step 6"):]
    revise = next(ln for ln in step6.splitlines() if "REVISE → implement feedback" in ln)
    assert "L5.6" in revise


def test_step_4_says_the_gate_detects_change_from_the_working_tree():
    """`--fixes` is recorded for the report; the gate detects change from the working tree.

    Red 2026-10-10: baseline a8d7445 text ("`--fixes` is what tells the commit guard"); failed
    at `assert "is what tells the commit guard" not in step4`.
    """
    step4 = PIPELINE[PIPELINE.find("## Step 4:"):PIPELINE.find("## Step 5")]
    assert "is what tells the commit guard" not in step4
    assert "working tree" in step4


def test_codex_closes_the_run_when_close_is_the_only_item_left():
    """`status --require-complete` exits 2 for a complete but open run, so the skill must say
    to close it.

    Red 2026-10-10: baseline a8d7445 text (no mention of closing); failed at
    `assert "pipeline close" in section`.
    """
    section = CODEX[CODEX.find("## Gate before reporting"):CODEX.find("## Report completion")]
    assert "pipeline close" in section
