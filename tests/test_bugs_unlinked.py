"""`bugs record-unlinked`: a stuck run's fixes reach the ledger, as claimed, exactly once."""
from __future__ import annotations

from datetime import datetime, timezone

from test_bugs_resolve import SHA, gh_merged, pending_record

from cca_checks.bugs import unlinked
from cca_checks.bugs.model import CLAIMED
from cca_checks.bugs.records import read_ledger
from cca_checks.bugs.resolve import resolve

NOW = datetime(2026, 10, 28, 20, 0, tzinfo=timezone.utc)


def _bugs(root):
    rows, errors = read_ledger(root)
    assert not errors
    return [r for r in rows.values() if getattr(r, "kind", "") == "bug"]


def test_a_stuck_run_is_written_as_claimed_and_leaves_pending(_private_bugs_dir, tmp_path):
    """Invariant: an old enough pending run's fixes are written keyed by the run's own commit,
    with `fixed` CLAIMED (so historical, never proved), an `unlinked:` source and no `pr:`
    source, and the record moves to unlinked/ so resolve can never write it again.

    Red 2026-10-28, two mutations in `unlinked.record_unlinked`: deleting
    `row.evidence["fixed"] = CLAIMED`; failed at `assert all(r.evidence["fixed"] == CLAIMED
    ...)`. Deleting `_move(folder, root, UNLINKED)`; failed at `assert not any(pending...)`.
    """
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1")
    rep = unlinked.record_unlinked(root, write=True, now=NOW)
    rows = _bugs(root)
    assert rep.rows_written == 2 and len(rows) == 2
    assert all(r.evidence["fixed"] == CLAIMED and r.tier == "historical" for r in rows)
    assert all(r.fix_commit == SHA for r in rows)
    assert all(unlinked.UNLINKED_SOURCE in r.sources for r in rows)
    assert not any(s.startswith("pr:") for r in rows for s in r.sources)
    assert not any((root / "pending").iterdir())
    assert (root / unlinked.UNLINKED).is_dir()
    assert resolve(root, write=True, run=lambda args: "[]").rows_written == 0


def test_a_recent_run_is_left_for_resolve(_private_bugs_dir, tmp_path):
    """Invariant: a run captured within `older_than_hours` stays in pending/, untouched, so a
    run whose pull request is still open gets resolve's merge link first.

    Red 2026-10-28: mutation in `unlinked.record_unlinked`, the age test
    `now - captured < timedelta(hours=older_than_hours)` became `False`; failed at
    `assert rep.rows_written == 0`.
    """
    root = _private_bugs_dir
    folder = pending_record(root, tmp_path, "r1", captured="2026-10-28T19:00:00Z")
    rep = unlinked.record_unlinked(root, write=True, now=NOW)
    assert rep.rows_written == 0 and rep.too_recent == [folder.name]
    assert folder.exists()


def test_a_finding_already_recorded_for_the_run_is_not_written_again(_private_bugs_dir,
                                                                      tmp_path, monkeypatch):
    """Invariant: a finding the ledger already holds a bug row for, from the same run, is not
    written again under the run's commit. The case that matters is a row resolve wrote under
    the squash merge: its key differs from the run-commit key, so the store cannot catch it.

    Red 2026-10-28: mutation in `unlinked.record_unlinked`, the guard
    `if (row.run_id, row.finding_id) in held:` became `if False:`; failed at
    `assert len(_bugs(root)) == 2` (4: both findings under the merge and the run commit).
    """
    monkeypatch.setenv("CCA_BUGS_OWNERS", "acme")
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1")
    assert resolve(root, write=True, run=gh_merged).rows_written == 2  # keyed by MERGE_SHA
    pending_record(root, tmp_path / "again", "r1")  # the same run, captured a second time
    rep = unlinked.record_unlinked(root, write=True, now=NOW)
    assert len(_bugs(root)) == 2
    assert len(rep.already_backfilled) == 2
