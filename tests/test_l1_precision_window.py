"""The L1 precision script selects runs by time, and can still reproduce the baseline by id.

Invariant: a run inside the window is chosen by its earliest verdict time, whatever its id looks
like, and a run named rather than stamped never passes a bound it falls outside of. The defect,
found 2026-10-30 at the pre-registered 15-run look: `--since` compared run ids as text, so
`cca-deep-20260906` and `pr898-20261012` passed `--since 20261028T200105Z`.

Red proof (run 2026-10-30):
- test_a_named_run_outside_the_bound_is_excluded: against the pre-change `l1_precision.py`
  (master `8d6f4f7`), `rows(root, "20261028T200105Z", "~")` kept `cca-deep-20260906` and
  `pr898-20261012`; failed at `assert ids == [...]`. Mutation `and (lo is None or t >= lo)` ->
  `and True` in `window` failed at the same assertion.
The other four call `window`, which the pre-change module lacks (not red proof), so each was
proved by a mutation of the new code, failing on its assertion and restored:
- test_a_named_run_inside_the_window_counts_by_its_time and test_first_keeps_the_earliest_runs:
  `key=lambda r: (times[r], r))` -> `key=lambda r: r)` (order by id, not time); failed at
  `assert ids == [...]` and `assert _ids(found) == [...]`.
- test_by_id_reproduces_the_old_comparison: `if by_id:` -> `if False:`; failed at
  `assert ids == [...]`.
- test_an_untimed_run_is_reported_not_counted: `untimed = sorted(...)` -> `untimed = []`; failed
  at `assert note["untimed_runs"] == ["legacy-run"]`.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

HARNESS = Path(__file__).resolve().parents[1] / "benchmarks" / "harness" / "l1_precision.py"
_spec = importlib.util.spec_from_file_location("l1_precision_under_test", HARNESS)
l1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(l1)


def _ledger(root: Path, rows: list[dict]) -> Path:
    path = root / "w" / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return root


def _row(run, ts, fid="BUG-001", verdict="CONFIRMED"):
    return {"run_id": run, "ts": ts, "finding_id": fid, "verdict": verdict}


ROWS = [
    _row("cca-deep-20260906", "2026-09-06T00:00:00Z"),
    _row("pr898-20261012", "2026-10-12T19:20:12+00:00"),
    _row("20261028T185351Z", "2026-10-28T18:55:00+00:00"),
    _row("20261028T204421Z", "2026-10-28T21:04:08Z"),
    _row("pr709-700-deferred-20261029", "2026-10-29T15:00:49+00:00"),
    _row("20261029T202226Z", "2026-10-29T20:36:32+00:00"),
]


def _ids(rows):
    return sorted({r["run_id"] for r in rows})


def test_a_named_run_outside_the_bound_is_excluded(tmp_path):
    root = _ledger(tmp_path, ROWS)
    ids = _ids(l1.rows(root, "20261028T200105Z", "~"))
    assert ids == ["20261028T204421Z", "20261029T202226Z", "pr709-700-deferred-20261029"]


def test_a_named_run_inside_the_window_counts_by_its_time(tmp_path):
    found, note = l1.window(l1._all_rows(_ledger(tmp_path, ROWS)), "2026-10-28T20:01:05Z")
    ids = [r["run_id"] for r in sorted(found, key=lambda r: r["ts"])]
    assert ids == ["20261028T204421Z", "pr709-700-deferred-20261029", "20261029T202226Z"]
    assert note["first"] == "20261028T204421Z" and note["last"] == "20261029T202226Z"


def test_first_keeps_the_earliest_runs(tmp_path):
    found, note = l1.window(l1._all_rows(_ledger(tmp_path, ROWS)), "20261028T200105Z", first=2)
    assert _ids(found) == ["20261028T204421Z", "pr709-700-deferred-20261029"]
    assert note["last"] == "pr709-700-deferred-20261029"


def test_by_id_reproduces_the_old_comparison(tmp_path):
    """The pre-registered baseline was `--until 20261029T000000Z` by id text: no named id."""
    found, _ = l1.window(l1._all_rows(_ledger(tmp_path, ROWS)), until="20261029T000000Z",
                         by_id=True)
    ids = _ids(found)
    assert ids == ["20261028T185351Z", "20261028T204421Z"]


def test_an_untimed_run_is_reported_not_counted(tmp_path):
    root = _ledger(tmp_path, ROWS + [_row("legacy-run", None)])
    found, note = l1.window(l1._all_rows(root), "20261028T200105Z")
    assert "legacy-run" not in _ids(found)
    assert note["untimed_runs"] == ["legacy-run"]
