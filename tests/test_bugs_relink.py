"""`bugs relink`: a stuck run links to the squash merge carrying its commit's whole message."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest
from bugs_testkit import commit, make_repo, publish, run_git
from bugs_testkit import isolated_git_environment as isolated_git_environment
from test_bugs_resolve import pending_record

from cca_checks.bugs import relink, stats, unlinked
from cca_checks.bugs.model import EVIDENCED, BugRow
from cca_checks.bugs.records import read_ledger

NOW = datetime(2026, 10, 29, 12, 0, tzinfo=timezone.utc)
RUN_MSG = "fix: 2 audit fixes from CCA (STANDARD tier)\n\nP2:\n- BUG-001: a.\n- DOC-001: b.\n"


def _history(tmp_path, *, squash_body: str | None = None, squashes: int = 1):
    """A repo whose run commit sits on a side branch and whose master squashes it."""
    repo = make_repo(tmp_path / "repo")
    run_git(repo, "checkout", "-q", "-b", "side")
    run = commit(repo, "a.py", "x = 1\n", RUN_MSG, date="2026-10-13T09:00:00+00:00")
    run_git(repo, "checkout", "-q", "master")
    body = squash_body if squash_body is not None else "* " + RUN_MSG
    merges = [commit(repo, f"m{i}.py", f"x = {i}\n", f"fix: the series (#12)\n\n{body}",
                     date="2026-10-14T09:00:00+00:00") for i in range(squashes)]
    publish(repo)
    return repo, run, merges


def _bugs(root):
    rows, errors = read_ledger(root)
    assert not errors
    return [r for r in rows.values() if isinstance(r, BugRow)]


def test_an_unlinked_run_is_superseded_by_rows_keyed_by_its_squash_merge(_private_bugs_dir,
                                                                        tmp_path):
    """Invariant: an unlinked run whose commit message is a section of one squash merge gets rows
    keyed by that merge, `fixed` evidenced, a `pr:` source from the merge subject, each naming
    the unlinked row it replaces; readers then count each fix once, and the record moves to
    recorded/.

    Red 2026-10-29, two mutations in `relink.relink`: deleting `row.supersedes =
    previous.bug_id`; failed at the `assert all(... r.supersedes ...)` check. Deleting
    `_move(folder, root, RECORDED)`; failed at `assert not any(...unlinked...)`.
    """
    root = _private_bugs_dir
    repo, run, (merge,) = _history(tmp_path)
    pending_record(root, tmp_path, "r1", captured="2026-10-13T10:00:00Z", commit=run)
    unlinked.record_unlinked(root, write=True, now=NOW)
    rep = relink.relink(root, [repo], write=True, now=NOW)
    assert rep.rows_written == 2 and rep.superseding == 2
    new = [r for r in _bugs(root) if r.fix_commit == merge]
    assert {r.finding_id for r in new} == {"BUG-001", "DOC-001"}
    assert all(r.evidence["fixed"] == EVIDENCED and "pr:acme/widgets#12" in r.sources
               and r.supersedes for r in new)
    rows, _ = read_ledger(root)
    assert stats.summarize(rows)["fixes"] == 2
    assert not any((root / unlinked.UNLINKED).iterdir())
    assert (root / "recorded").is_dir()


def test_a_matching_subject_with_a_different_body_links_nothing(_private_bugs_dir, tmp_path):
    """Invariant: only the whole message links. A squash section with the run's subject but other
    bullets (another run with a generic subject) leaves the run unmatched, writing nothing.

    Red 2026-10-29: mutation in `relink.relink`, the lookup `normalize(message)` became
    `normalize(message.splitlines()[0])` with sections keyed the same way; failed at
    `assert rep.rows_written == 0`.
    """
    root = _private_bugs_dir
    other = "* fix: 2 audit fixes from CCA (STANDARD tier)\n\nP2:\n- BUG-007: another run.\n"
    repo, run, _ = _history(tmp_path, squash_body=other)
    folder = pending_record(root, tmp_path, "r1", captured="2026-10-13T10:00:00Z", commit=run)
    rep = relink.relink(root, [repo], write=True, now=NOW)
    assert rep.rows_written == 0 and rep.unmatched == [f"pending/{folder.name}"]
    assert folder.exists()


def test_a_message_in_two_merges_is_ambiguous_and_links_nothing(_private_bugs_dir, tmp_path):
    """Invariant: a run whose message is a section of two merges is reported ambiguous and left
    where it is.

    Red 2026-10-29: mutation in `relink.relink`, `if len(hits) > 1:` became `if False:`;
    failed at `assert rep.rows_written == 0`.
    """
    root = _private_bugs_dir
    repo, run, _ = _history(tmp_path, squashes=2)
    folder = pending_record(root, tmp_path, "r1", captured="2026-10-13T10:00:00Z", commit=run)
    rep = relink.relink(root, [repo], write=True, now=NOW)
    assert rep.rows_written == 0 and len(rep.ambiguous) == 1
    assert folder.exists()


@pytest.mark.parametrize("trailer", ["", "\nCo-Authored-By: Someone <s@example.com>\n"])
def test_a_pending_run_links_with_or_without_trailers(_private_bugs_dir, tmp_path, trailer):
    """Invariant: a pending run links too (no `supersedes`), and a Co-Authored-By trailer the
    squash dropped does not stop the match (a control on `normalize`; the red proofs above
    cover the matching rule itself)."""
    root = _private_bugs_dir
    repo = make_repo(tmp_path / "repo")
    run_git(repo, "checkout", "-q", "-b", "side")
    run = commit(repo, "a.py", "x = 1\n", RUN_MSG + trailer, date="2026-10-13T09:00:00+00:00")
    run_git(repo, "checkout", "-q", "master")
    merge = commit(repo, "m.py", "x = 2\n", "fix: the series (#12)\n\n* " + RUN_MSG,
                   date="2026-10-14T09:00:00+00:00")
    publish(repo)
    pending_record(root, tmp_path, "r1", captured="2026-10-13T10:00:00Z", commit=run)
    rep = relink.relink(root, [repo], write=True, now=NOW)
    assert rep.rows_written == 2 and rep.superseding == 0
    assert {r.fix_commit for r in _bugs(root)} == {merge}
