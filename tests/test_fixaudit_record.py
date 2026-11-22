"""The fix audit journal, its side ledger and the shape of the L5.4 verdict file."""

from __future__ import annotations

import json
import subprocess

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
        "hunks": [h["id"] for h in report["delta_hunks"]] if hunks is None else hunks,
        "findings": findings}))
    return report


def _edit_and_test(repo, name, text):
    _put(repo, name, text)
    ps.record("L5", detail="green", root=repo)


def _app_run(repo, tier):
    return _to_l5(repo, tier, [_entry(1, ["app.py"])], lambda: _put(repo, "app.py", "x = 2\n"))


def _two_fix_run(repo):
    """A STANDARD run whose FIX-1 edits app.py and FIX-2 edits lib.py."""
    _commit(repo, {"lib.py": "y = 1\n"})

    def edit():
        _put(repo, "app.py", "x = 2\n")
        _put(repo, "lib.py", "y = 2\n")

    return _to_l5(repo, "STANDARD", [_entry(1, ["app.py"]), _entry(2, ["lib.py"])], edit)


def _journal(repo, verdict, pass_no, finding="F-1", fix_id="FIX-1", reason="r"):
    return fr.main(["journal", "--root", str(repo), "--finding", finding, "--fix-id", fix_id,
                    "--pass", str(pass_no), "--verdict", verdict, "--reason", reason])


def _three_cycles(repo, **fa):
    """Passes 1 to 3 each confirm a P2 in FIX-1, journaled, with an edit of app.py between."""
    for k in (1, 2, 3):
        if k > 1:
            _edit_and_test(repo, "app.py", f"x = {k + 1}\n")
        _verdicts(repo, k, [_fa(f"FA-{k}-1", **fa)])
        assert fr.record(repo) != []
        assert _journal(repo, "CONFIRMED", k) == 0


