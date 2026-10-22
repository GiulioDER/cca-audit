"""Tasks 4.1 and 4.2: false-positive rows at L2.5, the architect's acceptance at L6, and the
false-positive records `pipeline close` writes.

Red proof (user rule, 2026-09-08) is in each test's docstring: the mutation applied to the
production line, the test run against it, and the assertion that failed; each was restored.
"""
from __future__ import annotations

import json
import subprocess

import pytest
from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.bugs import fp_records
from cca_checks.bugs.query_anchors import at

POOL = (
    "def lease(pool):\n"
    "    return pool.pop()\n"
    "\n"
    "\n"
    "def guard(pool):\n"
    "    if not pool:\n"
    "        raise ValueError('empty')\n"
)


@pytest.fixture
def repo(git_repo):
    (git_repo / "pool.py").write_text(POOL, encoding="utf-8")
    for args in (["add", "-A"], ["commit", "-qm", "pool"],
                 ["remote", "add", "origin", "https://github.com/acme/widgets.git"]):
        subprocess.run(["git", "-C", str(git_repo), *args], check=True, capture_output=True)
    return git_repo


def _anchor(repo, ref):
    return at(repo, [ref])[0]["anchor"]


def fp_row(repo, run_id, finding, *, line=2, reason="not_real", refs=("pool.py:6",), **over):
    where = _anchor(repo, f"pool.py:{line}")
    row = {"run_id": run_id, "finding_id": finding, "auditor": "cca-bug-auditor",
           "category": "x", "severity": "Medium", "high_stakes": False, "priority": "P2",
           "verify_source": "llm", "verdict": "FALSE_POSITIVE", "fix_id": None,
           "file": "pool.py", "line": line, "symbol": where["symbol"],
           "code_sha": where["code_sha"], "claim": f"{finding} pops an empty pool",
           "evidence_refs": list(refs), "fp_reason": reason}
    row.update(over)
    return row


def _append(repo, rows):
    path = repo / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row) + "\n")


def through_l25(repo, rows_for):
    """A STANDARD run whose findings F-1..F-n are all FALSE_POSITIVE; `rows_for(run_id)` gives
    their ledger rows. Returns the state after L2.6."""
    state = ps.start("STANDARD", root=repo)
    rows = rows_for(state.run_id)
    n = len(rows)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": n}, output=findings_output(repo, n), root=repo)
    _append(repo, rows)
    ps.record("L2.5", counts={"confirmed": 0, "fp": n, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo, fp=n), root=repo)
    ps.record("L2.6", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(repo), root=repo)
    return state


def acceptance(**decisions):
    return {"fp_acceptance": [{"id": i.replace("_", "-"), "decision": d, "reason": "read it"}
                              for i, d in decisions.items()]}


# Task 4.1: the L2.5 validator


def test_l25_refuses_an_unknown_fp_reason(repo):
    """Invariant: a FALSE_POSITIVE row's fp_reason is one of not_real, out_of_scope, settled.

    Red 2026-10-22: mutation `if row["fp_reason"] not in FP_REASONS` -> `if False` in
    `pipeline_state._check_fp_rows`; failed at `pytest.raises(ps.RecordRefused, match="fp_reason")`
    (DID NOT RAISE).
    """
    with pytest.raises(ps.RecordRefused, match="fp_reason 'looks_fine'"):
        through_l25(repo, lambda run: [fp_row(repo, run, "F-1", reason="looks_fine")])


def test_l25_refuses_a_fp_row_without_its_record_fields(repo):
    """Invariant: a FALSE_POSITIVE row carries every Step 2.5b field.

    Red 2026-10-22: mutation `missing = [k for k in FP_FIELDS if k not in row]` -> `missing = []`
    in `_check_fp_rows`; failed at `pytest.raises(..., match="lacks symbol")` (the regex did not
    match: the row got as far as the anchor check). A first version deleted `claim`, which the
    same mutation turned into a KeyError rather than a failed assertion, so it was changed.
    """
    def rows(run):
        row = fp_row(repo, run, "F-1")
        del row["symbol"]
        return [row]
    with pytest.raises(ps.RecordRefused, match="lacks symbol"):
        through_l25(repo, rows)


def test_l25_refuses_a_hand_written_code_sha_and_pins_every_place(repo):
    """Invariant: the row's symbol and code_sha are the code's, and L2.5 writes fp_anchors.json
    with the finding's anchor first, then each evidence location's.

    Red 2026-10-22: mutation `if (row.get("symbol"), row.get("code_sha")) != want:` -> `if False:`
    in `_fp_anchors`; failed at `pytest.raises(..., match="copy both")` (DID NOT RAISE).
    """
    with pytest.raises(ps.RecordRefused, match="copy both"):
        through_l25(repo, lambda run: [fp_row(repo, run, "F-1", code_sha="0" * 64)])
    ps.abort("test", root=repo)
    state = through_l25(repo, lambda run: [fp_row(repo, run, "F-1")])
    pinned = json.loads((repo / ".claude" / "audits" / "runs" / state.run_id
                         / "fp_anchors.json").read_text(encoding="utf-8"))
    assert pinned["F-1"]["anchors"] == [_anchor(repo, "pool.py:2"), _anchor(repo, "pool.py:6")]
    assert [a["symbol"] for a in pinned["F-1"]["anchors"]] == ["lease", "guard"]


