"""Task 4.3: the mechanical PRIOR_FP matcher, and the L2.5 gate that holds the run to it.

The saved answers are `tests/fixtures/audit_memory_responses/`, built from the golden records
to RE-call's contract C5 (see `build.py` there). Red proof (user rule, 2026-09-08) is in each
test's docstring: the mutation, the test run against it, the assertion that failed; restored.
"""
from __future__ import annotations

import copy
import json
import subprocess
from pathlib import Path

import pytest
from conftest import write_output

from cca_checks import pipeline_state as ps
from cca_checks.bugs import match_fp
from cca_checks.bugs.__main__ import main
from cca_checks.bugs.query_anchors import at
from cca_checks.bugs.records import RetractionRow, from_record
from cca_checks.bugs.store import write

RESPONSES = Path(__file__).parent / "fixtures" / "audit_memory_responses"
ANSWER = json.loads((RESPONSES / "fp_answer.json").read_text(encoding="utf-8"))
RECORD = ANSWER["records"][0]["recall_record"]
GOLDEN_FP = json.loads((Path(__file__).parent / "fixtures" / "audit_memory"
                        / "expected_records.json").read_text(encoding="utf-8"))[
    "RE-call__fp__run-20261021-9f8e7d__BUG-003.md"]
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

    Red 2026-10-22: mutation `if local(anchor) == anchor.get("code_sha"):` -> `if True:` in
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
    """Invariant: a retraction in the local ledger (RE-call learns of it one refresh later) stops
    the record suppressing; in the answer, a retraction shows as the target's `superseded_by`.

    Red 2026-10-22: mutation `if rec["id"] in retracted:` -> `if False:` in `match_fp.match`;
    failed at `assert got.action == "attach"`.
    """
    [got] = run(retracted={RECORD["id"]})
    assert got.action == "attach" and "retracted" in got.reasons


def test_currency_comes_from_the_returned_fields_not_from_retraction_links():
    """Orchestrator, 2026-10-22, from RE-call #977 and #970: RE-call honours the successor's
    validity window, so a retraction dated in the future does not yet hide its target. A
    retraction record in the answer whose target RE-call still returns current does not stop it.

    Red 2026-10-22: mutation restoring the inference (`if rec["id"] in retracted or rec["id"] in
    {r["recall_record"].get("target_id") for r in held}:`) in `match_fp.match`; failed at
    `assert got.action == "suppress"`.
    """
    answer = edited()
    answer["records"].append({**copy.deepcopy(answer["records"][0]),
                              "id": RECORD["id"] + "~retract",
                              "kind": "false_positive_retraction",
                              "recall_record": {"kind": "false_positive_retraction",
                                                "target_id": RECORD["id"],
                                                "date": "2026-11-30"}})
    [got] = run(answer=answer)
    assert got.action == "suppress"


def test_a_local_retraction_counts_only_from_its_date(tmp_path):
    """The local ledger follows the same window: a retraction dated after today is not yet one.

    Red 2026-10-22: mutation `return date.fromisoformat(...) <= date.fromisoformat(...)` ->
    `return True` in `match_fp._in_effect`; failed at `assert retracted == set()`.
    """
    fp = from_record(GOLDEN_FP)
    write([fp], tmp_path, "2026-10-22T00:00:00Z")
    later = RetractionRow(kind="false_positive_retraction", project=fp.project, repo=fp.repo,
                          date="2026-11-30", target_id=fp.id, reason="r", retracted_by="o")
    write([later], tmp_path, "2026-10-22T00:00:00Z")
    retracted, _, blocked = match_fp.ledger_facts(tmp_path, today="2026-10-22")
    assert blocked is None and retracted == set()
    retracted, _, _ = match_fp.ledger_facts(tmp_path, today="2026-11-30")
    assert retracted == {fp.id}


def test_an_expired_record_does_not_suppress():
    """A lapsed valid_until, and one that does not read as a date, both stop a suppression.

    Red 2026-10-22: mutation `return expired(valid_until, today)` -> `return False` in
    `match_fp._expired`; failed at the first `assert got.action == "attach"`.
    Red 2026-10-22: mutation `except ValueError: return True` -> `return False` in
    `envelope.expired`; failed at the second `assert got.action == "attach"`.
    """
    [got] = run(answer=edited(valid_until="2026-10-01"))
    assert got.action == "attach"
    [got] = run(answer=edited(valid_until="not a date"))
    assert got.action == "attach"