def _parked_but_present(k):
    return f"FA-{k}-1 (P2, FIX-1): F-1 is parked, but FIX-1 is still in the tree; revert that fix whole"


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
    disposition = {"run_id": run, "hits": {hits[0]["hit_id"]: {
        "disposition": "not_applicable", "reason": "reads a file this repo writes"}}}
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
    was confirmed in pass 1") ...)` (AssertionError: []). Round 1 mutations of `build_report`:
    `if passes else hunks` became `if False else hunks`, and separately the delta took its
    text from the fix hunk of the same id; each failed the delta assertion,
    `('app.py:1', ('x = 1',), ('x = 7',)) != ('app.py:1', ('x = 2',), ('x = 7',))`."""
    run = _app_run(git_repo, "STANDARD")
    first = _verdicts(git_repo, 1, [_fa("FA-1-1")])
    assert first["delta_hunks"] == first["hunks"] and first["hunks"][0]["id"] == "app.py:1"
    reasons = fr.record(git_repo)
    assert any(r.startswith("FA-1-1 (P2, FIX-1) was confirmed in pass 1") for r in reasons), reasons
    assert "L5.4" not in ps.load(git_repo).layers
    _edit_and_test(git_repo, "app.py", "x = 7\n")
    second = _verdicts(git_repo, 2, [_fa("FA-2-1", "RESOLVED", resolves="FA-1-1")])
    assert [(h["id"], h["removed"]) for h in second["hunks"]] == [("app.py:1", ("x = 1",))]
    # the diff since the tree pass 1 audited, with its text, not the whole fix again
    assert [(h["id"], h["removed"], h["added"]) for h in second["delta_hunks"]] == [
        ("app.py:1", ("x = 2",), ("x = 7",))]
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


def test_a_kept_pass_tree_is_pinned_so_gc_cannot_prune_it(git_repo):
    """Invariant: each kept pass's audited tree is pinned as refs/cca/<run_id>/pass-<k>, so
    `git gc` keeps the tree the next pass's delta and the red proofs restore.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_kept_pass_tree_is_pinned_so_gc_cannot_prune_it:
    with `record`'s `pipeline_state.pin_tree(state, f"pass-{keep['pass']}", ...)` mutated to
    `pass` (as at 2b269bd, which pinned nothing), failed `assert (128, '') == (0, 'tree')`."""
    run = _app_run(git_repo, "STANDARD")
    tree = _verdicts(git_repo, 1, [])["tree"]
    assert fr.record(git_repo) == []

    def git(*args):
        proc = subprocess.run(["git", "-C", str(git_repo), *args], capture_output=True,
                              text=True)
        return proc.returncode, proc.stdout.strip()

    git("gc", "--prune=now", "--quiet")
    assert git("cat-file", "-t", tree) == (0, "tree")
    assert git("rev-parse", f"refs/cca/{run}/pass-1") == (0, tree)


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
    `assert ['FA-1-1', 'FA-2-1', 'FA-1-1', 'FA-2-1'] == ['FA-1-1', 'FA-2-1']`. Round 1 (dedup now
    from fixaudit-appended.json): `if k in appended:` became `if False:`; failed
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
        "pass re-verify it RESOLVED"]
    assert fixaudit.kept_passes(git_repo, run)[1] == kept
    _edit_and_test(git_repo, "app.py", "x = 5\n")
    _verdicts(git_repo, 2, [_fa("FA-2-1", "RESOLVED", resolves="FA-1-1")])
    assert fr.record(git_repo) == []
    assert fr.record(git_repo) == []
    assert _side_ledger(git_repo) == ["FA-1-1", "FA-2-1"]
    appended = git_repo / ".claude" / "audits" / "runs" / run / fr.APPENDED
    assert json.loads(appended.read_text(encoding="utf-8")) == [1, 2]


def test_a_torn_line_in_the_shared_ledger_does_not_block_record(git_repo):
    """Invariant: record never reads the shared side ledger, so another run's torn line cannot
    block it, and the new rows still start on their own line.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_torn_line_in_the_shared_ledger_does_not_block_record:
    `record` read the shared ledger (`json.loads` per line) instead of fixaudit-appended.json;
    failed `assert fr.main(["record", ...]) == 0` (2 == 0)."""
    ledger = git_repo / fr.LEDGER
    ledger.parent.mkdir(parents=True)
    ledger.write_bytes(b'{"run_id": "other", "ver')
    _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [_fa("FA-1-1", priority="P3")])
    assert fr.main(["record", "--root", str(git_repo)]) == 0
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert lines[0] == '{"run_id": "other", "ver'
    assert [json.loads(line)["id"] for line in lines[1:]] == ["FA-1-1"]


def test_the_journal_command_validates_its_row(git_repo):
    """Invariant: `fixaudit journal` refuses an unknown verdict, a pass not kept, a finding L4
    does not fix, a CONFIRMED for a pass that confirmed nothing in that fix, a PARKED before the
    cap, and a second row for the same finding and pass; it appends a valid row.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_the_journal_command_validates_its_row:
    each check of `_journal_cli` became `if False:` in turn; the verdict one failed
    `assert journal(**{"--verdict": "CONFIRMD"}) == 2` (0 == 2), the pass one
    `assert journal(**{"--pass": "2"}) == 2` (0 == 2), the L4 one
    `assert journal(**{"--finding": "F-9"}) == 2` (0 == 2). Round 1: the duplicate check became `if False:`: failed
    `assert _journal(git_repo, "CONFIRMED", 1) == 2` (0 == 2, the same cycle twice); the CONFIRMED
    check became `if False and ...`: failed `assert _journal(..., finding="F-2", fix_id="FIX-2") == 2`
    (0 == 2); round 2: the duplicate check, now also covered for CONFIRMED by the same-tree
    check, was re-proven with a FALSE_POSITIVE row: failed `assert _journal(git_repo,
    "FALSE_POSITIVE", 1) == 2` (0 == 2); the PARKED cap check became `if False:`: failed `assert _journal(git_repo, "PARKED", 1)
    == 2` (0 == 2). The pass check, re-proven with a FALSE_POSITIVE row because the CONFIRMED check
    also refuses an unkept pass: failed `assert _journal(git_repo, "FALSE_POSITIVE", 2) == 2` (0 == 2)."""
    run = _two_fix_run(git_repo)
    _verdicts(git_repo, 1, [_fa("FA-1-1")])
    fr.record(git_repo)
    assert _journal(git_repo, "CONFIRMD", 1) == 2
    assert _journal(git_repo, "FALSE_POSITIVE", 2) == 2  # not CONFIRMED: only the pass check
    assert _journal(git_repo, "CONFIRMED", 1, finding="F-9") == 2
    assert _journal(git_repo, "CONFIRMED", 1, finding="F-2", fix_id="FIX-2") == 2
    assert _journal(git_repo, "PARKED", 1) == 2  # parked with one cycle, under the cap
    # the kept pass 1 is a cycle without a journal row (STAKES-001), and refused rows add none
    assert fr.cycles(git_repo, run) == {"F-1": 1}
    assert _journal(git_repo, "CONFIRMED", 1) == 0
    assert _journal(git_repo, "CONFIRMED", 1) == 2  # the same cycle counted twice
    assert _journal(git_repo, "FALSE_POSITIVE", 1) == 2  # any second row: only the duplicate rule
    assert fr.cycles(git_repo, run) == {"F-1": 1}


def test_a_parked_finding_blocks_until_its_fix_is_reverted(git_repo):
    """Invariant: at the cap a finding is parked, but its confirmed defects keep blocking until
    every file its fix alone lists is back to prefix_tree; then PARKED may be journaled.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_parked_finding_blocks_until_its_fix_is_reverted:
    `elif not _reverted(row, fixes, hunks):` became `elif False:`, and separately `_reverted`
    `if alone & changed:` became `if False:`; each failed
    `assert [] == [_parked_but_present(k) for k in (1, 2, 3)]`. `if row["finding"] not in held:`
    became `if True:`; failed the same assertion with 'FA-1-1 ... is unresolved ...' in its place.
    Round 2: `_reverted` is now file level, so `elif not _reverted(row, fixes, changed):` as
    `elif False:` and the held mutation were re-run, with the same failures."""
    run = _app_run(git_repo, "STANDARD")
    _three_cycles(git_repo, line=50)  # outside every hunk: only the file clause can block
    assert fr.parked(git_repo, run) == {"F-1"}
    assert fr.record(git_repo) == [_parked_but_present(k) for k in (1, 2, 3)]
    _edit_and_test(git_repo, "app.py", "x = 1\n")  # FIX-1 reverted whole
    _verdicts(git_repo, 4, [])
    assert fr.record(git_repo) == []
    assert _journal(git_repo, "PARKED", 4, reason="reverted at the cap") == 0


def test_a_parked_finding_blocks_while_its_line_is_in_a_fix_hunk(git_repo):
    """Invariant: a parked finding in a file still in the fix diff (here FIX-2's lib.py) keeps
    blocking after its own fix's files are reverted.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_parked_finding_blocks_while_its_line_is_in_a_fix_hunk:
    `_reverted` `return not any(h["path"] == row["file"] ...` became `return True or ...`;
    failed `assert [] == [_parked_but_present(k) for k in (1, 2, 3)]`. Round 2 (file level, the
    line clause dropped): `return not (files | {row["file"]}) & changed` became `return not files & changed`;
    failed `assert [] == [_parked_but_present(k) for k in (1, 2, 3)]`."""
    _two_fix_run(git_repo)
    _three_cycles(git_repo, file="lib.py")
    _edit_and_test(git_repo, "app.py", "x = 1\n")
    _verdicts(git_repo, 4, [])
    assert fr.record(git_repo) == [_parked_but_present(k) for k in (1, 2, 3)]


@pytest.mark.parametrize("spelling", ["./lib.py", "lib.py/", "sub\\lib.py", "../lib.py",
                                      "/lib.py", "C:/lib.py"])
def test_a_verdict_row_file_must_be_spelt_as_the_diff_spells_it(git_repo, spelling):
    """Invariant: every FA row's file, a P3 too, is a canonical repo-relative POSIX path. A P3
    row isolates this check: the in-scope check applies only to a confirmed P1 or P2.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_verdict_row_file_must_be_spelt_as_the_diff_spells_it:
    all six cases against the round 1 code (2d6ad3a's fixaudit_record.py) failed
    `assert ['FA-1-1 (P2, FIX-1) was confirmed ...'] == ["FA-1-1 file ... spells it"]` (then a P2
    row; now a P3, which isolates the check); `_pass_reasons`
    `if not fixaudit.canonical(row["file"]):` became `if False:` (re-proved 2026-11-22 at
    2b269bd plus the final fix wave): all six failed `assert [] == ["FA-1-1 file ... spells it"]`."""
    _two_fix_run(git_repo)
    _verdicts(git_repo, 1, [_fa("FA-1-1", file=spelling, priority="P3")])
    assert fr.record(git_repo) == [f"FA-1-1 file {spelling!r} is not a repo-relative POSIX "
                                   "path as the fix diff spells it"]


def test_the_reviewers_probe_a_second_spelling_is_refused_and_not_kept(git_repo):
    """Invariant: a confirmed P2 at `./lib.py` (the reviewer's probe, which let a parked fix
    through: `fr.record` returned []) is refused and its pass never kept, so it can never
    become a parked row that reads as reverted.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_the_reviewers_probe_a_second_spelling_is_refused_and_not_kept:
    against the round 1 code (2d6ad3a's fixaudit_record.py) it failed its first assertion
    (`['FA-1-1 (P2, FIX-1) was confirmed ...'] == ["FA-1-1 file './lib.py' ..."]`); with
    `if not fixaudit.canonical(row["file"]):` as `if False:` (re-proved 2026-11-22 at 2b269bd
    plus the final fix wave) it failed the same assertion with "FA-1-1 is a confirmed P2 in
    ./lib.py, which the fix diff does not touch" in its place."""
    run = _two_fix_run(git_repo)
    _verdicts(git_repo, 1, [_fa("FA-1-1", file="./lib.py")])
    assert fr.record(git_repo) == ["FA-1-1 file './lib.py' is not a repo-relative POSIX path "
                                   "as the fix diff spells it"]
    assert fixaudit.kept_passes(git_repo, run) == {}


def test_a_confirmed_row_must_name_a_file_of_the_fix_or_its_diff(git_repo):
    """Invariant: a confirmed P2 sits in the fix diff or in its fix's files; a P3 elsewhere is
    allowed (it goes to the deferred pool).
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_confirmed_row_must_name_a_file_of_the_fix_or_its_diff:
    against the round 1 code (2d6ad3a's fixaudit_record.py), and with the in-scope `elif` as
    `elif False and ...`, it failed `assert ['FA-1-1 (P2, FIX-1) was confirmed in pass 1 ...'] ==
    ["FA-1-1 is a confirmed P2 in other.py, which neither ..."]`. Round 3 (R7: the diff only):
    the expected message changed; the in-scope `elif` re-run as `elif False and ...` fails the same
    assertion."""
    _commit(git_repo, {"other.py": "z = 1\n"})
    _two_fix_run(git_repo)
    _verdicts(git_repo, 1, [_fa("FA-1-1", file="other.py"),
                            _fa("FA-1-2", file="other.py", priority="P3")])
    assert fr.record(git_repo) == ["FA-1-1 is a confirmed P2 in other.py, which the fix diff "
                                   "does not touch"]


def test_a_confirmed_row_in_a_listed_but_unedited_file_is_refused(git_repo):
    """Invariant (R7): a file the fix lists but the diff does not touch cannot hold a confirmed
    P2, or a row there would read as reverted while the fix is in place.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_confirmed_row_in_a_listed_but_unedited_file_is_refused:
    against 311d1e1's fixaudit.py and fixaudit_record.py, and with the in-scope clause back to
    `not in changed | {that fix's files}` (round 2's rule), it failed
    `assert ['FA-1-1 (P2, FIX-1) was confirmed in pass 1 ...'] == ['FA-1-1 is a confirmed P2 in
    lib.py, which the fix diff does not touch']`."""
    _commit(git_repo, {"lib.py": "y = 1\n"})
    _to_l5(git_repo, "STANDARD", [_entry(1, ["app.py", "lib.py"])],
           lambda: _put(git_repo, "app.py", "x = 2\n"))
    _verdicts(git_repo, 1, [_fa("FA-1-1", file="lib.py")])
    assert fr.record(git_repo) == ["FA-1-1 is a confirmed P2 in lib.py, which the fix diff "
                                   "does not touch"]


def test_the_reviewers_second_probe_an_l4_spelling_the_diff_never_uses(git_repo):
    """Invariant (R8): L4 listing `App.py` for an edit of `app.py` is refused at check, so FA
    rows copying that spelling can never park a fix still in the tree. The probe recorded L5.4
    with nothing reverted (`fr.record` returned []).
    Red 2026-11-22, tests/test_fixaudit_record.py::test_the_reviewers_second_probe_an_l4_spelling_the_diff_never_uses:
    against 311d1e1's fixaudit.py and fixaudit_record.py, and with `build_report` `if unlisted:` as
    `if False:`, it failed `Failed: DID NOT RAISE FixAuditRefused`."""
    _to_l5(git_repo, "STANDARD", [_entry(1, ["App.py"])], lambda: _put(git_repo, "app.py", "x = 2\n"))
    with pytest.raises(fixaudit.FixAuditRefused, match=r"the fixes edit app\.py, which no L4"):
        _check(git_repo)


def test_a_fix_listing_no_file_is_refused(git_repo):
    """Invariant (R8): every fix lists at least one file.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_fix_listing_no_file_is_refused:
    against 311d1e1's fixaudit.py and fixaudit_record.py, and with `or not files` dropped from
    `stored_fixes`, it failed `Failed: DID NOT RAISE FixAuditRefused`."""
    _commit(git_repo, {"lib.py": "y = 1\n"})

    def edit():
        _put(git_repo, "app.py", "x = 2\n")
        _put(git_repo, "lib.py", "y = 2\n")

    _to_l5(git_repo, "STANDARD", [_entry(1, ["app.py", "lib.py"]), _entry(2, [])], edit)
    with pytest.raises(fixaudit.FixAuditRefused, match=r'FIX-2: "files" must list'):
        _check(git_repo)


@pytest.mark.parametrize("entry", ["./app.py", ".", "sub\\app.py", "/app.py"])
def test_an_l4_file_entry_must_be_spelt_as_the_diff_spells_it(git_repo, entry):
    """Invariant (R8): each L4 `files` entry is a canonical repo-relative POSIX path, not `.`.
    `app.py` is listed too, so only the spelling check can refuse.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_an_l4_file_entry_must_be_spelt_as_the_diff_spells_it:
    against 311d1e1's fixaudit.py and fixaudit_record.py all four cases failed `DID NOT RAISE
    FixAuditRefused`; with `canonical(f)` as `f` in `stored_fixes` all four failed the same way;
    with `path != "."` dropped from `canonical`, the `.` case failed it (the others are refused
    by the remaining clauses)."""
    _to_l5(git_repo, "STANDARD", [_entry(1, ["app.py", entry])],
           lambda: _put(git_repo, "app.py", "x = 2\n"))
    with pytest.raises(fixaudit.FixAuditRefused, match=r'FIX-1: "files" must list'):
        _check(git_repo)


def test_every_edited_non_test_file_must_be_listed_by_a_fix(git_repo):
    """Invariant (R8): a non-test file the fixes edit that no L4 entry lists is refused; an
    edited test file need not be listed.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_every_edited_non_test_file_must_be_listed_by_a_fix:
    against 311d1e1's fixaudit.py and fixaudit_record.py, and with `if unlisted:` as `if False:`,
    it failed `DID NOT RAISE FixAuditRefused`; with `not is_test_path(path)` as `True` in
    `_unlisted`, it failed `Regex pattern did not match` (actual: 'the fixes edit lib.py,
    tests/test_new.py, which no L4 ...')."""
    _commit(git_repo, {"lib.py": "y = 1\n"})

    def edit():
        _put(git_repo, "app.py", "x = 2\n")
        _put(git_repo, "lib.py", "y = 2\n")
        _put(git_repo, "tests/test_new.py", "def test_x():\n    pass\n")

    _to_l5(git_repo, "STANDARD", [_entry(1, ["app.py"])], edit)
    with pytest.raises(fixaudit.FixAuditRefused, match=r"the fixes edit lib\.py, which no L4"):
        _check(git_repo)


def test_a_marker_that_is_not_json_refuses_with_its_reason(git_repo, capsys):
    """Invariant (R9): an appended marker that is not valid JSON refuses with exit 2 and a reason
    naming the file, before the gate records.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_marker_that_is_not_json_refuses_with_its_reason:
    against 311d1e1's fixaudit.py and fixaudit_record.py, and with the marker's `except ValueError` as
    `except KeyError`, it failed `assert 'refused: fixaudit-appended.json is not valid JSON' in
    'refused: Expecting value: line 1 column 1 (char 0)'`."""
    run = _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [])
    _put(git_repo, f".claude/audits/runs/{run}/{fr.APPENDED}", "not json")
    assert fr.main(["record", "--root", str(git_repo)]) == 2
    assert f"refused: {fr.APPENDED} is not valid JSON" in capsys.readouterr().err
    assert "L5.4" not in ps.load(git_repo).layers


def test_a_parked_fix_sharing_a_file_blocks_while_that_file_differs(git_repo):
    """Invariant: a file the parked fix shares with another fix counts as reverted only when it
    has no hunk at all, since code cannot tell which fix made a shared file's hunks.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_parked_fix_sharing_a_file_blocks_while_that_file_differs:
    against the round 1 code (2d6ad3a's fixaudit_record.py), and with `_reverted`'s `files`
    minus the other fixes' files (the round 1 rule), it failed
    `assert [] == [_parked_but_present(k) for k in (1, 2, 3)]`."""
    _commit(git_repo, {"lib.py": "y = 1\n"})

    def edit():
        _put(git_repo, "app.py", "x = 2\n")
        _put(git_repo, "lib.py", "y = 2\n")

    _to_l5(git_repo, "STANDARD", [_entry(1, ["app.py", "lib.py"]), _entry(2, ["lib.py"])], edit)
    _three_cycles(git_repo, line=50)
    _edit_and_test(git_repo, "app.py", "x = 1\n")  # app.py reverted; lib.py still differs
    _verdicts(git_repo, 4, [])
    assert fr.record(git_repo) == [_parked_but_present(k) for k in (1, 2, 3)]


def test_a_parked_fix_with_a_skipped_binary_file_blocks_while_it_differs(git_repo):
    """Invariant: a binary file the diff skips still counts as changed for the revert check.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_parked_fix_with_a_skipped_binary_file_blocks_while_it_differs:
    against the round 1 code (2d6ad3a's fixaudit_record.py), and with `| set(report["skipped"])`
    dropped from `_changed`, it failed `assert [] == [_parked_but_present(k) for k in (1, 2, 3)]`."""
    (git_repo / "blob.bin").write_bytes(b"\x00\x01")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "binary")

    def edit():
        _put(git_repo, "app.py", "x = 2\n")
        (git_repo / "blob.bin").write_bytes(b"\x00\x02")

    run = _to_l5(git_repo, "STANDARD", [_entry(1, ["app.py", "blob.bin"])], edit)
    # the skipped binary is acknowledged, so only the revert check can block
    _put(git_repo, fr.SIBLINGS.as_posix(),
         json.dumps({"run_id": run, "hits": {}, "skipped": {"blob.bin": "a test fixture"}}))
    _three_cycles(git_repo, line=50)
    _edit_and_test(git_repo, "app.py", "x = 1\n")
    report = _verdicts(git_repo, 4, [])
    assert report["skipped"] == ["blob.bin"]
    assert fr.record(git_repo) == [_parked_but_present(k) for k in (1, 2, 3)]


def test_a_damaged_appended_marker_refuses_before_the_gate_records(git_repo):
    """Invariant: the appended-pass marker is checked before L5.4 is recorded.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_damaged_appended_marker_refuses_before_the_gate_records:
    against the round 1 code (2d6ad3a's fixaudit_record.py), and with the marker check as
    `if False and not all(`, it failed `assert [] == ['fixaudit-appended.json must hold ...']`."""
    run = _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [])
    _put(git_repo, f".claude/audits/runs/{run}/{fr.APPENDED}", '["1"]')
    assert fr.record(git_repo) == [
        f"{fr.APPENDED} must hold a list of the pass numbers already appended; repair it"]
    assert "L5.4" not in ps.load(git_repo).layers


