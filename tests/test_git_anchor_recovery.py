"""Legacy recovery uses the finding's own immutable commit bullet, not a file list."""
from __future__ import annotations

import hashlib
from dataclasses import replace

import pytest
from bugs_testkit import commit, make_repo, run_git
from bugs_testkit import isolated_git_environment as isolated_git_environment
from test_anchor_recovery import row

from cca_checks.bugs import anchor_log, prior, records, store
from cca_checks.bugs.model import memo_filename


def legacy_note(r):
    return {"v": 2, "bug_id": r.id, "repo": r.repo, "run_id": r.run_id,
            "fix_commit": r.fix_commit,
            "anchors": [{"file": "pool.py", "symbol": None, "code_sha": "1" * 64}],
            "provenance": {"method": "git-finding-file", "commit": r.fix_commit,
                           "message_sha256": "2" * 64,
                           "summary_sha256": hashlib.sha256(r.summary.encode()).hexdigest(),
                           "finding_id": r.finding_id, "ordinal": r.ordinal,
                           "references": [{"file": "pool.py", "token": "pool.py"}]}}


def test_null_run_annotation_reaches_all_readers_without_ledger_edits(tmp_path):
    """Baseline af3f6bf refuses this valid provenance-bearing legacy annotation."""
    r = replace(row(), run_id=None, summary="pool.py: repair the lease")
    store.write([r], tmp_path, "stamp")
    before = (tmp_path / store.LEDGER).read_bytes()
    note = legacy_note(r)
    accepted = True
    try:
        anchor_log.append(tmp_path, [note])
    except ValueError:
        accepted = False
    assert accepted, "legacy findings need real null run identities, never invented run IDs"
    assert records.read_ledger(tmp_path)[0][r.id].anchors
    assert store.bug_rows(tmp_path / store.LEDGER)[0].anchors
    assert prior.load_rows(tmp_path / store.LEDGER)[0]["anchors"] == note["anchors"]
    records.rerender_all(tmp_path, "stamp")
    parsed = records.parse_memo((tmp_path / store.MEMOS / memo_filename(r.id)).read_text())
    assert parsed.run_id is None
    assert parsed.evidence == r.evidence
    assert (tmp_path / store.LEDGER).read_bytes() == before


@pytest.mark.parametrize("field,value", [("summary", "other claim"), ("ordinal", 2),
                                        ("finding_id", "BUG-2"), ("summary", None)])
def test_legacy_annotation_stays_bound_to_the_finding(field, value):
    r = replace(row(), run_id=None, summary="pool.py: repair the lease")
    raw = r.to_json()
    raw[field] = value
    assert not anchor_log.effective(raw, {r.id: legacy_note(r)})


@pytest.mark.parametrize("damage", ["run", "hash", "method", "commit", "symbol", "reference"])
def test_legacy_provenance_is_required(damage):
    r = replace(row(), run_id=None, summary="pool.py: repair the lease")
    note = legacy_note(r)
    if damage == "run":
        del note["run_id"]
    elif damage == "hash":
        note["provenance"]["message_sha256"] = "bad"
    elif damage == "method":
        note["provenance"]["method"] = "guessed"
    elif damage == "commit":
        note["provenance"]["commit"] = "b" * 40
    elif damage == "symbol":
        note["anchors"][0]["symbol"] = "guessed"
    else:
        note["provenance"]["references"][0]["file"] = "other.py"
    assert not anchor_log.valid(note)


def git_history(tmp_path, summary="pool.py: correct the lease", *, duplicate=False,
                extra=None, changed_extra=False):
    repo = make_repo(tmp_path / "repo")
    commit(repo, "pool.py", "def lease():\n    return -1\n", "input")
    if extra:
        commit(repo, extra, "value = 0\n", "other input")
        if changed_extra:
            (repo / extra).write_text("value = 1\n")
            run_git(repo, "add", extra)
    message = "fix: audit fixes from CCA\n\nP2:\n- BUG-1: " + summary + "\n"
    if duplicate:
        message += "- BUG-1: " + summary + "\n"
    head = commit(repo, "pool.py", "def lease():\n    return 1\n", message)
    r = replace(row(), fix_commit=head, run_id=None, summary=summary,
                files=["pool.py", *([extra] if extra else [])])
    return r, repo


def test_git_bullet_yields_only_authored_file_context(tmp_path):
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import recover

    r, repo = git_history(tmp_path)
    note = None
    try:
        note = recover(r, repo)
    except Unavailable:
        pass
    assert note is not None, "the exact authored file reference was not recovered"
    assert anchor_log.valid(note) and note["run_id"] is None
    assert [(a["file"], a["symbol"]) for a in note["anchors"]] == [("pool.py", None)]
    assert note["provenance"]["references"] == [{"file": "pool.py", "token": "pool.py"}]


@pytest.mark.parametrize("case", ["no_file", "ambiguous_file", "unchanged_file",
                                 "wrong_bullet", "duplicate_bullet", "proof_file"])