# Task 4.1: the L6 validator


def test_l6_approved_refuses_a_missing_acceptance(repo):
    """Invariant: an APPROVED L6 over false positives decides each of them.

    Red 2026-10-22: mutation `if approved and missing:` -> `if False:` in `_check_l6`; failed at
    the second `pytest.raises(..., match="no ACCEPT or REJECT for F-2")` (DID NOT RAISE).
    Red 2026-10-22: mutation `if approved and fps:` -> `if False:` in `_check_l6` (no file at
    all); failed at the first `pytest.raises(..., match="needs --output")` (DID NOT RAISE).
    """
    through_l25(repo, lambda run: [fp_row(repo, run, "F-1"), fp_row(repo, run, "F-2")])
    with pytest.raises(ps.RecordRefused, match="needs --output"):
        ps.record("L6", detail="APPROVED", root=repo)
    with pytest.raises(ps.RecordRefused, match="no ACCEPT or REJECT for F-2"):
        ps.record("L6", detail="APPROVED", output=write_output(repo, acceptance(F_1="ACCEPT")),
                  root=repo)
    ps.record("L6", detail="APPROVED",
              output=write_output(repo, acceptance(F_1="ACCEPT", F_2="REJECT")), root=repo)


def test_l6_refuses_a_decision_on_a_finding_that_is_no_false_positive(repo):
    """Invariant: the acceptance names only L2.5's false positives, each with a known decision.

    Red 2026-10-22: mutation `if entry["id"] not in fps:` -> `if False:` in `_check_l6`; failed
    at `pytest.raises(..., match="did not mark FALSE_POSITIVE")` (DID NOT RAISE).
    """
    through_l25(repo, lambda run: [fp_row(repo, run, "F-1")])
    with pytest.raises(ps.RecordRefused, match="did not mark FALSE_POSITIVE"):
        ps.record("L6", detail="APPROVED",
                  output=write_output(repo, acceptance(F_1="ACCEPT", F_9="ACCEPT")), root=repo)
    with pytest.raises(ps.RecordRefused, match="ACCEPT or REJECT"):
        ps.record("L6", detail="APPROVED",
                  output=write_output(repo, acceptance(F_1="MAYBE")), root=repo)


def test_a_reject_is_disputed_for_a_human(repo):
    """Invariant: a false positive the architect rejected is listed as disputed in the run
    summary and counted in human_pending, the UNCERTAIN -> human route.

    Red 2026-10-22: mutation `and e.get("decision") == "REJECT"` -> `and e.get("decision") ==
    "NONE"` in `disputed_fps`; failed at `assert summary["disputed_fp"] == ["F-2"]` ([] != ['F-2']).
    """
    state = through_l25(repo, lambda run: [fp_row(repo, run, "F-1"), fp_row(repo, run, "F-2")])
    ps.record("L6", detail="APPROVED",
              output=write_output(repo, acceptance(F_1="ACCEPT", F_2="REJECT")), root=repo)
    ps.close(root=repo)
    summary = json.loads((repo / ".claude" / "audits" / "runs" / state.run_id
                          / "summary.json").read_text(encoding="utf-8"))
    assert summary["disputed_fp"] == ["F-2"]
    assert summary["human_pending"] == 1


# Task 4.2: the write path at close


def _ledger(bugs_dir):
    path = bugs_dir / "bugs.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_an_all_fp_run_is_captured_and_writes_its_record_at_close(repo, _private_bugs_dir):
    """Invariant: an APPROVED run whose findings were all false positives (no L4) still writes
    each accepted not_real false positive as a record, at close, with the finding's anchor and
    each evidence location as anchors, and its run is kept under recorded/.

    Red 2026-10-22: mutation `if not fixes and not picked:` -> `if not fixes:` (the early return
    on master) in `pending.capture`; failed at `assert [r["id"] for r in rows] == [...]`.
    """
    state = through_l25(repo, lambda run: [fp_row(repo, run, "F-1")])
    ps.record("L6", detail="APPROVED", output=write_output(repo, acceptance(F_1="ACCEPT")),
              root=repo)
    ps.close(root=repo)
    rows = _ledger(_private_bugs_dir)
    assert [r["id"] for r in rows] == [f"widgets:fp:{state.run_id}:F-1"]
    assert rows[0]["kind"] == "false_positive" and rows[0]["repo"] == "acme/widgets"
    assert rows[0]["anchors"] == [_anchor(repo, "pool.py:2"), _anchor(repo, "pool.py:6")]
    kept = list((_private_bugs_dir / "recorded").iterdir())
    assert len(kept) == 1 and (kept[0] / "fp_anchors.json").exists()
    assert not (_private_bugs_dir / "pending").exists()
    memo = (_private_bugs_dir / "memos").iterdir().__next__().read_text(encoding="utf-8")
    assert "recall_record: " in memo


