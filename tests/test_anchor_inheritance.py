"""A superseding bug row with no anchors of its own inherits its predecessor's, in every reader.

Recovered anchors are keyed by bug id, so a correction that re-keys a row lost the place the
old row had: on 2026-10-29, after `bugs relink`, RE-call:b27298bf4125:SEC-001 no longer carried
`recall/lite/refresh.py _refresh_locked`, which its unlinked predecessor did.
"""
from __future__ import annotations

from dataclasses import replace

from test_anchor_recovery import row

from cca_checks.bugs import prior, store
from cca_checks.bugs.anchors import Anchor
from cca_checks.bugs.records import read_ledger

PLACE = Anchor(file="pool.py", symbol="lease", code_sha="1" * 64)


def _pair(tmp_path, *, successor_files=None, successor_anchors=None):
    before = replace(row(), anchors=[PLACE], files=["pool.py", "CHANGELOG.md"])
    after = replace(row(), fix_commit="b" * 40, supersedes=before.bug_id,
                    files=successor_files or ["pool.py", "CHANGELOG.md"],
                    anchors=successor_anchors or [])
    store.write([before, after], tmp_path, "2026-10-29T00:00:00Z")
    return before, after


def test_every_reader_gives_the_successor_its_predecessors_anchors(tmp_path):
    """Invariant: `records.read_ledger`, `store.bug_rows` and `prior.load_rows` all give an
    anchorless superseding row the anchors of the row it supersedes.

    Red 2026-10-29, two mutations in `anchor_log`: in `inherit_rows`, `out[row_id] =
    replace(row, anchors=list(before.anchors))` became `pass`; failed at the `read_ledger`
    assert. In `inherit_dicts`, `row["anchors"] = [dict(a) for a in anchors]` became `pass`;
    failed at the `prior.load_rows` assert.
    """
    _, after = _pair(tmp_path)
    rows, _ = read_ledger(tmp_path)
    assert rows[after.bug_id].anchors == [PLACE]
    by_id = {r.bug_id: r for r in store.bug_rows(tmp_path / store.LEDGER)}
    assert by_id[after.bug_id].anchors == [PLACE]
    legacy = {r["bug_id"]: r for r in prior.load_rows(tmp_path / store.LEDGER)}
    assert [a["file"] for a in legacy[after.bug_id]["anchors"]] == ["pool.py"]


def test_an_anchor_outside_the_successors_fix_is_not_inherited(tmp_path):
    """Invariant: an inherited anchor's file must be one the successor's fix touched; a place
    the successor's commit never changed is not carried over.

    Red 2026-10-29: mutation in `anchor_log._fits`, the body became `return True`; failed at
    `assert rows[after.bug_id].anchors == []`.
    """
    _, after = _pair(tmp_path, successor_files=["other.py"])
    rows, _ = read_ledger(tmp_path)
    assert rows[after.bug_id].anchors == []


def test_the_successors_own_anchors_win(tmp_path):
    """A control: inheritance never overrides anchors the successor already has (no mutation of
    the inheritance turns this red, since the guard `row.anchors` short-circuits first)."""
    own = Anchor(file="pool.py", symbol="release", code_sha="2" * 64)
    _, after = _pair(tmp_path, successor_anchors=[own])
    rows, _ = read_ledger(tmp_path)
    assert rows[after.bug_id].anchors == [own]
