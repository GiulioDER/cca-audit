"""`pipeline close` counts the memory ids L1 findings cite against the L2.5 verdicts.

Red proof, 2026-10-31, all against the commit before this change (the tree at 0bfb8f6, with no
`memory_attribution` key in `_write_summary` and no `ids` in the hosted `prior_brief`). Each test
asserts `"memory_attribution" in summary` first, so a missing key is an AssertionError and not a
KeyError; every test ran, was collected and failed in its own assertion:

- test_hosted_brief_records_its_ids: baseline; failed at `assert summary["prior_brief"] == {...}`
  (`{"source": "hosted"}` has no `ids`).
- test_attribution_counts_citations_by_verdict: baseline; failed at
  `assert "memory_attribution" in summary`.
- test_missing_and_malformed_are_counted_apart: baseline; same assertion.
- test_a_repeated_id_counts_once: baseline; same assertion.
- test_case_variant_is_unlisted: baseline; same assertion.
- test_no_brief_and_non_diff_runs_report_null: baseline; same assertion.

- test_a_run_without_a_brief_closes_with_null: baseline; same assertion.

Fix round 1, 2026-10-31, baseline = the implementation of commit 07ca920 (`str(r["id"])` over
every selected record, no length cut); each ran, was collected and failed:

- test_a_record_without_an_id_does_not_block_close: `KeyError: 'id'` raised by that production
  line itself (`pipeline_state._hosted_brief_ids`) inside `ps.close`. The defect is the crash, so
  the exception at the production line is the intended failure, not a fixture or setup error.
- test_ids_stop_at_the_entries_the_block_keeps: failed at
  `assert summary["prior_brief"]["ids"] == printed` (eight ids against the four printed).
- test_the_printed_bug_id_is_listed_not_the_outer_id: failed at the `prior_brief` comparison
  (outer ids listed instead of `PRINTED:bug-1`).

Fix round 2, 2026-10-31, baseline = the implementation of commit 414be07 (render with
`code=None`, `prior-code.json` ignored):

- test_the_saved_code_answer_moves_the_cut: failed at `assert summary["prior_brief"]["ids"] ==
  labelled` (one more id than the labelled block prints).

Mutations on the green implementation, one per test, restored afterwards (all in
`pipeline_state.memory_attribution`; each failed in the intended assertion):

- test_case_variant_is_unlisted: `len(cited - listed)` became a lower-cased comparison; failed at
  `assert summary["memory_attribution"]["unlisted"] == 1`.
- test_missing_and_malformed_are_counted_apart: the list-of-strings check became
  `isinstance(ids, (list, str))`; failed at the (missing, malformed, findings_citing) tuple.
- test_a_repeated_id_counts_once: `unlisted` counted `ids` instead of the set `cited`; failed at
  `assert got["unlisted"] == 1`.
- test_no_brief_and_non_diff_runs_report_null: the `state.mode == "DIFF"` condition removed, with
  a local receipt in a HUNT run; failed at `assert summary["memory_attribution"] is None, mode`.
- test_a_run_without_a_brief_closes_with_null: the `if not listed` guard became `if brief is
  None`; failed at `assert summary["memory_attribution"] is None`.
- test_hosted_brief_records_its_ids: lessons ordered before bugs; failed at the `prior_brief` ids
  comparison.

Final review fix I5, 2026-10-31 (test_attribution_counts_citations_by_verdict now also expects
the two new keys):

- test_unlisted_ids_are_distinct_across_findings: baseline = this branch at 720e740 (no
  `cited_ids` or `unlisted_ids`); ran, was collected and failed at
  `assert got.get("unlisted_ids") == ["ghost"]` (None). Mutation on the green implementation:
  `unlisted_ids` built from a list that appends each finding's `cited - listed` (so a repeat
  across findings is kept); failed at the same assertion (["ghost", "ghost"]).
"""

from __future__ import annotations

import dataclasses
import json
import pathlib
import re
import subprocess

from conftest import plan_output, write_output

from cca_checks import pipeline_state as ps

