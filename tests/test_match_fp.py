"""Task 4.3: the mechanical PRIOR_FP matcher, and the L2.5 gate that holds the run to it.

The saved answers are `tests/fixtures/audit_memory/responses/`, built from the golden records
to RE-call's contract C5 (see `build.py` there). Red proof (user rule, 2026-09-08) is in each
test's docstring: the mutation, the test run against it, the assertion that failed; restored.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
from conftest import findings_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.bugs import match_fp
from cca_checks.bugs.__main__ import main

RESPONSES = Path(__file__).parent / "fixtures" / "audit_memory" / "responses"
ANSWER = json.loads((RESPONSES / "fp_answer.json").read_text(encoding="utf-8"))
RECORD = ANSWER["records"][0]["recall_record"]
REPO = "GiulioDER/RE-call"


def finding(**over):
    value = {"id": "BUG-7", "category": "concurrency", "priority": "P2", "high_stakes": False,
             "anchor": dict(RECORD["anchors"][0])}
    value.update(over)
    return value


def agrees(anchor):
    """The checkout's hash for every place equals the record's: nothing changed since."""
    return anchor["code_sha"]


def run(findings=None, answer=None, **kw):
    kw.setdefault("on", True)
    kw.setdefault("local", agrees)
    kw.setdefault("today", "2026-10-22")
    return match_fp.match(findings or [finding()], answer or ANSWER, repo=REPO, **kw)


def edited(**fields):
    answer = copy.deepcopy(ANSWER)
    answer["records"][0].update(fields)
    return answer


def test_a_current_exact_record_suppresses_with_its_citation():
    """Every condition holds, so the finding is suppressed and cites the record.

    Red 2026-10-22: mutation `"source": record.get("source")` -> `"source": None` in
    `match_fp._citation`; failed at `assert got.citation["source"] == ...`. (A first version
    called `.endswith` on it, which raised AttributeError instead; changed to an equality.)
    """
    [got] = run()
    assert (got.action, got.record_id, got.reasons) == ("suppress", RECORD["id"], ())
    assert got.citation["record_id"] == RECORD["id"]
    assert got.citation["source"] == "memos/RE-call__fp__run-20261021-9f8e7d__BUG-003.md"
    assert got.citation["run_id"] == RECORD["run_id"]


def test_an_anchor_the_query_did_not_cover_is_exact_only_when_the_checkout_agrees():
    """The second anchor (a mitigation in store.py, a file the query did not name) came back
    `none`: it counts exact only when the checkout's hash equals the record's.

    Red 2026-10-22: mutation `and local(anchor) == anchor.get("code_sha")` -> `and True` in
    `match_fp._statuses`; failed at `assert got.action == "attach"` (it suppressed).
    """
    [got] = run()
    assert got.action == "suppress"
    [got] = run(local=lambda anchor: "0" * 64)
    assert got.action == "attach"
    assert "anchor recall/store.py Store._with_retry_shared is none" in got.reasons


def test_symbol_changed_never_suppresses():
    """Invariant: every anchor exact; a changed mitigation lapses the record.

    Red 2026-10-22: mutation `if status != "exact":` -> `if status not in ("exact",
    "symbol_changed"):` in `match_fp.match`; failed at `assert got.action == "attach"`.
    """
    answer = edited()
    answer["records"][0]["anchor_matches"][1]["match"] = "symbol_changed"
    [got] = run(answer=answer)
    assert got.action == "attach"


def test_a_superseded_record_does_not_suppress():
    """Red 2026-10-22: mutation deleting `reasons.append(f"superseded by ...")` in
    `match_fp.match`; failed at `assert got.action == "attach"`."""
    [got] = run(answer=edited(superseded_by="RE-call:fp:run-x:BUG-003~retract"))
    assert got.action == "attach"


def test_a_retracted_record_never_suppresses():
    """Invariant: a retraction in the local ledger (RE-call learns of it one refresh later) or
    in the answer stops the record suppressing.

    Red 2026-10-22: mutation `if rec["id"] in retracted or rec["id"] in retractions:` -> `if
    False:` in `match_fp.match`; failed at the first `assert got.action == "attach"`.
    """
    [got] = run(retracted={RECORD["id"]})
    assert got.action == "attach" and "retracted" in got.reasons
    answer = edited()
    answer["records"].append({**copy.deepcopy(answer["records"][0]),
                              "kind": "false_positive_retraction",
                              "recall_record": {"kind": "false_positive_retraction",
                                                "target_id": RECORD["id"]}})
    [got] = run(answer=answer)
    assert got.action == "attach"


def test_an_expired_record_does_not_suppress():
    """Red 2026-10-22: mutation `return not isinstance(valid_until, str) or valid_until[:10] <=
    today` -> `return False` in `match_fp._expired`; failed at `assert got.action == "attach"`."""
    [got] = run(answer=edited(valid_until="2026-10-01"))
    assert got.action == "attach"


def test_another_category_does_not_suppress():
    """Red 2026-10-22: mutation `if finding.get("category") != rec.get("category"):` -> `if
    False:` in `match_fp.match`; failed at `assert got.action == "attach"`."""
    [got] = run([finding(category="resource-leak")])
    assert got.action == "attach"


def test_a_high_stakes_finding_does_not_suppress():
    """Red 2026-10-22: mutation `if finding.get("high_stakes") is True:` -> `if False:` in
    `match_fp.match`; failed at `assert got.action == "attach"`."""
    [got] = run([finding(high_stakes=True)])
    assert got.action == "attach" and "high stakes" in got.reasons


def test_a_finding_of_higher_priority_than_the_record_does_not_suppress():
    """The record is P2: a P1 finding goes to fp-check, a P3 one is suppressed.

    Red 2026-10-22: mutation `or mine < theirs` -> `or mine > theirs` in `match_fp.match`;
    failed at `assert got.action == "attach"` for the P1.
    """
    [got] = run([finding(priority="P1")])
    assert got.action == "attach"
    [got] = run([finding(priority="P3")])
    assert got.action == "suppress"


def test_changed_code_at_the_finding_does_not_suppress():
    """The finding's own place has another hash now: the record was judged on other code.

    Red 2026-10-22: mutation `reasons.append("the finding's code differs from the record's")` ->
    `pass` in `match_fp.match` (the answer reports the record's own anchor exact, so only this
    check sees the finding's new hash); failed at `assert got.action == "attach"`.
    """
    [got] = run([finding(anchor={**RECORD["anchors"][0], "code_sha": "1" * 64})])
    assert got.action == "attach"


def test_a_renamed_file_never_suppresses():
    """A finding at the same symbol in a moved file matches no record at all.

    Red 2026-10-22: mutation comparing only the symbol (`(primary.get("symbol"),)` vs
    `(anchor.get("symbol"),)`) in `match_fp.match`; failed at `assert run([...]) == []`.
    """
    moved = {**RECORD["anchors"][0], "file": "recall/pools/pool.py"}
    assert run([finding(anchor=moved)]) == []


def test_the_switch_turns_every_suppression_into_an_attachment(monkeypatch):
    """Red 2026-10-22: mutation `return os.environ.get(SWITCH, "").strip().lower() != "off"` ->
    `return True` in `match_fp.enabled`; failed at `assert got.action == "attach"`."""
    monkeypatch.setenv("CCA_PRIOR_FP", "off")
    [got] = match_fp.match([finding()], ANSWER, repo=REPO, today="2026-10-22", local=agrees)
    assert got.action == "attach" and "CCA_PRIOR_FP=off" in got.reasons


def test_another_repositorys_record_is_ignored():
    """Red 2026-10-22: mutation deleting the repo comparison in `match_fp._fp`; failed at
    `assert ... == []`."""
    assert match_fp.match([finding()], ANSWER, repo="acme/widgets", on=True,
                          today="2026-10-22", local=agrees) == []


# The L2.5 gate: PRIOR_FP is the matcher's verdict, never the orchestrator's.


def _through_l2(repo, n=2):
    state = ps.start("FAST", root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": n}, output=findings_output(repo, n), root=repo)
    return state


def _match_file(repo, state, *matches):
    path = repo / ".claude" / "audits" / "runs" / state.run_id / "match_fp.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"schema": 1, "matches": [
        {"finding_id": f, "record_id": r, "action": a, "reasons": [], "citation": {}}
        for f, r, a in matches]}), encoding="utf-8")


def _l25(repo, rows, prior_fp, confirmed):
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "prior_fp": prior_fp},
              output=write_output(repo, rows), root=repo)


def test_l25_refuses_a_prior_fp_the_matcher_did_not_suppress(git_repo):
    """Red 2026-10-22: mutation `unmatched = sorted(set(given) - set(suppress))` -> `unmatched =
    []` in `pipeline_state._check_prior_fp`; failed at `pytest.raises(..., match="did not
    suppress")` (DID NOT RAISE)."""
    state = _through_l2(git_repo)
    _match_file(git_repo, state, ("F-1", "rec-1", "attach"))
    with pytest.raises(ps.RecordRefused, match="did not suppress"):
        _l25(git_repo, [{"id": "F-1", "verdict": "PRIOR_FP", "record_id": "rec-1"},
                        {"id": "F-2", "verdict": "CONFIRMED"}], 1, 1)


def test_l25_refuses_to_hide_a_suppression(git_repo):
    """Invariant: nothing suppressed disappears into another verdict.

    Red 2026-10-22: mutation `dropped = sorted(set(suppress) - set(given))` -> `dropped = []`
    in `_check_prior_fp`; failed at `pytest.raises(..., match="does not mark")`.
    """
    state = _through_l2(git_repo)
    _match_file(git_repo, state, ("F-1", "rec-1", "suppress"))
    with pytest.raises(ps.RecordRefused, match="does not mark"):
        _l25(git_repo, [{"id": "F-1", "verdict": "CONFIRMED"},
                        {"id": "F-2", "verdict": "CONFIRMED"}], 0, 2)
    _l25(git_repo, [{"id": "F-1", "verdict": "PRIOR_FP", "record_id": "rec-1"},
                    {"id": "F-2", "verdict": "CONFIRMED"}], 1, 1)
    l25 = json.loads((git_repo / ".claude" / "audits" / "runs" / state.run_id
                      / "L2.5.json").read_text(encoding="utf-8"))
    assert {"id": "F-1", "verdict": "PRIOR_FP", "record_id": "rec-1"} in l25


def test_match_fp_cli_writes_the_match_file(git_repo, monkeypatch):
    """The command computes the findings' anchors in the checkout and writes the match file.

    Red 2026-10-22: mutation `a.out.write_text(...)` -> `pass` in `bugs.__main__._match_fp`;
    failed at `assert out.exists()`.
    """
    (git_repo / "recall").mkdir()
    l2 = write_output(git_repo, [{"id": "BUG-7", "category": "concurrency", "priority": "P2",
                                  "high_stakes": False, "file": "recall/nothere.py",
                                  "line": 3}])
    out = git_repo / ".claude" / "audits" / "runs" / "r" / "match_fp.json"
    assert main(["match-fp", "--findings", l2, "--records", str(RESPONSES / "fp_answer.json"),
                 "--out", str(out), "--root", str(git_repo), "--repo-name", REPO]) == 0
    assert out.exists()
    value = json.loads(out.read_text(encoding="utf-8"))
    assert value["switch"] == "on" and value["matches"] == [] and value["errors"] == []
