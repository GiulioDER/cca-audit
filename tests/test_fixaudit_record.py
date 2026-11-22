"""The fix audit journal, its side ledger and the shape of the L5.4 verdict file."""

from __future__ import annotations

import json

import pytest
from conftest import (
    _git,
    append_ledger,
    findings_output,
    load_hook,
    plan_output,
    verdicts_output,
    write_output,
)

from cca_checks import fixaudit, pipeline_state
from cca_checks import fixaudit_record as fr
from cca_checks import layer_outputs as lo
from cca_checks import pipeline_state as ps
from cca_checks.__main__ import main as cca_main
from cca_checks.bugs import ledgers

core = load_hook("cca_gate_core.py")

RUN = "run-1"


def _j(root, finding, verdict, pass_no=1, fix_id="FIX-1"):
    fr.journal(root, RUN, finding=finding, fix_id=fix_id, pass_no=pass_no, verdict=verdict,
               reason="r")


def _audit(pass_no=1):
    return {"pass": pass_no, "audited_tree": "abc", "hunks": ["h1"], "findings": [{
        "id": f"FA-{pass_no}-1", "fix_id": "FIX-1", "finding": "F-1", "priority": "P2",
        "verdict": "CONFIRMED", "file": "a.py", "line": 3, "claim": "c"}]}


def test_cycles_count_confirmed_fix_audit_findings_per_original_finding(tmp_path):
    """Invariant: only CONFIRMED rows count, per original finding.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_cycles_count_confirmed_fix_audit_findings_per_original_finding:
    `cycles` `== "CONFIRMED"` became `!= "CONFIRMED"`; failed `assert {'F-1': 1} == {'F-1': 2, 'F-2': 1}`."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "CONFIRMED", 2)
    _j(tmp_path, "F-1", "FALSE_POSITIVE", 3)
    _j(tmp_path, "F-2", "CONFIRMED", 1)
    assert fr.cycles(tmp_path, RUN) == {"F-1": 2, "F-2": 1}


def test_the_third_cycle_parks_the_finding(tmp_path):
    """Invariant: the third CONFIRMED cycle parks; a PARKED row parks outright.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_the_third_cycle_parks_the_finding:
    `parked` `n >= CAP` became `n > CAP`; the second assertion failed,
    `assert {'F-2'} == {'F-1', 'F-2'}`."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "CONFIRMED", 2)
    assert fr.parked(tmp_path, RUN) == set()
    _j(tmp_path, "F-1", "CONFIRMED", 3)
    _j(tmp_path, "F-2", "PARKED", 1)
    assert fr.parked(tmp_path, RUN) == {"F-1", "F-2"}


def test_pass_numbering_continues_after_a_pause(tmp_path):
    """Invariant: numbering is one past the highest pass journaled; an empty journal starts at 1.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_pass_numbering_continues_after_a_pause:
    `next_pass` `1 + max(` became `0 + max(`; the first assertion failed, `assert 0 == 1`."""
    assert fr.next_pass(tmp_path, RUN) == 1
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    _j(tmp_path, "F-1", "RESOLVED", 2)
    assert fr.next_pass(tmp_path, RUN) == 3


def test_side_ledger_rows_never_reach_the_disposition_ledger(tmp_path):
    """Invariant: fix audit rows go to the side ledger, never the disposition ledger.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_side_ledger_rows_never_reach_the_disposition_ledger:
    `append_verdicts` wrote to AUDITOR_SCORECARD.jsonl instead of `LEDGER`; failed
    `assert ({'CONFIRMED': 2}, 0) == ({'CONFIRMED': 1}, 0)`."""
    scorecard = tmp_path / pipeline_state.LEDGER
    scorecard.parent.mkdir(parents=True)
    scorecard.write_text(json.dumps({"run_id": RUN, "verdict": "CONFIRMED", "fix_id": "FIX-1"})
                         + "\n", encoding="utf-8")
    assert fr.append_verdicts(tmp_path, RUN, _audit()) == 1
    assert pipeline_state._ledger_rows(tmp_path, RUN) == ({"CONFIRMED": 1}, 0)
    runs = ledgers.load_ledger(scorecard, "r", ledgers.LoadReport())
    assert "FA-1-1" not in json.dumps([r.__dict__ for r in runs], default=str)
    side = (tmp_path / fr.LEDGER).read_text(encoding="utf-8").splitlines()
    row = json.loads(side[0])
    assert (row["run_id"], row["layer"], row["id"]) == (RUN, "L5.4", "FA-1-1")