RESPONSES = pathlib.Path(__file__).parent / "fixtures" / "audit_memory_responses"
BUG_ID = "RE-call:abcdef012345:BUG-002"
LESSON_ID = "RE-call:lesson:6a3ae35062b9"


def _finding(fid, **extra) -> dict:
    return {"id": fid, "auditor": "cca-bug-auditor", "category": "x", "priority": "P2",
            "file": "app.py", "line": 1, "claim": f"claim {fid}", "high_stakes": False, **extra}


def _fixture_records() -> list[dict]:
    answer = json.loads((RESPONSES / "bug_answer.json").read_text(encoding="utf-8"))
    return [r for r in answer["records"] if r["id"] in (BUG_ID, LESSON_ID)]


def _hosted_answer(run_dir, records=None) -> None:
    """Save a hosted answer holding one bug and one lesson of RE-call (a subset of the fixture),
    or `records` when given."""
    answer = json.loads((RESPONSES / "bug_answer.json").read_text(encoding="utf-8"))
    answer["records"] = _fixture_records() if records is None else records
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / ps.PRIOR_RECORDS).write_text(json.dumps(answer), encoding="utf-8")


def _open_run(repo, findings, verdicts, *, hosted=True, records=None, code=None):
    """Record L1 to L6 of a DIFF run, leaving it open; returns its state.

    `verdicts` maps finding id to verdict; findings are recorded as L2 in the order given.
    """
    subprocess.run(["git", "-C", str(repo), "remote", "add", "origin",
                    "https://github.com/GiulioDER/RE-call.git"], check=True)
    state = ps.start("FAST", root=repo)
    run_dir = repo / ".claude" / "audits" / "runs" / state.run_id
    if hosted:
        _hosted_answer(run_dir, records)
        if code is not None:
            (run_dir / ps.PRIOR_CODE).write_text(json.dumps(code), encoding="utf-8")
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": len(findings)}, output=write_output(repo, findings),
              root=repo)
    rows = [{"id": i, "verdict": v} for i, v in verdicts.items()]
    confirmed = sum(1 for v in verdicts.values() if v == "CONFIRMED")
    fp = sum(1 for v in verdicts.values() if v == "FALSE_POSITIVE")
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": fp, "uncertain": 0, "duplicate": 0},
              output=write_output(repo, rows), root=repo)
    ids = [i for i, v in verdicts.items() if v == "CONFIRMED"]
    ps.record("L3", counts={"to_fix": 0, "deferred": len(ids)},
              output=plan_output(repo, deferred=len(ids), ids=ids), root=repo)
    fps = [i for i, v in verdicts.items() if v == "FALSE_POSITIVE"]
    accepted = {"fp_acceptance": [{"id": i, "decision": "ACCEPT", "reason": "read it"}
                                  for i in fps]}
    ps.record("L6", detail="APPROVED", output=write_output(repo, accepted) if fps else None,
              root=repo)
    return state


def _run(repo, findings, verdicts, *, hosted=True, records=None, code=None) -> dict:
    """Drive a run to `pipeline close` and return its summary.json."""
    state = _open_run(repo, findings, verdicts, hosted=hosted, records=records, code=code)
    ps.close(root=repo)
    run_dir = repo / ".claude" / "audits" / "runs" / state.run_id
    return json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))


def test_hosted_brief_records_its_ids(git_repo):
    """Invariant: a hosted run's `prior_brief.ids` lists the records the block rendered, bugs
    first and lessons after, so a citation can be compared with what the auditors saw."""
    summary = _run(git_repo, [], {})
    assert summary["prior_brief"] == {"source": "hosted", "ids": [BUG_ID, LESSON_ID]}


def test_a_record_without_an_id_does_not_block_close(git_repo):
    """Invariant: a saved answer holding a record with no outer `id` closes cleanly, and the
    ids are the ones the block prints (the record's bug_id), never a KeyError at close."""
    records = _fixture_records()
    for record in records:
        if record["kind"] == "bug":
            del record["id"]
    summary = _run(git_repo, [], {}, records=records)
    assert summary["prior_brief"] == {"source": "hosted", "ids": [BUG_ID, LESSON_ID]}


