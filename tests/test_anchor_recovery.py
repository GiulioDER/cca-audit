"""Local, offline anchor recovery; every test uses a private ledger and git history.

Red proofs are recorded in docs/reports/2026-10-25-anchor-recovery-red-proof.json.
The mutations target the production rules, never fixture or import failures.
"""
from __future__ import annotations

import json
from dataclasses import replace

import pytest
from bugs_testkit import commit, make_repo, run_git
from bugs_testkit import isolated_git_environment as isolated_git_environment

from cca_checks.bugs import anchor_log, prior, records, recover_anchors, roundtrip, store
from cca_checks.bugs.anchors import Anchor, symbol_sha
from cca_checks.bugs.model import BugRow, memo_filename
from cca_checks.bugs.recover_anchors import Unavailable, plan, recover


def row():
    return BugRow(project="widgets", repo="acme/widgets", finding_id="BUG-1",
                  fix_commit="a" * 40, date="2026-10-25", files=["pool.py"],
                  summary="check the lease", run_id="run-1",
                  evidence={k: "absent" for k in
                            ("confirmed_l25", "fixed", "l6_approved", "red_proof")})


def note(r):
    return {"v": 1, "bug_id": r.id, "repo": r.repo, "fix_commit": r.fix_commit,
            "run_id": r.run_id,
            "anchors": [{"file": "pool.py", "symbol": "lease", "code_sha": "1" * 64}],
            "provenance": {k: "2" * 64 for k in
                           ("meta_sha256", "summary_sha256", "l2_sha256", "l4_sha256")}}


def test_all_readers_rerender_and_roundtrip_preserve_the_ledger(tmp_path):
    """A recovered anchor enriches all readers without rewriting any authored ledger byte."""
    r = row()
    store.write([r], tmp_path, "stamp")
    before = (tmp_path / store.LEDGER).read_bytes()
    n = note(r)
    anchor_log.append(tmp_path, [n])
    assert (tmp_path / store.LEDGER).read_bytes() == before
    expected = replace(r, anchors=[Anchor(**n["anchors"][0])])
    assert records.read_ledger(tmp_path)[0][r.id] == expected
    assert store.bug_rows(tmp_path / store.LEDGER) == [expected]
    assert prior.load_rows(tmp_path / store.LEDGER)[0]["anchors"] == n["anchors"]
    records.rerender_all(tmp_path, "stamp")
    text = (tmp_path / store.MEMOS / memo_filename(r.id)).read_text(encoding="utf-8")
    assert records.parse_memo(text) == expected
    assert roundtrip.check(tmp_path, [{"records": [
        {"id": r.id, "recall_record": records.to_record(expected)}]}]).ok
    assert (tmp_path / store.LEDGER).read_bytes() == before


@pytest.mark.parametrize("field,value", [("repo", "other/widgets"),
                                        ("fix_commit", "b" * 40), ("run_id", "other")])
def test_annotation_is_bound_to_repo_fix_and_run(tmp_path, field, value):
    """An annotation cannot be borrowed by a similarly named row from another provenance."""
    r = row()
    anchor_log.append(tmp_path, [note(r)])
    changed = replace(r, **{field: value}).to_json()
    changed["bug_id"] = r.id  # independently test the provenance, not the lookup key
    assert anchor_log.effective(changed, anchor_log.read(tmp_path)) == []


@pytest.mark.parametrize("field,value", [("file", "../pool.py"),
                                        ("symbol", "bad/name"), ("code_sha", "unknown")])
def test_malformed_anchor_notes_grant_nothing(tmp_path, field, value):
    """Invalid anchor shapes remain unusable even when a corrupt log line parses as JSON."""
    n = note(row())
    n["anchors"][0][field] = value
    (tmp_path / anchor_log.NAME).write_text(json.dumps(n) + "\n", encoding="utf-8")
    assert anchor_log.read(tmp_path) == {}


def test_authored_anchors_always_win():
    """An old side-log recovery cannot override a future authored finding anchor."""
    r = row()
    n = note(r)
    own = replace(r, anchors=[Anchor("pool.py", "lease", "3" * 64)])
    assert anchor_log.effective(own.to_json(), {r.id: n}) == [a.to_json() for a in own.anchors]
    assert anchor_log.apply(own, {r.id: n}) == own