@pytest.mark.parametrize("mutate", [
    lambda a: a["findings"][0].update(id="FA-2-1"),
    lambda a: a["findings"][0].update(id="F-1"),
    lambda a: a["findings"][0].update(id="FA-01-1"),
    lambda a: a["findings"][0].update(verdict="DUPLICATE"),
    lambda a: a["findings"][0].update(priority="P0"),
    lambda a: a.update({"pass": 0, "findings": []}),
    lambda a: a.update({"hunks": ["h1", "h1"]}),
    lambda a: a.update({"hunks": [""]}),
    lambda a: a["findings"][0].update(verdict="RESOLVED"),
    lambda a: a["findings"][0].update(verdict="RESOLVED", resolves="FA-1-1"),
], ids=["wrong-pass", "bad-prefix", "leading-zero", "verdict", "priority", "pass-zero",
        "dup-hunk", "empty-hunk", "resolved-names-nothing", "resolves-its-own-pass"])
def test_a_malformed_verdict_file_is_refused(mutate):
    """Each case is refused by its own line of `l54_audit`; the good file passes first.
    Red 2026-11-22, each `tests/test_fixaudit_record.py::test_a_malformed_verdict_file_is_refused[<id>]`
    failed `Failed: DID NOT RAISE ShapeError` under its mutation in layer_outputs.py:
    wrong-pass: removed `or int(match.group(1)) != pass_no`; bad-prefix: `if not match or` became
    `if match and`; leading-zero: `_FA_ID` loosened to `FA-([0-9]+)-[0-9]+`; verdict and priority:
    their `if` became `if False:`; pass-zero: removed `or pass_no < 1`; dup-hunk: removed the
    `len(set(hunks))` test; empty-hunk: dropped `and h`. resolved-names-nothing and
    resolves-its-own-pass: `if not target or int(target.group(1)) >= pass_no:` became `if False:`."""
    assert lo.l54_audit(_audit())["pass"] == 1
    bad = _audit()
    mutate(bad)
    with pytest.raises(lo.ShapeError):
        lo.l54_audit(bad)