def _clones(symbol_len: int) -> list[dict]:
    """Eight long bug records of RE-call, each with two anchors of a `symbol_len` symbol."""
    base = [r for r in _fixture_records() if r["kind"] == "bug"][0]
    records = []
    for n in range(8):
        clone = json.loads(json.dumps(base))
        rid = f"RE-call:{n:012x}:BUG-{n}"
        clone["id"] = clone["recall_record"]["id"] = clone["recall_record"]["bug_id"] = rid
        clone["recall_record"]["supersedes"] = None
        clone["recall_record"]["summary"] = "s" * 240
        symbol = "Sym." + "y" * symbol_len
        for anchor in clone["recall_record"]["anchors"]:
            anchor["symbol"] = symbol
        for match in clone["anchor_matches"]:
            match["anchor"]["symbol"] = symbol
        records.append(clone)
    return records


def _printed(records, code=None) -> list[str]:
    from cca_checks.bugs import prior
    selected = prior.select_records(records, "GiulioDER/RE-call")
    block = prior.render_records(selected, None, code, "GiulioDER/RE-call")
    return re.findall(r"^- \[P\d\] (\S+),", block, flags=re.MULTILINE)


def test_ids_stop_at_the_entries_the_block_keeps(git_repo):
    """Invariant: ids past the BLOCK_MAX cut are not listed, since the auditors never saw them.
    The expected ids are read back from the rendered block, not from the selection."""
    records = _clones(150)
    summary = _run(git_repo, [], {}, records=records)
    printed = _printed(records)
    assert 0 < len(printed) < 8
    assert summary["prior_brief"]["ids"] == printed


def test_the_saved_code_answer_moves_the_cut(git_repo):
    """Invariant: when Step 1 saved a `prior-code.json`, its labels lengthen the entries, so the
    listed ids are those of the labelled render, one fewer here than without the labels."""
    code = {"tenant_id": "cca", "generation_id": "g", "truncated": False, "records": []}
    for length in range(100, 300):
        records = _clones(length)
        if len(_printed(records, code["records"])) < len(_printed(records)):
            break
    else:
        raise AssertionError("no symbol length lets the labels move the cut")
    labelled, plain = _printed(records, code["records"]), _printed(records)
    assert 0 < len(labelled) < len(plain)
    summary = _run(git_repo, [], {}, records=records, code=code)
    assert summary["prior_brief"]["ids"] == labelled


def test_the_printed_bug_id_is_listed_not_the_outer_id(git_repo):
    """Invariant: the block prints `recall_record.bug_id`; a record whose outer id differs is
    listed by the printed one."""
    records = _fixture_records()
    for record in records:
        if record["kind"] == "bug":
            record["recall_record"]["bug_id"] = "PRINTED:bug-1"
    summary = _run(git_repo, [], {}, records=records)
    assert summary["prior_brief"] == {"source": "hosted", "ids": ["PRINTED:bug-1", LESSON_ID]}


def test_attribution_counts_citations_by_verdict(git_repo):
    """Invariant: findings citing memory are split by L2.5 verdict; an empty list does not cite;
    an id the brief did not list counts as unlisted."""
    findings = [_finding("BUG-001", memory_ids=[BUG_ID]),
                _finding("CODE-002", memory_ids=[]),
                _finding("SEC-003", memory_ids=[BUG_ID, "nope"])]
    summary = _run(git_repo, findings, {"BUG-001": "CONFIRMED", "CODE-002": "CONFIRMED",
                                        "SEC-003": "FALSE_POSITIVE"})
    assert "memory_attribution" in summary
    assert summary["memory_attribution"] == {
        "findings_citing": 2, "missing": 0, "malformed": 0,
        "by_verdict": {"CONFIRMED": 1, "FALSE_POSITIVE": 1, "other": 0},
        "confirmed_ids": [BUG_ID], "unlisted": 1,
        "cited_ids": sorted([BUG_ID, "nope"]), "unlisted_ids": ["nope"],
    }


