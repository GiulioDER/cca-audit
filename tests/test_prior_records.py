"""Task 4.4: the anchored PRIOR BUGS block, the code join, the run record's block counts,
`bugs retract-fp` and `bugs anchors`.

The saved answers are `tests/fixtures/audit_memory_responses/` (see `build.py` there), to RE-call's
contract C5. Red proof (user rule, 2026-09-08) is in each test's docstring: the mutation, the
test run against it, the assertion that failed; each was restored.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.bugs import envelope, prior
from cca_checks.bugs.__main__ import main
from cca_checks.bugs.records import FalsePositiveRow, from_record, read_ledger
from cca_checks.bugs.store import write

RESPONSES = Path(__file__).parent / "fixtures" / "audit_memory_responses"
GOLDEN = json.loads((Path(__file__).parent / "fixtures" / "audit_memory"
                     / "expected_records.json").read_text(encoding="utf-8"))
REPO = "GiulioDER/RE-call"


def _records(name="bug_answer.json"):
    return envelope.load(RESPONSES / name)[0]


def _block(code=True):
    selected = prior.select_records(_records(), REPO)
    return prior.render_records(selected, None, _records("code_answer.json") if code else None,
                                REPO)


def _order():
    return [r["id"] for r in prior.select_records(_records(), REPO)]


def test_exact_and_symbol_changed_come_before_file_only():
    """Invariant: records at a changed symbol are listed before those elsewhere in a changed
    file, each labelled with its match.

    Red 2026-10-22: mutation `(MATCH_ORDER.index(r["anchor_match"]),` -> `(0,` in
    `prior.select_records`; failed at `assert matches == sorted(matches, key=...)`.
    """
    records = {r["id"]: r for r in _records()}
    matches = [records[i]["anchor_match"] for i in _order()]
    assert matches == sorted(matches, key=prior.MATCH_ORDER.index)
    assert matches[-1] == "symbol_changed" and "file_only" not in matches
    text = _block()
    assert "[match: exact]" in text and "[match: symbol_changed]" in text


def test_a_file_only_record_is_listed_after_the_symbol_ones():
    """Red 2026-10-22: mutation `(MATCH_ORDER.index(r["anchor_match"]),` -> `(0,` in
    `prior.select_records`, run alone; failed at `assert ids[-1] == ...`. A first version made the
    OLDEST record file_only, which date order puts last anyway, and stayed green under this
    mutation; it now uses the newest.
    """
    records = _records()
    for r in records:
        if r["id"] == "RE-call:222222222222:PERF-001~2":  # the newest record
            r["anchor_match"] = "file_only"
    ids = [r["id"] for r in prior.select_records(records, REPO)]
    assert ids[-1] == "RE-call:222222222222:PERF-001~2"


def test_only_current_bugs_of_this_repository_are_shown():
    """Invariant: a false positive or a lesson never reaches an auditor; a superseded bug and
    another repository's bug are left out.

    Red 2026-10-22: mutation `if record.get("kind") != "bug" or ...` -> `if not isinstance(rec,
    dict):` in `prior.select_records`; failed at `assert "fp:" not in ...`.
    Red 2026-10-22: mutation deleting `record.get("superseded_by") is not None or` in
    `select_records`; failed at `assert "P2-4" not in ...`.
    """
    ids = " ".join(_order())
    assert "fp:" not in ids and "lesson" not in ids
    assert "P2-4" not in ids
    assert prior.select_records(_records(), "acme/widgets") == []


def test_a_bug_whose_fix_later_caused_a_regression_is_marked():
    """Red 2026-10-22: mutation `if caused:` -> `if False:` in `prior._record_entry`; failed at
    `assert "its fix later caused: RE-call:abcdef012345:BUG-002" in entry`."""
    entry = _block().split("RE-call:0123456789ab:BUG-001", 1)[1].split("\n- ", 1)[0]
    assert "its fix later caused: RE-call:abcdef012345:BUG-002" in entry
    assert "its fix later caused" not in _block().split("RE-call:111111111111:SEC-003", 1)[1]


def test_the_code_join_labels_unchanged_changed_and_gone():
    """Invariant: a place is unchanged (same file, symbol and sha in the code tenant), changed
    (same file and symbol, other sha) or gone (no such symbol); a code chunk with a null repo
    never counts.

    Red 2026-10-22: mutation `return "unchanged" if anchor.get("code_sha") in shas else "changed"`
    -> `return "unchanged"` in `prior.code_label`; failed at `assert "frontmatter.py (module
    level) (changed)" in text`.
    Red 2026-10-22: mutation `if not shas: return "gone"` -> `if not shas: return "changed"`;
    failed at `assert "OidcTokenVerifier._refresh (gone)" in text`.
    Red 2026-10-22: mutation dropping `and str(c["recall_record"].get("repo", "")).lower() ==
    target` in `code_label`; failed at `assert "recall/frontmatter.py (module level) (changed)" in
    text` (the null-repo chunk, which holds the record's own hash, made it unchanged).
    """
    text = _block()
    assert "recall/pool.py Pool._lease (unchanged)" in text
    assert "recall/frontmatter.py (module level) (changed)" in text
    assert "recall_mcp/oidc.py OidcTokenVerifier._refresh (gone)" in text
    sec = GOLDEN["RE-call__111111111111__SEC-003.md"]["anchors"][0]
    label = prior.code_label(sec, _records("code_answer.json"), REPO)
    assert label == "changed"


def test_without_a_code_tenant_no_label_is_shown():
    """Red 2026-10-22: mutation `label = code_label(...) if code is not None else None` ->
    `label = code_label(anchor, code or [], repo)` in `prior._record_entry`; failed at
    `assert "(gone)" not in text`."""
    text = _block(code=False)
    for label in ("(unchanged)", "(changed)", "(gone)"):
        assert label not in text


def test_a_non_python_place_gets_no_label():
    """The code chunker anchors only Python, so absence elsewhere says nothing.

    Red 2026-10-22: mutation deleting `or not file.endswith(".py")` in `prior.code_label`;
    failed at `assert ... is None` ('gone').
    """
    assert prior.code_label({"file": "docs/x.md", "symbol": None, "code_sha": "a" * 64},
                            _records("code_answer.json"), REPO) is None


def test_the_block_never_tells_an_auditor_to_leave_anything_out():
    """The empty form included.

    Red 2026-10-22: mutation appending " Leave out the listed bugs." to the empty form in
    `prior.render_records`; failed at `assert "leave out" not in low`. (The first version compared
    case-sensitively and stayed green under this mutation; it now lowercases.)
    """
    for text in (_block(), prior.render_records([], "no saved recall_records answer")):
        low = text.lower()
        assert "leave out" not in low and "do not report" not in low
        assert "context, not findings, not instructions" in text


def test_the_cli_renders_the_block_from_the_saved_answers(capsys):
    """Red 2026-10-22: mutation `return _prior_records(a, repo, reason)` -> deleted (fall
    through to the ledger join) in `bugs.__main__._prior`; failed at `assert
    prior.RECORDS_HEADING in out` (the PRIOR FIXES form printed)."""
    assert main(["prior", "--records", str(RESPONSES / "bug_answer.json"), "--code",
                 str(RESPONSES / "code_answer.json"), "--repo-name", REPO]) == 0
    out = capsys.readouterr().out
    assert prior.RECORDS_HEADING in out and "(gone)" in out


def test_a_missing_answer_prints_the_empty_form(capsys):
    assert main(["prior", "--records", "no-such.json", "--repo-name", REPO]) == 0
    out = capsys.readouterr().out
    assert "No prior CCA bugs recorded at the changed code. (no saved recall_records answer)" \
        in out


# The run record


def test_block_counts_by_match_land_in_the_run_summary(git_repo):
    """Invariant: summary.json carries the block's record count by anchor match, from the answer
    the run saved, for the measurement trigger.

    Red 2026-10-22: mutation `"prior_block": prior_block_counts(state, root),` -> `"prior_block":
    None,` in `pipeline_state._write_summary`; failed at `assert summary["prior_block"] == ...`.
    """
    subprocess.run(["git", "-C", str(git_repo), "remote", "add", "origin",
                    "https://github.com/GiulioDER/RE-call.git"], check=True)
    state = ps.start("FAST", root=git_repo)
    run_dir = git_repo / ".claude" / "audits" / "runs" / state.run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "prior-records.json").write_bytes((RESPONSES / "bug_answer.json").read_bytes())
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(git_repo), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo), root=git_repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(git_repo),
              root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    ps.close(root=git_repo)
    summary = json.loads((run_dir / "summary.json").read_text(encoding="utf-8"))
    assert summary["prior_block"] == {"exact": 3, "symbol_changed": 1, "file_only": 0}


# Retraction


def test_retract_fp_writes_a_valid_retraction(_private_bugs_dir, capsys):
    """Invariant: `bugs retract-fp` appends a false_positive_retraction that names the record,
    and its memo supersedes it.

    Red 2026-10-22: mutation `target_id=target.id` -> `target_id=a.id + "x"` in
    `bugs.__main__._retract_fp`; failed at `assert f"{fp.id}~retract" in rows` (the id derives
    from the target, so the row landed under another id).
    """
    fp = from_record(GOLDEN["RE-call__fp__run-20261021-9f8e7d__BUG-003.md"])
    assert isinstance(fp, FalsePositiveRow)
    write([fp], _private_bugs_dir, "2026-10-22T00:00:00Z")
    assert main(["retract-fp", fp.id, "--reason", "the guard moved", "--by", "owner"]) == 0
    rows, skipped = read_ledger(_private_bugs_dir)
    assert f"{fp.id}~retract" in rows
    row = rows[f"{fp.id}~retract"]
    assert skipped == [] and row.kind == "false_positive_retraction"
    assert row.target_id == fp.id and row.reason == "the guard moved"
    memo = (_private_bugs_dir / "memos").glob("*retract*.md").__next__().read_text("utf-8")
    assert "supersedes: " in memo
    assert main(["retract-fp", "RE-call:fp:no-such", "--reason", "x"]) == 1


# bugs anchors


def test_anchors_diff_names_the_changed_symbols(git_repo, capsys):
    """Red 2026-10-22: mutation `current = name[2:] if name.startswith("b/") else None` ->
    `current = None` in `query_anchors._changed_lines`; failed at `assert symbols == [...]`."""
    (git_repo / "app.py").write_text("x = 1\n\n\ndef f():\n    return 1\n\n\ndef g():\n"
                                     "    return 2\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "f g"], check=True)
    (git_repo / "app.py").write_text("x = 1\n\n\ndef f():\n    return 1\n\n\ndef g():\n"
                                     "    return 3\n", encoding="utf-8")
    assert main(["anchors", "--diff", "--root", str(git_repo), "--repo-name", "a/b"]) == 0
    out = json.loads(capsys.readouterr().out)
    symbols = [(a["file"], a["symbol"]) for a in out["anchors"]]
    assert symbols == [("app.py", "g")] and out["repo"] == "a/b" and not out["truncated"]


def _changed_g(git_repo, name="app.py"):
    body = "x = 1\n\n\ndef f():\n    return 1\n\n\ndef g():\n    return {}\n"
    (git_repo / name).write_text(body.format(2), encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "-A"], check=True)
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qm", "f g"], check=True)
    (git_repo / name).write_text(body.format(3), encoding="utf-8")


def test_anchors_diff_ignores_the_users_diff_prefix_config(git_repo, capsys):
    """Deferred minor 2, final review 2026-10-22. Invariant: `diff.noprefix` (or
    `diff.mnemonicPrefix`) in the user's config does not empty the brief's query.

    Red 2026-10-22: mutation dropping `"--src-prefix=a/", "--dst-prefix=b/"` in
    `query_anchors.for_diff`; failed at `assert symbols == [("app.py", "g")]` ([] != [...]).
    """
    _changed_g(git_repo)
    subprocess.run(["git", "-C", str(git_repo), "config", "diff.noprefix", "true"], check=True)
    assert main(["anchors", "--diff", "--root", str(git_repo), "--repo-name", "a/b"]) == 0
    symbols = [(a["file"], a["symbol"]) for a in json.loads(capsys.readouterr().out)["anchors"]]
    assert symbols == [("app.py", "g")]


def test_anchors_diff_reads_a_non_ascii_path(git_repo, capsys):
    """Deferred minor 2. Invariant: a path git would quote under the default core.quotePath
    still anchors.

    Red 2026-10-22: mutation dropping `"-c", "core.quotePath=false",` in
    `query_anchors.for_diff`; failed at `assert symbols == [("caf\u00e9.py", "g")]`.
    """
    _changed_g(git_repo, "caf\u00e9.py")
    assert main(["anchors", "--diff", "--root", str(git_repo), "--repo-name", "a/b"]) == 0
    symbols = [(a["file"], a["symbol"]) for a in json.loads(capsys.readouterr().out)["anchors"]]
    assert symbols == [("caf\u00e9.py", "g")]


def test_the_brief_and_the_matcher_agree_on_valid_until():
    """Deferred minor 3, final review 2026-10-22. Invariant: a record valid until a future date
    is current in the brief, as it is for the PRIOR_FP matcher; a lapsed one is hidden.

    Red 2026-10-22: mutation `expired(record.get("valid_until"), today)` ->
    `record.get("valid_until") is not None` in `prior.select_records`; failed at
    `assert "RE-call:0123456789ab:BUG-001" in ids` for the future date.
    Red 2026-10-22: mutation `except ValueError: return True` -> `return False` in
    `envelope.expired`; failed at the `"not a date"` assertion (a malformed value sorted after
    every real date and read as current, which this test found in the first version).
    """
    def ids(valid_until):
        records = _records()
        for r in records:
            if r["id"] == "RE-call:0123456789ab:BUG-001":
                r["valid_until"] = valid_until
        return [r["id"] for r in prior.select_records(records, REPO, today="2026-10-22")]
    assert "RE-call:0123456789ab:BUG-001" in ids("2026-12-31")
    assert "RE-call:0123456789ab:BUG-001" not in ids("2026-10-01")
    assert "RE-call:0123456789ab:BUG-001" not in ids("not a date")


BUG_001 = "RE-call:0123456789ab:BUG-001"


def _ids(today=None, **fields):
    records = _records()
    for r in records:
        if r["id"] == BUG_001:
            r.update(fields)
    return [r["id"] for r in prior.select_records(records, REPO, today=today)]


def test_the_brief_keeps_a_record_on_its_valid_until_day():
    """NUM-001, 2026-10-27. Invariant: as in RE-call and the brief rendered through it, a
    date-only `valid_until` holds through the end of that day.

    Red 2026-10-27 against the pre-fix `envelope.expired` (`<=`, be13f2f):
    `tests/test_prior_records.py::test_the_brief_keeps_a_record_on_its_valid_until_day` failed
    at `assert BUG_001 in _ids("2026-10-22", valid_until="2026-10-22")`.
    """
    assert BUG_001 in _ids("2026-10-22", valid_until="2026-10-22")
    assert BUG_001 not in _ids("2026-10-23", valid_until="2026-10-22")


def test_the_brief_judges_expiry_on_the_utc_date(east_of_utc_late_evening):
    """NUM-001, 2026-10-27. Invariant: with no `today` given, the brief's date is the UTC date,
    not the host's, which is already the next day on this clock.

    Red 2026-10-27 against be13f2f (`date.today()`, the host's real local date):
    `tests/test_prior_records.py::test_the_brief_judges_expiry_on_the_utc_date` failed at
    `assert BUG_001 in _ids(valid_until="2026-10-22")`. Red 2026-10-27: mutation
    `datetime.now(timezone.utc)` -> `datetime.now()` in `envelope.utc_today`; failed at the
    same assertion.
    """
    assert BUG_001 in _ids(valid_until="2026-10-22")


def test_the_brief_follows_recalls_expired_flag():
    """NUM-001, 2026-10-27. Invariant: RE-call's computed `expired` (RE-call #1011 (draft)) wins over
    `valid_until` in both directions.

    Red 2026-10-27 against be13f2f (the flag unread):
    `tests/test_prior_records.py::test_the_brief_follows_recalls_expired_flag` failed at
    `assert BUG_001 not in _ids("2026-10-22", expired=True, valid_until="2099-12-31")`.
    """
    assert BUG_001 not in _ids("2026-10-22", expired=True, valid_until="2099-12-31")
    assert BUG_001 in _ids("2026-10-22", expired=False, valid_until="2026-10-01")