def test_a_record_still_suppresses_on_its_valid_until_day():
    """NUM-001, 2026-10-27. Invariant: as in RE-call, a date-only `valid_until` holds through
    the end of that day, so the record suppresses on the day and not after it.

    Red 2026-10-27 against the pre-fix `envelope.expired` (`<=`, be13f2f):
    `tests/test_match_fp.py::test_a_record_still_suppresses_on_its_valid_until_day` failed at
    the first `assert got.action == "suppress"` (attach, reason `expired 2026-10-22`).
    """
    [got] = run(answer=edited(valid_until="2026-10-22"))
    assert got.action == "suppress"
    [got] = run(answer=edited(valid_until="2026-10-21"))
    assert (got.action, got.reasons[0]) == ("attach", "expired 2026-10-21")


def test_the_matcher_judges_expiry_on_the_utc_date(east_of_utc_late_evening):
    """NUM-001, 2026-10-27. Invariant: with no `today` given, the matcher's date is the UTC
    date, not the host's, which is already the next day on this clock.

    Red 2026-10-27 against be13f2f (`date.today()`, the host's real local date):
    `tests/test_match_fp.py::test_the_matcher_judges_expiry_on_the_utc_date` failed at
    `assert got.action == "suppress"`. Red 2026-10-27: mutation `datetime.now(timezone.utc)`
    -> `datetime.now()` in `envelope.utc_today` (the host's date on this clock, 2026-10-23);
    failed at the same assertion.
    """
    [got] = match_fp.match([finding()], edited(valid_until="2026-10-22"), repo=REPO, on=True,
                           local=agrees)
    assert got.action == "suppress"


def test_recalls_expired_flag_decides_the_match():
    """NUM-001, 2026-10-27. Invariant: RE-call's computed `expired` (RE-call #1007) wins over
    `valid_until` in both directions.

    Red 2026-10-27 against be13f2f (the flag unread):
    `tests/test_match_fp.py::test_recalls_expired_flag_decides_the_match` failed at
    the first `assert got.action == "attach"` (it suppressed).
    """
    [got] = run(answer=edited(expired=True))
    assert got.action == "attach"
    assert got.reasons[0] == "expired"
    [got] = run(answer=edited(expired=False, valid_until="2026-10-01"))
    assert got.action == "suppress"


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


def _gated_run(repo):
    """A FAST run whose L2 holds F-1 (category x, at app.py:1) and F-2 (category y, same place),
    with this run's saved recall_records answer holding one accepted false positive judged on
    app.py as it is now, category x."""
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://github.com/acme/widgets.git"], check=True)
    state = ps.start("FAST", root=repo)
    place = at(repo, ["app.py:1"])[0]["anchor"]
    l2 = [{"id": f"F-{i}", "category": c, "priority": "P2", "high_stakes": False,
           "file": "app.py", "line": 1, "claim": "c"} for i, c in ((1, "x"), (2, "y"))]
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 2}, output=write_output(repo, l2), root=repo)
    rec = {**RECORD, "repo": "acme/widgets", "category": "x", "anchors": [place],
           "id": "widgets:fp:r0:F-9"}
    answer = {"tenant_id": "cca", "generation_id": "g", "truncated": False, "records": [
        {**ANSWER["records"][0], "id": rec["id"], "recall_record": rec,
         "anchor_matches": [{"anchor": place, "match": "exact"}], "anchor_match": "exact"}]}
    run_dir = repo / ".claude" / "audits" / "runs" / state.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "fp-records.json").write_text(json.dumps(answer), encoding="utf-8")
    return state, rec["id"]


def _l25(repo, rows, prior_fp, confirmed):
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "prior_fp": prior_fp},
              output=write_output(repo, rows), root=repo)


def test_l25_refuses_a_prior_fp_the_matcher_did_not_suppress(git_repo):
    """Invariant: F-2's category differs from the record's, so it is not suppressible, whatever
    a match file says.

    Red 2026-10-22: mutation `unmatched = sorted(set(given) - set(suppress))` -> `unmatched =
    []` in `pipeline_state._check_prior_fp`; failed at `pytest.raises(..., match="did not
    suppress")` (DID NOT RAISE).
    """
    _, record = _gated_run(git_repo)
    with pytest.raises(ps.RecordRefused, match="did not suppress"):
        _l25(git_repo, [{"id": "F-1", "verdict": "PRIOR_FP", "record_id": record},
                        {"id": "F-2", "verdict": "PRIOR_FP", "record_id": record}], 2, 0)