def test_a_confirmed_cycle_needs_a_new_tree(git_repo):
    """Invariant: two CONFIRMED cycles of one finding at the same audited tree are one repair
    attempt, so the second is refused.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_confirmed_cycle_needs_a_new_tree:
    against the round 1 code (2d6ad3a's fixaudit_record.py), and with the same-tree check as
    `if False:`, it failed `assert _journal(git_repo, "CONFIRMED", 2) == 2` (0 == 2)."""
    run = _app_run(git_repo, "STANDARD")
    _verdicts(git_repo, 1, [_fa("FA-1-1")])
    fr.record(git_repo)
    assert _journal(git_repo, "CONFIRMED", 1) == 0
    _verdicts(git_repo, 2, [_fa("FA-2-1")])  # no edit since pass 1
    fr.record(git_repo)
    assert sorted(fixaudit.kept_passes(git_repo, run)) == [1, 2]
    assert _journal(git_repo, "CONFIRMED", 2) == 2
    assert fr.cycles(git_repo, run) == {"F-1": 1}


def test_a_verdict_row_names_an_l4_fix_and_resolves_only_its_own_fix(git_repo):
    """Invariant: an FA row's fix_id and finding are an L4 pair, and a RESOLVED row clears only a
    finding about the same fix.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_verdict_row_names_an_l4_fix_and_resolves_only_its_own_fix:
    `_pass_reasons` `if (row["fix_id"], row["finding"]) not in pairs:` became `if False:`; failed
    `assert ['FA-1-1 (P2, FIX-9) was confirmed ...'] == ['FA-1-1 names fix FIX-9 ...']`.
    `elif target["fix_id"] != row["fix_id"]:` became `elif False:`; failed
    `assert 'FA-2-1 is about FIX-2, but FA-1-1 is about FIX-1' in []`."""
    _two_fix_run(git_repo)
    _verdicts(git_repo, 1, [_fa("FA-1-1", fix_id="FIX-9")])
    assert fr.record(git_repo) == [
        "FA-1-1 names fix FIX-9 for finding F-1, which L4 does not have"]
    _verdicts(git_repo, 1, [_fa("FA-1-1")])
    fr.record(git_repo)
    _edit_and_test(git_repo, "app.py", "x = 3\n")
    _verdicts(git_repo, 2, [_fa("FA-2-1", "RESOLVED", fix=2, resolves="FA-1-1")])
    assert "FA-2-1 is about FIX-2, but FA-1-1 is about FIX-1" in fr.record(git_repo)


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
    all), and in round 1 `or row["id"] in cleared:` became `or bool(cleared):`; each failed at
    the CODE-001 step, `assert set() == {'DOC-001'}`. Second mutation:
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
    report = _verdicts(git_repo, 1, [_fa("FA-1-1", fix=6, file="util.py"),
                                    _fa("FA-1-2", fix=7, file="README.md")])
    assert report["auditors"] == ["bug-auditor", "bug-auditor:DAT", "perf-auditor"]
    hits = {h["fix_id"]: h for h in report["siblings"]["hits"]}
    assert {k: (h["path"], h["line"]) for k, h in hits.items()} == {
        "FIX-1": ("readers.py", 2), "FIX-2": ("github.py", 2), "FIX-3": ("report.py", 2)}
    remaining = {case for case, _ in _SEVEN}
    assert _blockers(fr.record(git_repo)) == remaining

    disposed = {}
    for case, n in (("BUG-001", 1), ("SEC-001", 2), ("NUM-001", 3)):
        disposed[hits[f"FIX-{n}"]["hit_id"]] = {"disposition": "not_applicable", "reason": "r"}
        _put(git_repo, fr.SIBLINGS.as_posix(), json.dumps({"run_id": run, "hits": disposed}))
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