def test_a_torn_journal_line_does_not_swallow_the_next_row(tmp_path):
    """A crash left half a line; the next append starts on a new line, so the fragment stays
    its own line and the later CONFIRMED row is not lost with it.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_torn_journal_line_does_not_swallow_the_next_row:
    `_append` `if torn:` became `if False:`; the last line then held the fragment and the new row
    joined, so `json.loads(lines[-1])` raised JSONDecodeError at the test's own parse of the row."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    path = tmp_path / ".claude" / "audits" / "runs" / RUN / fr.JOURNAL
    with path.open("ab") as handle:
        handle.write(b'{"finding": "F-1", "ver')
    _j(tmp_path, "F-2", "CONFIRMED", 2)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[-1])["finding"] == "F-2"
    assert lines[1] == '{"finding": "F-1", "ver'


def test_a_malformed_journal_line_stops_the_run_naming_file_and_line(tmp_path):
    """Deliberately fail closed: skipping the line could hide a CONFIRMED cycle.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_malformed_journal_line_stops_the_run_naming_file_and_line:
    `_rows` `raise ValueError(...)` became `continue`; failed `DID NOT RAISE ValueError`."""
    _j(tmp_path, "F-1", "CONFIRMED", 1)
    path = tmp_path / ".claude" / "audits" / "runs" / RUN / fr.JOURNAL
    with path.open("ab") as handle:
        handle.write(b"not json\n")
    with pytest.raises(ValueError, match=r"fixaudit-journal\.jsonl line 2"):
        fr.cycles(tmp_path, RUN)


# --- check and record ---------------------------------------------------------------------


def _put(repo, name, text):
    path = repo / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _commit(repo, files):
    for name, text in files.items():
        _put(repo, name, text)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "before the run")


def _entry(n, files, **extra):
    return {"fix_id": f"FIX-{n}", "finding": f"F-{n}", "files": files,
            "sibling_patterns": [], "sibling_reason": "the call heads cover it", **extra}


def _to_l5(repo, tier, fixes, edit):
    """A run of `tier` through L5: L3 recorded, `edit` applied, L4 storing `fixes`, L5 green."""
    n = len(fixes)
    state = ps.start(tier, root=repo)
    ps.record("L1", detail="auditors", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": n}, output=findings_output(repo, n), root=repo)
    append_ledger(repo, state.run_id, n)
    ps.record("L2.5", counts={"confirmed": n, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo, confirmed=n), root=repo)
    if tier != "FAST":
        ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": n}, output=plan_output(repo, to_fix=n), root=repo)
    edit()
    ps.record("L4", fixes=n, output=write_output(repo, fixes), root=repo)
    ps.record("L5", detail="green", root=repo)
    return state.run_id


def _check(repo):
    return fixaudit.check_report(repo, ps.load(repo))


def _fa(fa_id, verdict="CONFIRMED", fix=1, **extra):
    return {"id": fa_id, "fix_id": f"FIX-{fix}", "finding": f"F-{fix}", "priority": "P2",
            "verdict": verdict, "file": "app.py", "line": 1, "claim": "c", **extra}


def _verdicts(repo, pass_no, findings, hunks=None):
    """The verdict file of an auditor pass over the current report."""
    report = _check(repo)
    _put(repo, fr.AUDIT.as_posix(), json.dumps({
        "pass": pass_no, "audited_tree": report["tree"],
        "hunks": report["delta_hunks"] if hunks is None else hunks, "findings": findings}))
    return report


def _edit_and_test(repo, name, text):
    _put(repo, name, text)
    ps.record("L5", detail="green", root=repo)


def _app_run(repo, tier):
    return _to_l5(repo, tier, [_entry(1, ["app.py"])], lambda: _put(repo, "app.py", "x = 2\n"))


def _side_ledger(repo):
    path = repo / fr.LEDGER
    if not path.exists():
        return []
    return [json.loads(line)["id"] for line in path.read_text(encoding="utf-8").splitlines()]


def test_record_refuses_a_stale_report(git_repo):
    """Invariant: L5.4 is recorded only against a report written at the current tree.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_record_refuses_a_stale_report:
    `_evaluate` `elif json.loads(stored.read_bytes()).get("tree") != tree:` became
    `elif False:`; failed `assert any("fixaudit.json is stale" in r ...)` (AssertionError: [])."""
    _app_run(git_repo, "FAST")
    _check(git_repo)
    _edit_and_test(git_repo, "app.py", "x = 3\n")
    reasons = fr.record(git_repo)
    assert any("fixaudit.json is stale" in r for r in reasons), reasons
    _check(git_repo)
    assert fr.record(git_repo) == []


def test_record_refuses_an_undisposed_sibling(git_repo):
    """Invariant: every sibling hit needs a disposition before L5.4, and the accepted
    dispositions are copied into the run.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_record_refuses_an_undisposed_sibling:
    `_evaluate` `for hit in report["siblings"]["hits"]:` became `for hit in []:`; failed
    `assert [] == ['FIX-1: sibling readers.py:2: S-...: no disposition']`. Second mutation: `record`
    `pipeline_state._store_run_file(state, SIBLINGS.name, ...)` became `pass`; failed
    `assert kept.is_file()`."""
    _commit(git_repo, {"store.py": 'def load(p):\n    return p.read_text(encoding="utf-8")\n',
                       "readers.py": 'def other(q):\n    return q.read_text(encoding="utf-8")\n'})
    run = _to_l5(git_repo, "FAST", [_entry(1, ["store.py"])], lambda: _put(
        git_repo, "store.py",
        'def load(p):\n    return p.read_text(encoding="utf-8", errors="replace")\n'))
    hits = _check(git_repo)["siblings"]["hits"]
    assert [(h["path"], h["line"]) for h in hits] == [("readers.py", 2)]
    reasons = fr.record(git_repo)
    assert reasons == [f"FIX-1: sibling readers.py:2: {hits[0]['hit_id']}: no disposition"]
    disposition = {hits[0]["hit_id"]: {"disposition": "not_applicable",
                                       "reason": "reads a file this repo writes"}}
    _put(git_repo, fr.SIBLINGS.as_posix(), json.dumps(disposition))
    assert fr.record(git_repo) == []
    kept = git_repo / ".claude" / "audits" / "runs" / run / fr.SIBLINGS.name
    assert kept.is_file()
    assert json.loads(kept.read_text(encoding="utf-8")) == disposition


def test_record_refuses_an_unresolved_confirmed_p2_on_standard(git_repo):
    """Invariant: on STANDARD a confirmed P2 in the fix code blocks L5.4 until a later pass, at
    an edited tree and over the hunks changed since, re-verifies it RESOLVED.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_record_refuses_an_unresolved_confirmed_p2_on_standard:
    `BLOCKING = ("P1", "P2")` became `("P1",)`; failed `assert any(r.startswith("FA-1-1 (P2, FIX-1)
    was confirmed in pass 1") ...)` (AssertionError: []). Second mutation: `build_report`
    `if passes else hunks` became `if False else hunks`; failed
    `assert ['app.py:1', 'extra.py:1'] == ['extra.py:1']`."""
    run = _app_run(git_repo, "STANDARD")
    first = _verdicts(git_repo, 1, [_fa("FA-1-1")])
    assert first["delta_hunks"] == [h["id"] for h in first["hunks"]] == ["app.py:1"]
    reasons = fr.record(git_repo)
    assert any(r.startswith("FA-1-1 (P2, FIX-1) was confirmed in pass 1") for r in reasons), reasons
    assert "L5.4" not in ps.load(git_repo).layers
    _edit_and_test(git_repo, "extra.py", "y = 3\n")
    second = _verdicts(git_repo, 2, [_fa("FA-2-1", "RESOLVED", resolves="FA-1-1")])
    assert [h["id"] for h in second["hunks"]] == ["app.py:1", "extra.py:1"]
    assert second["delta_hunks"] == ["extra.py:1"]  # app.py is as pass 1 audited it
    assert fr.record(git_repo) == []
    assert ps.load(git_repo).layers["L5.4"]["detail"].startswith("pass 2:")
    assert _side_ledger(git_repo) == ["FA-1-1", "FA-2-1"]
    assert sorted(fixaudit.kept_passes(git_repo, run)) == [1, 2]


def test_fast_records_without_an_auditor_file_once_checks_pass(git_repo, capsys):
    """Invariant: FAST records L5.4 from the deterministic checks alone, through the CLI, and
    refuses a verdict file it has no pass for.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_fast_records_without_an_auditor_file_once_checks_pass:
    `_evaluate` `if state.tier == "FAST":` became `if False:`; failed
    `assert cca_main(["fixaudit", "record", ...]) == 0` (2 == 0). Second mutation: the FAST
    `reasons.append("a FAST run has no auditor pass ...")` became `pass`; failed the last assertion,
    `assert [] == ['a FAST run has no auditor pass: record L5.4 without --audit']`."""
    _app_run(git_repo, "FAST")
    root = ["--root", str(git_repo)]
    assert cca_main(["fixaudit", "check", *root]) == 0
    assert json.loads(capsys.readouterr().out)["delta_hunks"] == ["app.py:1"]
    assert cca_main(["fixaudit", "record", *root]) == 0
    entry = ps.load(git_repo).layers["L5.4"]
    assert (entry["source"], entry["tree"]) == ("tool", core.fingerprint(git_repo))
    assert not (git_repo / fr.AUDIT).exists()
    assert fr.record(git_repo, fr.AUDIT) == [
        "a FAST run has no auditor pass: record L5.4 without --audit"]


def test_an_edit_after_l54_makes_l5_and_l54_owed_again(git_repo):
    """Invariant: L5.4 is a fix verdict, stamped with its tree, so an edit makes it owed again.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_an_edit_after_l54_makes_l5_and_l54_owed_again:
    cca_gate_core.py `FIX_VERDICT_LAYERS` without "L5.4"; failed
    `assert ['L5'] == ['L5', 'L5.4']`."""
    _app_run(git_repo, "FAST")
    _check(git_repo)
    assert fr.record(git_repo) == []
    assert ps.outstanding(ps.load(git_repo), git_repo) == ["L6 has no recorded verdict (re-read Step 6)"]
    _put(git_repo, "app.py", "x = 4\n")
    stale = [r.split(" ", 1)[0] for r in ps.outstanding(ps.load(git_repo), git_repo)
             if "before the latest edit" in r]
    assert stale == ["L5", "L5.4"]


def test_a_new_pass_must_cover_every_hunk_changed_since_the_last(git_repo):
    """Invariant: a pass that skips a changed hunk is refused and not kept.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_new_pass_must_cover_every_hunk_changed_since_the_last:
    `_pass_reasons` `if missing:` became `if False:`; failed
    `assert [] == ['L5.4 pass 1 does not cover the changed hunks app.py:1']`."""
    run = _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [], hunks=[])
    assert fr.record(git_repo) == ["L5.4 pass 1 does not cover the changed hunks app.py:1"]
    assert fixaudit.kept_passes(git_repo, run) == {}


def test_resolved_needs_an_edit_since_the_confirming_pass(git_repo):
    """Invariant: nothing edited means nothing fixed, so RESOLVED at the same tree is refused.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_resolved_needs_an_edit_since_the_confirming_pass:
    `_pass_reasons` `elif passes[p]["audited_tree"] == data["audited_tree"]:` became
    `elif False:`; failed `assert any("nothing was edited since" in r ...)` (AssertionError: [])."""
    _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [_fa("FA-1-1")])
    fr.record(git_repo)
    _verdicts(git_repo, 2, [_fa("FA-2-1", "RESOLVED", resolves="FA-1-1")])
    reasons = fr.record(git_repo)
    assert any("nothing was edited since" in r for r in reasons), reasons


def test_a_refused_or_retried_record_appends_each_pass_once(git_repo):
    """Invariant: a refused record appends nothing to the side ledger, a retry never duplicates
    a pass's rows, and a kept pass number is never reused.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_refused_or_retried_record_appends_each_pass_once:
    three mutations of `record` and `_pass_reasons`. `append_verdicts(root, state.run_id, keep)`
    added after the pass file is kept: failed `assert ['FA-1-1'] == []`. `if k != latest + 1:`
    became `if False:`: failed `assert [] == ['L5.4 is numbered pass 1, ...: number it 2', ...]`.
    `if k not in logged:` became `if True:`: failed
    `assert ['FA-1-1', 'FA-2-1', 'FA-1-1', 'FA-2-1'] == ['FA-1-1', 'FA-2-1']`."""
    run = _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [_fa("FA-1-1")])
    assert fr.record(git_repo) != []
    assert fr.record(git_repo) != []  # the same file again
    assert _side_ledger(git_repo) == []
    kept = fixaudit.kept_passes(git_repo, run)[1]
    _verdicts(git_repo, 1, [])  # pass 1 again, with its finding dropped
    assert fr.record(git_repo) == [
        "L5.4 is numbered pass 1, but the last kept pass is 1: number it 2",
        "FA-1-1 (P2, FIX-1) was confirmed in pass 1 and is unresolved: fix it and have the next "
        "pass re-verify it RESOLVED, or park F-1"]
    assert fixaudit.kept_passes(git_repo, run)[1] == kept
    _edit_and_test(git_repo, "app.py", "x = 5\n")
    _verdicts(git_repo, 2, [_fa("FA-2-1", "RESOLVED", resolves="FA-1-1")])
    assert fr.record(git_repo) == []
    assert fr.record(git_repo) == []
    assert _side_ledger(git_repo) == ["FA-1-1", "FA-2-1"]


def test_the_journal_command_validates_its_row(git_repo):
    """Invariant: `fixaudit journal` refuses an unknown verdict, a pass not kept and a finding L4
    does not fix, and appends a valid row.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_the_journal_command_validates_its_row:
    each check of `_journal_cli` became `if False:` in turn; the verdict one failed
    `assert journal(**{"--verdict": "CONFIRMD"}) == 2` (0 == 2), the pass one
    `assert journal(**{"--pass": "2"}) == 2` (0 == 2), the L4 one
    `assert journal(**{"--finding": "F-9"}) == 2` (0 == 2)."""
    run = _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [_fa("FA-1-1")])
    fr.record(git_repo)

    def journal(**changed):
        row = {"--finding": "F-1", "--fix-id": "FIX-1", "--pass": "1",
               "--verdict": "CONFIRMED", "--reason": "r", **changed}
        return fr.main(["journal", "--root", str(git_repo),
                        *(x for kv in row.items() for x in kv)])

    assert journal(**{"--verdict": "CONFIRMD"}) == 2
    assert journal(**{"--pass": "2"}) == 2
    assert journal(**{"--finding": "F-9"}) == 2
    assert fr.cycles(git_repo, run) == {}
    assert journal() == 0
    assert fr.cycles(git_repo, run) == {"F-1": 1}


def test_a_parked_finding_no_longer_blocks(git_repo):
    """Invariant: a confirmed finding whose original finding the journal parks stops blocking.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_parked_finding_no_longer_blocks:
    `_unresolved` dropped `and row["finding"] not in held`; failed the last
    `assert ['FA-1-1 (P2, FIX-1) was confirmed in pass 1 and is unresolved: ...'] == []`."""
    _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [_fa("FA-1-1")])
    assert fr.record(git_repo) != []
    assert fr.main(["journal", "--root", str(git_repo), "--finding", "F-1", "--fix-id", "FIX-1",
                    "--pass", "1", "--verdict", "PARKED", "--reason", "fix reverted"]) == 0
    assert fr.record(git_repo) == []


# The seven fix-induced findings of run 20261120T225224Z, as (case, the prefix of the reason
# that names it). FIX-1 to FIX-3 leave a sibling, FIX-4 and FIX-5 add a mechanism, and the
# auditors confirm a defect in FIX-6 (CODE-001) and FIX-7 (DOC-001).
_SEVEN = (("BUG-001", "FIX-1: sibling"), ("SEC-001", "FIX-2: sibling"),
          ("NUM-001", "FIX-3: sibling"), ("BUG-002", "FIX-4: adds timeout"),
          ("BUG-003", "FIX-5: adds parser"), ("CODE-001", "FA-1-1 ("), ("DOC-001", "FA-1-2 ("))


def _blockers(reasons):
    return {case for case, prefix in _SEVEN if any(r.startswith(prefix) for r in reasons)}


def test_the_seven_case_fixture_blocks_until_each_is_resolved(git_repo):
    """Invariant: each of the seven blocks L5.4 on its own, and clearing one clears only it.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_the_seven_case_fixture_blocks_until_each_is_resolved:
    `_unresolved` `row["id"] not in cleared` became `not cleared` (any RESOLVED clears
    all); failed at the CODE-001 step, `assert set() == {'DOC-001'}`. Second mutation:
    `finding_auditors` dropped `.removeprefix("cca-")`; failed
    `assert ['cca-bug-auditor', 'bug-auditor', ...] == ['bug-auditor', 'bug-auditor:DAT', 'perf-auditor']`."""
    _commit(git_repo, {
        "store.py": 'def load(p):\n    return p.read_text(encoding="utf-8")\n',
        "readers.py": 'def other(q):\n    return q.read_text(encoding="utf-8")\n',
        "gitread.py": 'def git(args):\n    exe = resolve_tool("git", here())\n    return run(exe)\n',
        "github.py": 'def gh(args):\n    exe = resolve_tool("git", top())\n    return run(exe)\n',
        "deferred.py": "def parent(run):\n    return read_summary(run)\n",
        "report.py": "def show(run):\n    return read_summary(run)\n",
        "proof.py": "def run(cmd):\n    return go(cmd)\n",
        "dedupe.py": "def ids(rows):\n    return rows\n",
        "util.py": "A = 1\n", "README.md": "doc\n",
        "tests/test_mech.py": "def test_timeout_fires():\n    pass\n\n\n"
                              "def test_malformed_rows():\n    pass\n",
    })

    def fix():
        _put(git_repo, "store.py",
             'def load(p):\n    return p.read_text(encoding="utf-8", errors="replace")\n')
        _put(git_repo, "gitread.py", 'def git(args):\n    exe = resolve_tool("git", here())\n'
             "    if inside(exe):\n        raise SystemExit(1)\n    return run(exe)\n")
        _put(git_repo, "deferred.py", "def parent(run):\n    return read_summary(run) or state(run)\n")
        _put(git_repo, "proof.py", "def run(cmd):\n    return go(cmd, timeout=5)\n")
        _put(git_repo, "dedupe.py", "def ids(rows):\n    return set(json.loads(rows))\n")
        _put(git_repo, "util.py", "A = 2\n")
        _put(git_repo, "README.md", "doc, still without the caveat\n")

    fixes = [_entry(1, ["store.py"]), _entry(2, ["gitread.py"]),
             _entry(3, ["deferred.py"], sibling_patterns=[r"re:read_summary\("]),
             _entry(4, ["proof.py"]), _entry(5, ["dedupe.py"]), _entry(6, ["util.py"]),
             _entry(7, ["README.md"])]
    run = _to_l5(git_repo, "STANDARD", fixes, fix)
    report = _verdicts(git_repo, 1, [_fa("FA-1-1", fix=6), _fa("FA-1-2", fix=7)])
    assert report["auditors"] == ["bug-auditor", "bug-auditor:DAT", "perf-auditor"]
    hits = {h["fix_id"]: h for h in report["siblings"]["hits"]}
    assert {k: (h["path"], h["line"]) for k, h in hits.items()} == {
        "FIX-1": ("readers.py", 2), "FIX-2": ("github.py", 2), "FIX-3": ("report.py", 2)}
    remaining = {case for case, _ in _SEVEN}
    assert _blockers(fr.record(git_repo)) == remaining

    disposed = {}
    for case, n in (("BUG-001", 1), ("SEC-001", 2), ("NUM-001", 3)):
        disposed[hits[f"FIX-{n}"]["hit_id"]] = {"disposition": "not_applicable", "reason": "r"}
        _put(git_repo, fr.SIBLINGS.as_posix(), json.dumps(disposed))
        remaining.discard(case)
        assert _blockers(fr.record(git_repo)) == remaining
    for case, n, test in (("BUG-002", 4, "test_timeout_fires"), ("BUG-003", 5, "test_malformed_rows")):
        fixes[n - 1]["failure_test"] = f"tests/test_mech.py::{test}"
        ps.record("L4", fixes=7, output=write_output(git_repo, fixes), root=git_repo)
        remaining.discard(case)
        assert _blockers(fr.record(git_repo)) == remaining
    assert sorted(fixaudit.kept_passes(git_repo, run)) == [1]

    for case, n, path, text in (("CODE-001", 1, "util.py", "A = 2  # the one helper\n"),
                                ("DOC-001", 2, "README.md", "doc, with the caveat\n")):
        _edit_and_test(git_repo, path, text)
        _verdicts(git_repo, n + 1, [_fa(f"FA-{n + 1}-1", "RESOLVED", fix=5 + n,
                                        resolves=f"FA-1-{n}")])
        remaining.discard(case)
        reasons = fr.record(git_repo)
        assert _blockers(reasons) == remaining
    assert reasons == []
    assert ps.load(git_repo).layers["L5.4"]["source"] == "tool"