def test_git_bullet_refuses_unsupported_locations(tmp_path, case):
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import recover

    summary = {"no_file": "guard the lease", "ambiguous_file": "pool.py: correct lease",
               "unchanged_file": "other.py: correct lease",
               "proof_file": "guard the lease; +test tests/test_pool.py"}.get(
                   case, "pool.py: correct the lease")
    extra = {"ambiguous_file": "other/pool.py", "unchanged_file": "other.py",
             "proof_file": "tests/test_pool.py"}.get(case)
    r, repo = git_history(tmp_path, summary, duplicate=case == "duplicate_bullet",
                          extra=extra, changed_extra=case in {"ambiguous_file", "proof_file"})
    if case == "wrong_bullet":
        r = replace(r, summary="pool.py: another finding")
    with pytest.raises(Unavailable):
        recover(r, repo)


def test_git_plan_is_idempotent_and_preserves_evidence(tmp_path):
    from cca_checks.bugs.recover_git_anchors import plan

    r, repo = git_history(tmp_path)
    ledger = tmp_path / "ledger"
    store.write([r], ledger, "stamp")
    original = (ledger / store.LEDGER).read_bytes()
    first = plan(ledger, [repo])
    assert len(first.annotations) == 1 and not first.unresolved
    anchor_log.append(ledger, first.annotations)
    second = plan(ledger, [repo])
    assert not second.annotations and second.already_anchored == 1
    assert records.read_ledger(ledger)[0][r.id].evidence == r.evidence
    assert (ledger / store.LEDGER).read_bytes() == original


def test_named_changed_definition_supplies_file_context(tmp_path):
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import recover

    r, repo = git_history(tmp_path, "repair `lease()`")
    note = None
    try:
        note = recover(r, repo)
    except Unavailable:
        pass
    assert note is not None, "the exact named changed definition was not located"
    assert note["provenance"]["method"] == "git-finding-symbol-file"
    assert note["provenance"]["references"][0]["symbol"] == "lease"
    assert note["anchors"][0]["file"] == "pool.py"
    assert note["anchors"][0]["symbol"] is None
    assert anchor_log.valid(note)


@pytest.mark.parametrize("directory", ["pkg/tests", "pkg/test", "pkg/fixtures", "pkg/examples"])
def test_proof_symbol_does_not_supply_production_file_context(tmp_path, directory):
    """RED at 3dae98c: a nested proof function became the production finding's anchor."""
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import recover

    r, repo = git_history(tmp_path)
    file = directory + "/test_pool.py"
    summary = "repair lease handling; regression covered by `test_lease()`"
    head = commit(repo, file, "def test_lease():\n    assert True\n",
                  "fix: audit fixes from CCA\n\nP2:\n- BUG-1: " + summary + "\n")
    r = replace(r, fix_commit=head, summary=summary, files=["pool.py", file])
    with pytest.raises(Unavailable, match="unambiguous"):
        recover(r, repo)


@pytest.mark.parametrize("case", ["unchanged", "duplicated", "wrong_repo"])
def test_named_definition_requires_a_unique_changed_target(tmp_path, case):
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import recover

    summary = "repair `lease()`"
    r, repo = git_history(tmp_path, summary)
    if case == "wrong_repo":
        run_git(repo, "remote", "set-url", "origin", "https://github.com/other/widgets.git")
    else:
        source = ("def lease():\n    return 1\n\ndef helper():\n    return 2\n"
                  if case == "unchanged" else "def lease():\n    return 2\n\ndef lease():\n    return 3\n")
        head = commit(repo, "pool.py", source, "fix: audit fixes from CCA\n\nP2:\n- BUG-1: "
                      + summary + "\n")
        r = replace(r, fix_commit=head)
    with pytest.raises(Unavailable):
        recover(r, repo)


def test_legacy_summary_with_surrogate_is_refused():
    r = replace(row(), run_id=None, summary="pool.py: repair the lease")
    raw = r.to_json()
    raw["summary"] = "\ud800"
    raised = False
    got = []
    try:
        got = anchor_log.effective(raw, {r.id: legacy_note(r)})
    except UnicodeError:
        raised = True
    assert not raised and not got, "malformed summary crashed an independent ledger reader"


def test_malformed_git_finding_has_a_domain_refusal(tmp_path):
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import recover

    r, repo = git_history(tmp_path)
    r = replace(r, files=7)
    domain, other = False, None
    try:
        recover(r, repo)
    except Unavailable:
        domain = True
    except (TypeError, AttributeError) as exc:
        other = exc
    assert domain and other is None, "malformed finding escaped the structured refusal"


def _bare_history(tmp_path, summary):
    """A fix whose bullet names the changed definition `check_lease` as a bare word."""
    repo = make_repo(tmp_path / "repo")
    commit(repo, "pool.py", "def check_lease():\n    return -1\n\ndef lease():\n    return 0\n",
           "input")
    head = commit(repo, "pool.py",
                  "def check_lease():\n    return 1\n\ndef lease():\n    return 2\n",
                  "fix: audit fixes from CCA\n\nP2:\n- BUG-1: " + summary + "\n")
    return replace(row(), fix_commit=head, run_id=None, summary=summary, files=["pool.py"]), repo