# --- run 20261122T184346Z, L4 fixes ---------------------------------------------------------

_STORE = 'def load(p):\n    return p.read_text(encoding="utf-8")\n'
_STORE_FIXED = 'def load(p):\n    return p.read_text(encoding="utf-8", errors="replace")\n'


def _sibling_run(repo):
    """A FAST run whose FIX-1 leaves one sibling hit, in readers.py; returns (run_id, hit)."""
    _commit(repo, {"store.py": _STORE,
                   "readers.py": 'def other(q):\n    return q.read_text(encoding="utf-8")\n'})
    run = _to_l5(repo, "FAST", [_entry(1, ["store.py"])], lambda: _put(repo, "store.py",
                                                                        _STORE_FIXED))
    (hit,) = _check(repo)["siblings"]["hits"]
    return run, hit


def _dispose(repo, run_id, hit_ids=(), skipped=None):
    data = {"run_id": run_id, "hits": {h: {"disposition": "not_applicable", "reason": "r"}
                                       for h in hit_ids}}
    if skipped is not None:
        data["skipped"] = skipped
    _put(repo, fr.SIBLINGS.as_posix(), json.dumps(data))
    return data


def test_a_dispositions_file_must_carry_this_runs_id(git_repo):
    """Invariant (ENV-001, STAKES-003): the sibling dispositions file counts only when its
    top-level `run_id` is the open run's, so a file an earlier run left behind clears nothing.
    Failure caught: hit ids are stable across runs, so an unbound file cleared a later run's hit.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_dispositions_file_must_carry_this_runs_id:
    baseline 023a1b5 (pre-fix `_dispositions`); the flat map without a run_id was accepted, so it
    failed the first assertion, `assert any('has no "run_id"' in r ...)` on `[]`."""
    run, hit = _sibling_run(git_repo)
    _put(git_repo, fr.SIBLINGS.as_posix(), json.dumps(
        {hit["hit_id"]: {"disposition": "not_applicable", "reason": "r"}}))
    reasons = fr.record(git_repo)
    assert any('has no "run_id"' in r and run in r for r in reasons), reasons
    _dispose(git_repo, "20000101T000000Z", [hit["hit_id"]])
    reasons = fr.record(git_repo)
    assert any("20000101T000000Z" in r and run in r for r in reasons), reasons
    assert "L5.4" not in ps.load(git_repo).layers
    data = _dispose(git_repo, run, [hit["hit_id"]])
    assert fr.record(git_repo) == []
    kept = git_repo / ".claude" / "audits" / "runs" / run / fr.SIBLINGS.name
    assert json.loads(kept.read_text(encoding="utf-8")) == data


