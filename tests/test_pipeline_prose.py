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