ROW = {"run_id": "r1", "finding_id": "F-1", "category": "x", "severity": "Medium",
       "priority": "P2", "high_stakes": False, "auditor": "a", "claim": "c",
       "evidence_refs": ["pool.py:6"], "verify_source": "llm", "fp_reason": "not_real"}
ANCHORS = {"F-1": {"anchors": [{"file": "pool.py", "symbol": "lease", "code_sha": "a" * 64}],
                   "errors": []}}
ACCEPTED = {"fp_acceptance": [{"id": "F-1", "decision": "ACCEPT", "reason": "r"}]}


def _build(row=None, anchors=None, accepted=None, verdict="APPROVED"):
    return fp_records.build([row or ROW], ANCHORS if anchors is None else anchors,
                            ACCEPTED if accepted is None else accepted, verdict,
                            repo="acme/widgets", date="2026-10-22")


def test_build_writes_the_accepted_not_real_fp():
    """The control for the five exclusions below: every condition holds, one row."""
    rows, skipped = _build()
    assert [r.id for r in rows] == ["widgets:fp:r1:F-1"] and skipped == []


def test_out_of_scope_is_never_a_record():
    """Red 2026-10-22: mutation `if row.get("fp_reason") != FP_REASON:` -> `if False:` in
    `fp_records.build`; failed at `assert rows == []` (one row built)."""
    rows, skipped = _build(row={**ROW, "fp_reason": "out_of_scope"})
    assert rows == [] and skipped == ["F-1: fp_reason 'out_of_scope'"]


def test_settled_is_never_a_record():
    """Red 2026-10-22: mutation `if row.get("fp_reason") != FP_REASON:` -> `if False:` in
    `fp_records.build`, run alone; failed at `assert rows == []`."""
    rows, _ = _build(row={**ROW, "fp_reason": "settled"})
    assert rows == []


def test_a_rejected_fp_is_never_a_record():
    """Red 2026-10-22: mutation `if decided.get(finding) != ACCEPT:` -> `if False:` in
    `fp_records.build`; failed at `assert rows == []`."""
    rows, skipped = _build(accepted={"fp_acceptance": [{"id": "F-1", "decision": "REJECT",
                                                         "reason": "r"}]})
    assert rows == [] and skipped == ["F-1: L6 REJECT"]


def test_a_run_not_approved_writes_no_record():
    """Red 2026-10-22: mutation `if str(verdict).strip().upper() != APPROVED:` -> `if False:` in
    `fp_records.build`; failed at `assert rows == []`."""
    rows, skipped = _build(verdict="REVISE")
    assert rows == [] and skipped == ["L6 verdict is REVISE, not APPROVED"]


def test_an_unresolvable_anchor_writes_no_record():
    """Invariant: a place with no code_sha, or any place that failed to resolve, makes no record.

    Red 2026-10-22: mutation `if not isinstance(facts, dict) or facts.get("errors"):` -> `if not
    isinstance(facts, dict):` in `fp_records._anchors`; failed at the second `assert rows == []`.
    Red 2026-10-22: mutation `and isinstance(item.get("code_sha"), str) and item["code_sha"]):` ->
    `and True):` in `_anchors`; failed at the first `assert rows == []` (null code_sha).
    """
    null_sha = {"F-1": {"anchors": [{"file": "pool.py", "symbol": "lease", "code_sha": None}],
                        "errors": []}}
    rows, _ = _build(anchors=null_sha)
    assert rows == []
    failed = {"F-1": {"anchors": ANCHORS["F-1"]["anchors"], "errors": ["gone.py:3: OSError"]}}
    rows, skipped = _build(anchors=failed)
    assert rows == [] and skipped == ["F-1: an anchor did not resolve"]


def test_no_nul_reaches_a_record():
    """Invariant: U+0000 in fp-check's claim is replaced when the row is built (#88's guard).

    Red 2026-10-22: mutation `return [no_nul(r) for r in out], []` -> `return out, []` in
    `fp_records.to_rows`; failed at `assert "\\x00" not in rows[0].claim`.
    """
    rows, _ = _build(row={**ROW, "claim": "pops\x00 an empty pool"})
    assert "\x00" not in rows[0].claim and rows[0].claim == "pops� an empty pool"