def test_missing_and_malformed_are_counted_apart(git_repo):
    """Invariant: a finding without the key is `missing`, one whose value is not a list of
    strings is `malformed`, and a bare string is never iterated into characters."""
    findings = [_finding("BUG-001"), _finding("BUG-002", memory_ids="id1")]
    summary = _run(git_repo, findings, {"BUG-001": "CONFIRMED", "BUG-002": "CONFIRMED"})
    assert "memory_attribution" in summary
    got = summary["memory_attribution"]
    assert (got["missing"], got["malformed"], got["findings_citing"]) == (1, 1, 0)
    assert got["unlisted"] == 0


def test_a_repeated_id_counts_once(git_repo):
    """Invariant: the same id twice in one finding is one citation, not two unlisted ones."""
    findings = [_finding("BUG-001", memory_ids=["ghost", "ghost"])]
    summary = _run(git_repo, findings, {"BUG-001": "CONFIRMED"})
    assert "memory_attribution" in summary
    got = summary["memory_attribution"]
    assert got["findings_citing"] == 1
    assert got["unlisted"] == 1
    assert got["confirmed_ids"] == ["ghost"]


def test_unlisted_ids_are_distinct_across_findings(git_repo):
    """Invariant: `unlisted` stays a per finding count, while `cited_ids` and `unlisted_ids` are
    the sorted distinct ids, so an unlisted id two findings cite is one distinct id (the
    pre-registration's unlisted share divides by distinct cited ids)."""
    findings = [_finding("BUG-001", memory_ids=["ghost", BUG_ID]),
                _finding("BUG-002", memory_ids=["ghost"])]
    summary = _run(git_repo, findings, {"BUG-001": "CONFIRMED", "BUG-002": "FALSE_POSITIVE"})
    assert "memory_attribution" in summary
    got = summary["memory_attribution"]
    assert got["unlisted"] == 2
    assert got.get("unlisted_ids") == ["ghost"]
    assert got.get("cited_ids") == sorted([BUG_ID, "ghost"])


def test_case_variant_is_unlisted(git_repo):
    """Invariant: the comparison with the listed ids is exact, so a case variant is unlisted."""
    findings = [_finding("BUG-001", memory_ids=[BUG_ID.upper()])]
    summary = _run(git_repo, findings, {"BUG-001": "CONFIRMED"})
    assert "memory_attribution" in summary
    assert summary["memory_attribution"]["unlisted"] == 1


def test_no_brief_and_non_diff_runs_report_null(git_repo):
    """Invariant: unknown is null, never zeros: a run with no brief reports null, and so does
    a HUNT or DEFERRED run even when its brief and its findings exist."""
    findings = [_finding("BUG-001", memory_ids=[BUG_ID])]
    verdicts = {"BUG-001": "CONFIRMED"}
    state = _open_run(git_repo, findings, verdicts, hosted=False)
    run_dir = git_repo / ".claude" / "audits" / "runs" / state.run_id
    block = f"```text\nBUG {BUG_ID} [exact] app.py run: a race in run\n```"
    (run_dir / ps.LOCAL_PRIOR).write_text(json.dumps({"status": "ok", "block": block}),
                                          encoding="utf-8")
    summary_path = git_repo / ".claude" / "audits" / "runs" / state.run_id / "summary.json"
    for mode in ("HUNT", "DEFERRED"):
        ps._write_summary(dataclasses.replace(state, mode=mode), git_repo)
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
        assert "memory_attribution" in summary, mode
        assert summary["memory_attribution"] is None, mode
    ps._write_summary(state, git_repo)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["memory_attribution"] is not None


def test_a_run_without_a_brief_closes_with_null(git_repo):
    """Invariant: a DIFF run with an empty receipt writes `memory_attribution: null`."""
    summary = _run(git_repo, [_finding("BUG-001", memory_ids=[BUG_ID])],
                   {"BUG-001": "CONFIRMED"}, hosted=False)
    assert "memory_attribution" in summary
    assert summary["memory_attribution"] is None