def test_an_earlier_runs_dispositions_do_not_clear_a_later_runs_hit(git_repo):
    """Invariant (STAKES-003, the auditor's probe test_stale_siblings_probe.py made real): run B,
    with the same fix id and the same hit as run A, is refused while the dispositions file is
    run A's, naming both runs.
    Red NOT YET SHOWN for this node. The pre-fix code (023a1b5) refuses run A's file of the new
    shape at the first record, so the baseline fails in setup, which is no red proof. The planned
    red is the mutation of the new `_dispositions`, `if data["run_id"] != run_id:` as
    `if False:`, expected to fail `assert any(run_a in r and run_b in r ...)` on `[]`; it was
    not run in L4 (the run was refused by a session permission check)."""
    run_a, hit = _sibling_run(git_repo)
    _dispose(git_repo, run_a, [hit["hit_id"]])
    assert fr.record(git_repo) == []
    ps.abort("next run", root=git_repo)
    _put(git_repo, "store.py", _STORE)
    run_b = _to_l5(git_repo, "FAST", [_entry(1, ["store.py"])],
                   lambda: _put(git_repo, "store.py", _STORE_FIXED))
    (hit_b,) = _check(git_repo)["siblings"]["hits"]
    assert hit_b["hit_id"] == hit["hit_id"] and run_b != run_a
    reasons = fr.record(git_repo)
    assert any(run_a in r and run_b in r for r in reasons), reasons
    assert not (git_repo / ".claude" / "audits" / "runs" / run_b / fr.SIBLINGS.name).exists()


