"""Invariants of the benchmark scorer's drop adjudication.

These assert the SCORING RULES, not the current benchmark numbers. A run's recall
is data; "a drop that lands on ground truth is a wrong drop" is a contract. Pinning
the former would make every re-run a test failure and teach us to update the
expected value instead of reading it.
"""
import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

HARNESS = Path(__file__).resolve().parents[1] / "benchmarks" / "harness"
WORKFLOW_JS = HARNESS / "audit_workflow.js"


def _load_score():
    spec = importlib.util.spec_from_file_location("bench_score", HARNESS / "score.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


score = _load_score()

WINDOWS = [[100, 110]]


def f(line, **kw):
    return {"severity": "High", "line": line, "title": "t", **kw}


# --- localization -----------------------------------------------------------

@pytest.mark.parametrize("line,expected", [
    (100, True), (110, True),          # inside
    (97, True), (113, True),           # exactly on the +/-TOL boundary
    (96, False), (114, False),         # one line past it
    (None, False), ("x", False),       # unparseable line numbers never count as hits
])
def test_hit_respects_tolerance_band(line, expected):
    assert score.hit(line, WINDOWS) is expected


def test_count_hits_counts_every_finding_not_just_the_first():
    # any_hit answers "was this bug caught"; count_hits answers "how many drops cost
    # us". Collapsing the second into the first would under-report the gate's cost.
    findings = [f(100), f(105), f(500)]
    assert score.any_hit(findings, WINDOWS) is True
    assert score.count_hits(findings, WINDOWS) == 2


def test_count_hits_tolerates_missing_lists():
    assert score.count_hits(None, WINDOWS) == 0
    assert score.count_hits([], WINDOWS) == 0


# --- drop_reason bucketing --------------------------------------------------

def test_every_enum_reason_buckets_to_itself():
    for reason in score.DROP_REASONS:
        assert score.reason_bucket(f(1, drop_reason=reason)) == reason


@pytest.mark.parametrize("finding", [
    f(1),                          # field absent (pre-enum run)
    f(1, drop_reason=""),          # present but empty
])
def test_missing_reason_is_unlabeled_never_refuted(finding):
    # The failure this guards: scoring an un-reasoned drop as a refutation would
    # credit the gate for judgement it never exercised.
    assert score.reason_bucket(finding) == "unlabeled"


def test_unknown_reason_is_flagged_not_silently_accepted():
    assert score.reason_bucket(f(1, drop_reason="because")) == "invalid:because"


def test_refuted_and_inconclusive_are_disjoint_and_cover_the_enum():
    assert not set(score.REFUTED_REASONS) & set(score.INCONCLUSIVE_REASONS)
    assert set(score.DROP_REASONS) == set(
        score.REFUTED_REASONS + score.INCONCLUSIVE_REASONS + score.OTHER_REASONS
    )


def test_tally_reasons_accumulates_across_calls():
    acc = {}
    score.tally_reasons([f(1, drop_reason="duplicate"), f(2)], acc)
    score.tally_reasons([f(3, drop_reason="duplicate")], acc)
    assert acc == {"duplicate": 2, "unlabeled": 1}


# --- the enum must not drift away from the workflow that emits it -----------

def test_enum_matches_the_workflow_schema():
    js = WORKFLOW_JS.read_text(encoding="utf-8")
    block = js.split("const DROP_REASON", 1)[1].split("enum: [", 1)[1].split("]", 1)[0]
    in_js = {line.split("'")[1] for line in block.splitlines() if "'" in line}
    assert in_js, "could not parse the DROP_REASON enum out of audit_workflow.js"
    assert in_js == set(score.DROP_REASONS), (
        "audit_workflow.js emits drop_reason values score.py does not know about "
        "(or vice versa); an unknown value scores as invalid: and drops out of the rates"
    )


# --- end-to-end adjudication ------------------------------------------------

def _run(tmp_path, rows, windows=WINDOWS):
    """Run score.py as the CLI does and return the summary it writes."""
    base = tmp_path
    (base / "results").mkdir(parents=True, exist_ok=True)
    (base / "harness").mkdir(parents=True, exist_ok=True)
    res = base / "results" / "wf_output.json"
    idx = base / "harness" / "bugs_index.json"
    res.write_text(json.dumps(rows), encoding="utf-8")
    idx.write_text(json.dumps({
        "proj/1": {"files": {"a.py": {"buggy_windows": windows, "fixed_windows": windows}}}
    }), encoding="utf-8")
    env = {**os.environ, "CCA_BENCH_DIR": str(base)}
    out = subprocess.run([sys.executable, str(HARNESS / "score.py"), str(res), str(idx)],
                         capture_output=True, text=True, env=env)
    assert out.returncode == 0, out.stderr
    return json.loads((base / "results" / "summary.json").read_text(encoding="utf-8"))


def _row(**kw):
    base = {"bug": "proj/1", "project": "proj", "file": "a.py", "recognized": False,
            "buggy_raw": [], "buggy_confirmed": [], "buggy_dropped": [],
            "fixed_raw": [], "fixed_confirmed": [], "fixed_dropped": []}
    return {**base, **kw}


def test_drop_on_ground_truth_with_no_surviving_catch_is_fatal(tmp_path):
    # The auditor DID find the bug; the gate killed it. Recall must not read as an
    # auditor miss -- that is the whole point of adjudicating drops.
    got = _run(tmp_path, [_row(
        buggy_raw=[f(105)],
        buggy_dropped=[f(105, drop_reason="refuted_not_a_defect")],
    )])
    assert got["summary"]["recall_raw"] == "1/1"
    assert got["summary"]["recall_confirmed"] == "0/1"
    assert got["summary"]["wrong_drops_on_ground_truth"] == 1
    assert got["summary"]["bugs_lost_to_gate"] == 1
    assert got["per_bug"]["proj/1"]["drop_verdict"] == "FATAL"


def test_wrong_drop_is_redundant_when_another_finding_still_catches_it(tmp_path):
    got = _run(tmp_path, [_row(
        buggy_raw=[f(105), f(106)],
        buggy_confirmed=[f(106)],
        buggy_dropped=[f(105, drop_reason="duplicate")],
    )])
    assert got["summary"]["wrong_drops_on_ground_truth"] == 1
    assert got["summary"]["bugs_lost_to_gate"] == 0  # cost no recall
    assert got["per_bug"]["proj/1"]["drop_verdict"] == "redundant"


def test_drop_on_the_fixed_file_is_credited_not_charged(tmp_path):
    # Same gate, opposite direction: here the drop PREVENTED a false alarm. A metric
    # that only counts drops charges the gate for this at the same rate as for a miss.
    got = _run(tmp_path, [_row(
        fixed_raw=[f(105)],
        fixed_dropped=[f(105, drop_reason="refuted_misread_flow")],
    )])
    assert got["summary"]["false_alarms_prevented_by_gate"] == 1
    assert got["summary"]["wrong_drops_on_ground_truth"] == 0
    assert got["summary"]["specificity_confirmed"] == "1/1"
    assert got["per_bug"]["proj/1"]["drop_verdict"] == "-"


def test_off_target_drop_counts_in_neither_direction(tmp_path):
    got = _run(tmp_path, [_row(
        buggy_raw=[f(900)],
        buggy_dropped=[f(900, drop_reason="refuted_guarded_elsewhere")],
    )])
    s = got["summary"]
    assert s["total_dropped_by_fpcheck"] == 1
    assert s["wrong_drops_on_ground_truth"] == 0
    assert s["false_alarms_prevented_by_gate"] == 0
    assert s["refuted"] == "1/1"


def test_unlabeled_drops_are_excluded_from_both_rates(tmp_path):
    got = _run(tmp_path, [_row(buggy_raw=[f(900)], buggy_dropped=[f(900)])])
    s = got["summary"]
    assert s["drop_reasons"] == {"unlabeled": 1}
    # denominator is LABELED drops, so an unlabeled run reports 0/0, not 0/1 refuted
    assert s["refuted"].startswith("0/0")
    assert s["inconclusive"].startswith("0/0")


def test_refuted_and_inconclusive_split_the_labeled_drops(tmp_path):
    got = _run(tmp_path, [_row(buggy_raw=[f(900), f(901), f(902)], buggy_dropped=[
        f(900, drop_reason="refuted_not_a_defect"),
        f(901, drop_reason="inconclusive_unprovable_from_file"),
        f(902, drop_reason="duplicate"),
    ])])
    s = got["summary"]
    assert s["refuted"] == "1/3"
    assert s["inconclusive"] == "1/3"