def test_torn_append_is_separated_and_latest_valid_annotation_wins(tmp_path):
    """A partial last write neither swallows the next note nor pins an older valid note."""
    r = row()
    n = note(r)
    (tmp_path / anchor_log.NAME).write_bytes(b'{"v":')
    anchor_log.append(tmp_path, [n])
    assert anchor_log.read(tmp_path) == {r.id: n}
    changed = {**n, "anchors": [{**n["anchors"][0], "code_sha": "4" * 64}]}
    anchor_log.append(tmp_path, [changed])
    assert anchor_log.read(tmp_path) == {r.id: changed}


def history(tmp_path):
    repo = make_repo(tmp_path / "repo")
    base = commit(repo, "pool.py", "def lease():\n    return 0\n", "add pool")
    tree = run_git(repo, "rev-parse", f"{base}^{{tree}}").strip()
    fixed = "# shifted\n\n\ndef lease():\n    return 1\n"
    head = commit(repo, "pool.py", fixed, "fix lease")
    landed = commit(repo, "README.md", "landed\n", "land repair")
    r = replace(row(), fix_commit=landed)
    archive = tmp_path / "archives" / "run-1"
    archive.mkdir(parents=True)
    data = {"meta.json": {"repo": r.repo, "run_id": r.run_id, "commit": head},
            "summary.json": {"run_id": r.run_id, "commit": head,
                             "base_tree": tree, "base_commit": base},
            "L2.json": [{"id": r.finding_id, "file": "pool.py", "line": 2}],
            "L4.json": [{"finding": r.finding_id, "files": ["pool.py"]}]}
    for name, value in data.items():
        (archive / name).write_text(json.dumps(value), encoding="utf-8")
    return r, repo, archive, fixed


def test_recovery_uses_input_tree_lines_and_hashes_the_landed_repair(tmp_path):
    """Fixes can move a symbol; today's working tree and fixed-file line numbers are wrong."""
    r, repo, archive, fixed = history(tmp_path)
    commit(repo, "pool.py", "def other():\n    return 9\n", "later change")
    n = recover(r, archive, repo)
    assert n["anchors"] == [{"file": "pool.py", "symbol": "lease",
                             "code_sha": symbol_sha(fixed, "lease")}]


def test_recovery_refuses_a_different_landed_symbol(tmp_path):
    """An archived repair cannot describe different code that landed under the fix commit."""
    r, repo, archive, _ = history(tmp_path)
    changed = commit(repo, "pool.py", "def lease():\n    return 7\n", "different repair")
    with pytest.raises(Unavailable, match="landed symbol differs"):
        recover(replace(r, fix_commit=changed), archive, repo)


def test_recovery_needs_finding_level_file_support(tmp_path):
    """A finding file must be in its own fix and row, rather than merely in a fix commit."""
    r, repo, archive, _ = history(tmp_path)
    with pytest.raises(Unavailable, match="finding file is not supported"):
        recover(replace(r, files=[]), archive, repo)


def test_missing_input_snapshot_only_yields_file_context(tmp_path):
    """The recovery never guesses a function using the line number in the repaired file."""
    r, repo, archive, _ = history(tmp_path)
    p = archive / "summary.json"
    value = json.loads(p.read_text())
    value.pop("base_tree")
    p.write_text(json.dumps(value))
    assert recover(r, archive, repo)["anchors"][0]["symbol"] is None


def test_plan_is_idempotent_and_accounts_for_missing_archives(tmp_path):
    """Only supported rows are annotated, and a second pass cannot keep appending them."""
    r, repo, archive, _ = history(tmp_path)
    root = tmp_path / "ledger"
    other = replace(r, finding_id="BUG-2", run_id="missing")
    store.write([r, other], root, "stamp")
    out = plan(root, [repo], [archive.parent])
    assert len(out.annotations) == 1
    assert out.unresolved == {other.id: "no matching local audit archive"}
    anchor_log.append(root, out.annotations)
    again = plan(root, [repo], [archive.parent])
    assert again.annotations == [] and again.already_anchored == 1


def test_untrusted_git_resolution_never_launches_a_command(monkeypatch, tmp_path):
    """A repo-local Git executable must not be run just to read recovery evidence."""
    calls = []
    monkeypatch.setattr(recover_anchors, "resolve_tool", lambda *args: None)

    def launched(*args, **kwargs):
        calls.append(args)
        from subprocess import CompletedProcess
        return CompletedProcess([], 0, stdout=b"", stderr=b"")

    monkeypatch.setattr(recover_anchors.subprocess, "run", launched)
    with pytest.raises(Unavailable, match="trusted git"):
        recover_anchors._git(tmp_path, "rev-parse", "HEAD")
    assert calls == []
