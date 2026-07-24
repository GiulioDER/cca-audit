"""Tests for the CCA statistical-filtering layer.

The load-bearing one is `test_never_emits_a_drop`: the additive-only property must
be structural, so it cannot regress into the code later as a "small optimisation".
"""
import json
from dataclasses import fields
from datetime import datetime, timedelta, timezone

import cca_scorecard as sc
import pytest

NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def write(tmp_path, rows):
    p = tmp_path / "ledger.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows), encoding="utf-8")
    return p


def row(verdict, auditor="bug-auditor", category="cat", days_ago=1):
    return {"ts": (NOW - timedelta(days=days_ago)).isoformat().replace("+00:00", "Z"),
            "run_id": "r", "auditor": auditor, "category": category,
            "verdict": verdict, "fix_id": "FIX-001"}


def test_below_n_min_is_learning_and_never_acted_on(tmp_path):
    # 9 findings, every one a false positive -> precision 0.0, the worst possible cell.
    led = write(tmp_path, [row("FALSE_POSITIVE") for _ in range(9)])
    rep = sc.build(led, now=NOW, routing=True)
    assert rep.cells[0].learning is True
    assert rep.route_up == [] and rep.review == []


def test_at_n_min_a_bad_cell_routes_up_when_routing_on(tmp_path):
    led = write(tmp_path, [row("FALSE_POSITIVE") for _ in range(8)]
                + [row("CONFIRMED") for _ in range(2)])   # n=10, precision 0.2
    rep = sc.build(led, now=NOW, routing=True)
    assert rep.cells[0].n == 10
    assert rep.cells[0].precision == pytest.approx(0.2)
    assert rep.route_up == ["bug-auditor/cat"]
    assert rep.review == ["bug-auditor/cat"]           # 0.2 < 0.40 too


def test_routing_off_is_the_default_and_suppresses_only_the_route_action(tmp_path):
    led = write(tmp_path, [row("FALSE_POSITIVE") for _ in range(8)]
                + [row("CONFIRMED") for _ in range(2)])
    rep = sc.build(led, now=NOW)                        # routing defaults off
    assert rep.route_up == []
    assert rep.review == ["bug-auditor/cat"]            # surfacing is ALWAYS on


def test_good_cell_is_not_routed(tmp_path):
    led = write(tmp_path, [row("CONFIRMED") for _ in range(9)]
                + [row("FALSE_POSITIVE")])              # precision 0.9
    rep = sc.build(led, now=NOW, routing=True)
    assert rep.route_up == [] and rep.review == []


def test_uncertain_and_duplicate_excluded_from_denominator(tmp_path):
    led = write(tmp_path, [row("CONFIRMED"), row("FALSE_POSITIVE")]
                + [row("UNCERTAIN") for _ in range(20)]
                + [row("DUPLICATE") for _ in range(20)])
    rep = sc.build(led, now=NOW, routing=True)
    assert rep.cells[0].n == 2                          # NOT 42
    assert rep.cells[0].learning is True                # so still under the guard


def test_window_excludes_stale_rows(tmp_path):
    led = write(tmp_path, [row("FALSE_POSITIVE", days_ago=200) for _ in range(20)]
                + [row("CONFIRMED", days_ago=1)])
    rep = sc.build(led, now=NOW, routing=True)
    assert rep.cells[0].n == 1                          # the 20 stale rows dropped out
    assert rep.route_up == []


def test_outcome_rows_are_skipped_not_scored(tmp_path):
    led = write(tmp_path, [row("CONFIRMED"),
                           {"run_id": "r", "fix_id": "FIX-001", "result": "fixed"}])
    rep = sc.build(led, now=NOW)
    assert rep.rows_scored == 1 and rep.rows_skipped_outcome == 1


def test_malformed_rows_are_counted_not_silently_dropped(tmp_path):
    p = tmp_path / "ledger.jsonl"
    p.write_text(json.dumps(row("CONFIRMED")) + "\n{not json}\n"
                 + json.dumps({"verdict": "CONFIRMED"}) + "\n", encoding="utf-8")
    rep = sc.build(p, now=NOW)
    assert rep.rows_skipped_malformed == 2              # bad JSON + row missing ts/auditor
    assert "malformed" in sc.render(rep)                # and it is SURFACED, not hidden


def test_yield_flag_for_high_volume_zero_confirmed_auditor(tmp_path):
    led = write(tmp_path, [row("FALSE_POSITIVE", category=f"c{i}") for i in range(15)])
    rep = sc.build(led, now=NOW)
    assert rep.yield_flags == ["bug-auditor"]


def test_missing_ledger_is_empty_not_an_error(tmp_path):
    rep = sc.build(tmp_path / "nope.jsonl", now=NOW)
    assert rep.cells == [] and rep.route_up == []


def test_never_emits_a_drop(tmp_path):
    """Structural: no configuration of thresholds or data can yield a suppression.

    `Report` must not grow a field that can express one. If someone later adds
    `drop`/`suppress`/`exclude`/`demote`, this test fails and they have to justify
    turning a suppression rate into a score.
    """
    banned = ("drop", "suppress", "exclude", "demote", "ignore", "silence")
    names = {f.name for f in fields(sc.Report)}
    assert not [n for n in names if any(b in n for b in banned)], names

    # And empirically: the worst imaginable cell still only ever gets ADDED scrutiny.
    led = write(tmp_path, [row("FALSE_POSITIVE") for _ in range(50)])
    rep = sc.build(led, now=NOW, routing=True)
    assert rep.cells[0].precision == 0.0
    assert rep.route_up == ["bug-auditor/cat"]          # more verification...
    assert rep.review == ["bug-auditor/cat"]            # ...and a human look
    # ...and nothing anywhere in the report tells a caller to skip that cell.
    assert "bug-auditor/cat" in sc.render(rep)