def test_l25_derives_the_suppressions_itself_and_ignores_a_hand_written_match_file(git_repo):
    """Final review 2026-10-22, Important 1. Invariant: record L2.5 runs the matcher again over
    the stored L2 and the saved answer; a match file (stale, edited or hand-written) that
    suppresses F-2 suppresses nothing.

    Red 2026-10-22: mutation making `_prior_fp_matches` return the `suppress` entries of
    `match_fp.json` (the first version's behaviour); failed at `pytest.raises(..., match="did
    not suppress F-2"...)` (DID NOT RAISE).
    """
    state, record = _gated_run(git_repo)
    match = git_repo / ".claude" / "audits" / "runs" / state.run_id / "match_fp.json"
    match.write_text(json.dumps({"schema": 1, "matches": [
        {"finding_id": f, "record_id": record, "action": "suppress", "reasons": [],
         "citation": {}} for f in ("F-1", "F-2")]}), encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="F-2 is PRIOR_FP but the matcher did not"):
        _l25(git_repo, [{"id": "F-1", "verdict": "PRIOR_FP", "record_id": record},
                        {"id": "F-2", "verdict": "PRIOR_FP", "record_id": record}], 2, 0)


def test_l25_refuses_to_hide_a_suppression(git_repo):
    """Invariant: nothing suppressed disappears into another verdict.

    Red 2026-10-22: mutation `dropped = sorted(set(suppress) - set(given))` -> `dropped = []`
    in `_check_prior_fp`; failed at `pytest.raises(..., match="does not mark")`.
    """
    state, record = _gated_run(git_repo)
    with pytest.raises(ps.RecordRefused, match="does not mark"):
        _l25(git_repo, [{"id": "F-1", "verdict": "CONFIRMED"},
                        {"id": "F-2", "verdict": "CONFIRMED"}], 0, 2)
    _l25(git_repo, [{"id": "F-1", "verdict": "PRIOR_FP", "record_id": record},
                    {"id": "F-2", "verdict": "CONFIRMED"}], 1, 1)
    l25 = json.loads((git_repo / ".claude" / "audits" / "runs" / state.run_id
                      / "L2.5.json").read_text(encoding="utf-8"))
    assert {"id": "F-1", "verdict": "PRIOR_FP", "record_id": record} in l25


def test_a_finding_that_does_not_say_whether_it_is_high_stakes_is_not_suppressed():
    """Final review 2026-10-22, Important 3. Invariant: a missing high_stakes field fails closed.

    Red 2026-10-22: mutation `if finding.get("high_stakes") is not False:` -> `if
    finding.get("high_stakes") is True:` in `match_fp.match`; failed at `assert got.action ==
    "attach"`.
    """
    unsaid = finding()
    del unsaid["high_stakes"]
    [got] = run([unsaid])
    assert got.action == "attach" and "high stakes" in got.reasons


def test_an_unknown_repository_never_suppresses():
    """Final review and security review 2026-10-22. Invariant: with no repository to scope the
    records to, a record of another repository at an identical place cannot suppress.

    Red 2026-10-22: mutation deleting `if repo is None: reasons.append(...)` (`if False:`) in
    `match_fp.match`; failed at `assert got.action == "attach"`.
    """
    [got] = match_fp.match([finding()], ANSWER, repo=None, on=True, today="2026-10-22",
                           local=agrees)
    assert got.action == "attach"


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


def test_a_record_the_local_ledger_does_not_hold_never_suppresses():
    """Security review 2026-10-22. Invariant: where this host keeps the ledger, the saved answer
    (a file the orchestrator writes) suppresses only with a record the ledger holds as authored.

    Red 2026-10-22: mutation `elif known is not None and known.get(rec["id"]) != rec:` -> `elif
    False:` in `match_fp.match`; failed at the first `assert got.action == "attach"`.
    """
    [got] = run(known={})
    assert got.action == "attach"
    [got] = run(known={RECORD["id"]: {**RECORD, "category": "other"}})
    assert got.action == "attach"
    [got] = run(known={RECORD["id"]: dict(RECORD)})
    assert got.action == "suppress"