def test_a_distinctive_bare_definition_name_supplies_file_context(tmp_path):
    """Invariant: a legacy summary that names exactly one changed definition as a bare,
    distinctive word ("check_lease rejects ...") recovers file context, marked `form: bare`.

    Measured 2026-10-28: 502 of 556 unanchored rows failed with "no unambiguous source file
    reference" because only backticked names and calls counted; this form recovered 81.
    Red 2026-10-28: mutation in `recover_git_anchors.symbol_references`,
    `if _distinctive(m.group(1)):` became `if False:`; failed at `assert note is not None`.
    """
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import recover

    r, repo = _bare_history(tmp_path, "pool check_lease rejects negative leases")
    note = None
    try:
        note = recover(r, repo)
    except Unavailable:
        pass
    assert note is not None, "the bare changed definition was not located"
    ref = note["provenance"]["references"][0]
    assert (ref["symbol"], ref["form"], ref["file"]) == ("check_lease", "bare", "pool.py")
    assert note["anchors"][0]["symbol"] is None and anchor_log.valid(note)


@pytest.mark.parametrize("summary", ["pool lease rejects negatives",
                                     "see pool_notes.md for check_lease_v2 context"])
def test_a_prose_word_or_path_part_supplies_no_context(tmp_path, summary):
    """Invariant: a bare word counts only when it reads as an identifier, and never as part
    of a path: "lease" names the changed `lease` definition but is prose, and the second
    summary names no changed definition at all.

    Red 2026-10-28: mutation in `recover_git_anchors._distinctive`, the body became
    `return True`; failed at `pytest.raises(Unavailable)` for the first case (DID NOT RAISE),
    because "lease" then matched the changed `lease`.
    """
    from cca_checks.bugs.recover_anchors import Unavailable
    from cca_checks.bugs.recover_git_anchors import recover

    r, repo = _bare_history(tmp_path, summary)
    with pytest.raises(Unavailable):
        recover(r, repo)


def single_file_note(r):
    return {"v": 2, "bug_id": r.id, "repo": r.repo, "run_id": r.run_id,
            "fix_commit": r.fix_commit,
            "anchors": [{"file": "pool.py", "symbol": None, "code_sha": "1" * 64}],
            "provenance": {"method": "git-single-production-file", "commit": r.fix_commit,
                           "message_sha256": "2" * 64, "changed_sha256": "3" * 64,
                           "summary_sha256": hashlib.sha256(r.summary.encode()).hexdigest(),
                           "finding_id": r.finding_id, "ordinal": r.ordinal,
                           "parent": "a" * 40, "exclusions_version": 1,
                           "references": [{"file": "pool.py"}]}}


def test_single_file_note_is_valid():
    """Red proof: with the method missing from the accepted tuple in
    anchor_log._git_provenance, this fails on `assert anchor_log.valid(note)`."""
    r = replace(row(), run_id=None, summary="pool.py: repair the lease")
    assert anchor_log.valid(single_file_note(r))


@pytest.mark.parametrize("damage", ["symbol", "missing_parent", "missing_summary_sha",
                                    "references", "zero_exclusions_version"])
def test_single_file_note_rejects_damage(damage):
    """Each case is red when its own check is deleted from _git_provenance:
    symbol (the `a["symbol"] is not None` clause), missing_parent (the parent SHA40 check
    in the new branch), missing_summary_sha (summary_sha256 in the shared tuple),
    references (the `refs == [...]` comparison), zero_exclusions_version (`version >= 1`).
    The failing assertion in every mutation is `assert not anchor_log.valid(note)`."""
    r = replace(row(), run_id=None, summary="pool.py: repair the lease")
    note = single_file_note(r)
    prov = note["provenance"]
    if damage == "symbol":
        note["anchors"][0]["symbol"] = "guessed"
    elif damage == "missing_parent":
        del prov["parent"]
    elif damage == "missing_summary_sha":
        del prov["summary_sha256"]
    elif damage == "references":
        prov["references"] = [{"file": "other.py"}]
    else:
        prov["exclusions_version"] = 0
    assert not anchor_log.valid(note)


def test_single_file_note_is_effective_for_its_row():
    """Red proof: with the method unaccepted in _git_provenance, valid() fails inside
    effective() and the first assert (`== note["anchors"]`) fails with an empty list.
    The second assert is held by the existing summary_sha256 binding in effective()."""
    r = replace(row(), run_id=None, summary="pool.py: repair the lease")
    note = single_file_note(r)
    assert anchor_log.effective(r.to_json(), {r.id: note}) == note["anchors"]
    other = r.to_json()
    other["summary"] = "pool.py: a different claim"
    assert not anchor_log.effective(other, {r.id: note})
