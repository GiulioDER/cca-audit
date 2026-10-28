"""`bugs stats` counts fixes, never false positives or superseded rows, by anchor not commit."""
from __future__ import annotations

from dataclasses import replace

from test_anchor_recovery import row

from cca_checks.bugs import stats, store
from cca_checks.bugs.anchors import Anchor
from cca_checks.bugs.records import read_ledger


def _ledger(tmp_path, rows):
    store.write(rows, tmp_path, "2026-10-28T00:00:00Z")
    got, errors = read_ledger(tmp_path)
    assert not errors
    return got


def test_a_superseded_fix_is_counted_once(tmp_path):
    """Invariant: a bug row that a later row supersedes is counted as its successor only, so
    the severity totals count fixes, not ledger lines.

    Red 2026-10-28: mutation in `stats._bugs`, `return [r for r in bugs if r.bug_id not in
    replaced]` became `return bugs`; failed at `assert s["fixes"] == 2` (3).
    """
    first = replace(row(), severity="P1")
    second = replace(row(), severity="P1", fix_commit="b" * 40, supersedes=first.bug_id)
    third = replace(row(), severity="P3", finding_id="SEC-2", fix_commit="c" * 40)
    s = stats.summarize(_ledger(tmp_path, [first, second, third]))
    assert s["fixes"] == 2
    assert s["severity"] == {"P1": 1, "P2": 0, "P3": 1, "unknown": 0}
    assert s["by_project"]["widgets"]["total"] == 2


def test_files_are_counted_by_anchor_not_by_fix_commit(tmp_path):
    """Invariant: a fix counts against the file its finding was anchored to, not every file
    its commit touched; a fix with no anchor counts against no file.

    Red 2026-10-28: mutation in `stats.summarize`, `for place in {(r.project, a.file) for a
    in r.anchors}` became `for place in {(r.project, f) for f in r.files}`; failed at
    `assert files == {"pool.py": 1}` (CHANGELOG.md and pool.py counted).
    """
    anchored = replace(row(), files=["CHANGELOG.md", "pool.py"],
                       anchors=[Anchor(file="pool.py", symbol="lease", code_sha="1" * 64)])
    bare = replace(row(), finding_id="DOC-9", fix_commit="d" * 40, files=["CHANGELOG.md"])
    s = stats.summarize(_ledger(tmp_path, [anchored, bare]))
    files = {t["file"]: t["fixes"] for t in s["top_files"]}
    assert files == {"pool.py": 1}
    assert s["anchored"] == 1


def test_domain_comes_from_the_finding_id_prefix():
    """Invariant: `SEC-002` is SEC; legacy severity numbering (`P1-03`, `F-12`) is unattributed.

    Red 2026-10-28: mutation in `stats.domain`, `return prefix if prefix in DOMAINS else
    "unattributed"` became `return prefix or "unattributed"`; failed at the `F-12` assert ('F').
    (`P1-03` never matches the letters-then-dash prefix, so it stays unattributed either way.)
    """
    assert stats.domain(replace(row(), finding_id="SEC-002")) == "SEC"
    assert stats.domain(replace(row(), finding_id="STAKES-1")) == "STAKES"
    assert stats.domain(replace(row(), finding_id="P1-03")) == "unattributed"
    assert stats.domain(replace(row(), finding_id="F-12")) == "unattributed"