def test_a_ledger_that_cannot_be_read_in_full_suppresses_nothing(tmp_path):
    """Security review 2026-10-22. Invariant: an unreadable ledger line may be the retraction, so
    nothing suppresses; a host with no ledger trusts the answer.

    Red 2026-10-22: mutation `if skipped:` -> `if False:` in `match_fp.ledger_facts`; failed at
    `assert blocked == "1 bug ledger line(s) cannot be read"`.
    """
    assert match_fp.ledger_facts(tmp_path) == (set(), None, None)
    (tmp_path / "bugs.jsonl").write_text('{"kind": "false_positive", "id": 3}\n',
                                         encoding="utf-8")
    retracted, known, blocked = match_fp.ledger_facts(tmp_path)
    assert blocked == "1 bug ledger line(s) cannot be read"
    [got] = run(known=known, blocked=blocked)
    assert got.action == "attach" and blocked in got.reasons


def test_an_exact_the_answer_claims_is_confirmed_against_the_checkout():
    """Push security review 2026-10-22. Invariant: with the checkout at hand, RE-call's `exact` is
    confirmed by the local hash, so a saved answer cannot vouch for code that changed.

    Red 2026-10-22: mutation `elif status == "exact":` -> `elif False:` in `match_fp._statuses`;
    failed at `assert got.action == "attach"`.
    """
    answer = edited()
    answer["records"][0]["anchor_matches"][1]["match"] = "exact"
    store = RECORD["anchors"][1]
    [got] = run(answer=answer, local=lambda a: "0" * 64 if a == store else a["code_sha"])
    assert got.action == "attach"
    assert "anchor recall/store.py Store._with_retry_shared is changed in the checkout" \
        in got.reasons


def test_a_retraction_with_an_unreadable_date_is_in_effect(tmp_path):
    """Security review 2026-10-22. Invariant: a retraction whose date does not parse counts as
    in effect; a string compare would put it after every real date and never let it act.

    Red 2026-10-22: mutation `return True` -> `return False` in the `except ValueError` of
    `match_fp._in_effect`; failed at `assert retracted == {fp.id}`.
    """
    fp = from_record(GOLDEN_FP)
    write([fp], tmp_path, "2026-10-22T00:00:00Z")
    odd = RetractionRow(kind="false_positive_retraction", project=fp.project, repo=fp.repo,
                        date="zz-not-a-date", target_id=fp.id, reason="r", retracted_by="o")
    write([odd], tmp_path, "2026-10-22T00:00:00Z")
    retracted, _, blocked = match_fp.ledger_facts(tmp_path, today="2026-10-22")
    assert blocked is None and retracted == {fp.id}


def test_prior_fp_is_refused_outside_diff_mode(git_repo):
    """Deferred minor 4, final review 2026-10-22. Invariant: Step 2.4 runs in DIFF mode only, and
    the gate holds it there: in a hunt nothing is suppressed, whatever answer the run saved.

    Red 2026-10-22: mutation `if state.mode != "DIFF" or not path.exists():` -> `if not
    path.exists():` in `pipeline_state._prior_fp_matches`; failed at `pytest.raises(...,
    match="did not suppress")` (DID NOT RAISE).
    """
    subprocess.run(["git", "-C", str(git_repo), "remote", "add", "origin",
                    "https://github.com/acme/widgets.git"], check=True)
    state = ps.start("FAST", "HUNT", root=git_repo)
    ps.record("L0.4", detail="PASS", root=git_repo)
    place = at(git_repo, ["app.py:1"])[0]["anchor"]
    l2 = [{"id": "F-1", "category": "x", "priority": "P2", "high_stakes": False,
           "file": "app.py", "line": 1, "claim": "c"}]
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 1}, output=write_output(git_repo, l2), root=git_repo)
    rec = {**RECORD, "repo": "acme/widgets", "category": "x", "anchors": [place],
           "id": "widgets:fp:r0:F-9"}
    answer = {"tenant_id": "cca", "generation_id": "g", "truncated": False, "records": [
        {**ANSWER["records"][0], "id": rec["id"], "recall_record": rec,
         "anchor_matches": [{"anchor": place, "match": "exact"}], "anchor_match": "exact"}]}
    run_dir = git_repo / ".claude" / "audits" / "runs" / state.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "fp-records.json").write_text(json.dumps(answer), encoding="utf-8")
    with pytest.raises(ps.RecordRefused, match="did not suppress"):
        _l25(git_repo, [{"id": "F-1", "verdict": "PRIOR_FP", "record_id": rec["id"]}], 1, 0)