def test_a_skipped_non_test_path_needs_a_reason_before_record(git_repo, capsys):
    """Invariant (SEC-001): an edit no check can read (a binary file here) is printed by
    `fixaudit check` under `skipped`, and `record` refuses while a skipped non-test path has no
    reason under "skipped" in the dispositions file; a skipped test path needs none.
    Failure caught: a skipped edit passed L5.4 unaudited, with no one told.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_a_skipped_non_test_path_needs_a_reason_before_record:
    baseline 023a1b5; the check summary had no `skipped`, so it failed
    `assert None == ['blob.bin', 'tests/data.bin']`. The record refusal itself (the
    skipped-reason list in `_evaluate`) has no red shown yet: its planned mutation, that
    comprehension's condition as `if False`, was not run in L4."""
    (git_repo / "blob.bin").write_bytes(b"\x00\x01")
    (git_repo / "tests").mkdir(exist_ok=True)
    (git_repo / "tests" / "data.bin").write_bytes(b"\x00\x01")
    _git(git_repo, "add", "-A")
    _git(git_repo, "commit", "-qm", "binaries")

    def edit():
        _put(git_repo, "app.py", "x = 2\n")
        (git_repo / "blob.bin").write_bytes(b"\x00\x02")
        (git_repo / "tests" / "data.bin").write_bytes(b"\x00\x02")

    run = _to_l5(git_repo, "FAST", [_entry(1, ["app.py", "blob.bin"])], edit)
    assert cca_main(["fixaudit", "check", "--root", str(git_repo)]) == 0
    assert json.loads(capsys.readouterr().out).get("skipped") == ["blob.bin", "tests/data.bin"]
    reasons = fr.record(git_repo)
    assert any("blob.bin is skipped" in r for r in reasons), reasons
    assert not any("tests/data.bin" in r for r in reasons), reasons
    _dispose(git_repo, run, skipped={"blob.bin": "  "})
    assert any("blob.bin is skipped" in r for r in fr.record(git_repo))
    _dispose(git_repo, run, skipped={"blob.bin": "a fixture image; no code reads it"})
    assert fr.record(git_repo) == []


def test_three_confirming_passes_park_a_finding_with_no_journal_row(git_repo):
    """Invariant (STAKES-001, the auditor's probe test_no_journal_cap_never_reached made real):
    the cap counts kept passes at distinct audited trees that confirmed a P1 or P2 in the fix,
    not only the journal rows the agent chose to write, so three such passes park the finding.
    Red 2026-11-22, tests/test_fixaudit_record.py::test_three_confirming_passes_park_a_finding_with_no_journal_row:
    baseline 023a1b5 (`cycles` counted journal rows only); failed
    `assert set() == {'F-1'}` at `fr.parked(...)`."""
    run = _app_run(git_repo, "STANDARD")
    for k in (1, 2, 3):
        if k > 1:
            _edit_and_test(git_repo, "app.py", f"x = {k + 1}\n")
        _verdicts(git_repo, k, [_fa(f"FA-{k}-1", line=50)])
        assert fr.record(git_repo) != []
    assert fr.parked(git_repo, run) == {"F-1"}
    assert fr.cycles(git_repo, run) == {"F-1": 3}
    assert fr.record(git_repo) == [_parked_but_present(k) for k in (1, 2, 3)]
