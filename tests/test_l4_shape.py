"""L4 files in either stored shape reach the ledger, and a shape nothing can read is refused.

Measured 2026-10-28: 12 runs captured from 2026-10-21 to 2026-10-27, by both clients, stored L4
as an object, `{"fixes": [...]}`, whose entries were canonical or `{"id": "FIX-1", "findings":
[...]}`. `forward.rows_and_skips` iterated the object as a list, found nothing, and resolve
reported every one as "no ledger rows could be built", so their fixes never reached the ledger.
"""
from __future__ import annotations

import json

import pytest
from conftest import findings_output, plan_output, verdicts_output, write_output
from test_bugs_forward import MERGE, make_record

from cca_checks import layer_outputs
from cca_checks import pipeline_state as ps
from cca_checks.bugs.forward import rows_for


@pytest.mark.parametrize("entries", [
    [{"id": "FIX-001", "findings": ["BUG-001", "DOC-001"], "files": ["a.py"]}],
    [{"fix_id": "FIX-001", "finding_id": "BUG-001", "files": ["a.py"]},
     {"fix_id": "FIX-002", "finding_id": "DOC-001", "files": ["b.py"]}],
])
def test_an_object_shaped_l4_still_builds_ledger_rows(tmp_path, entries):
    """Invariant: `{"fixes": [...]}` builds one row per finding, whether its entries list
    `findings` under a fix `id` or are canonical; keys beside `fixes` hold no applied fix.

    Red 2026-10-28: mutation in `forward.rows_and_skips`, `entries = l4_fixes(...)` back to
    `entries = [e for e in _load(record, "L4.json", []) if isinstance(e, dict)]`; failed at
    `assert ids == ["BUG-001", "DOC-001"]` ([] for both cases).
    """
    rec = make_record(tmp_path)
    (rec / "L4.json").write_text(json.dumps({"fixes": entries, "parked": ["SEC-009"]}))
    ids = [r.finding_id for r in rows_for(rec, MERGE)]
    assert ids == ["BUG-001", "DOC-001"]


def test_an_l4_entry_naming_no_finding_is_a_shape_error():
    """Invariant: an entry with no finding id in any of its spellings is refused, not dropped
    (a control on the normaliser's refusal; the record-time test below proves it is used)."""
    with pytest.raises(layer_outputs.ShapeError):
        layer_outputs.l4_fixes({"fixes": [{"files": ["a.py"]}]})
    with pytest.raises(layer_outputs.ShapeError):
        layer_outputs.l4_fixes({"applied": []})


def test_record_l4_refuses_a_file_the_ledger_cannot_read(git_repo):
    """Invariant: `pipeline record L4` refuses an L4 file in neither readable shape, so the run
    is corrected while it is open instead of losing its fixes at resolve.

    Red 2026-10-28: mutation deleting the `elif layer == "L4":` branch of
    `pipeline_state._check_shape`; failed at `pytest.raises(ps.RecordRefused)` (DID NOT RAISE).
    """
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 1}, output=findings_output(git_repo, 1), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo, confirmed=1), root=git_repo)
    ps.record("L3", counts={"to_fix": 1}, output=plan_output(git_repo, to_fix=1), root=git_repo)
    (git_repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    with pytest.raises(ps.RecordRefused) as refused:
        ps.record("L4", fixes=1, output=write_output(git_repo, {"applied": [{"id": "FIX-1"}]}),
                  root=git_repo)
    assert "L4" in str(refused.value)
    assert "L4" not in ps.load(git_repo).layers
