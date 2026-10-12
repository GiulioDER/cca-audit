"""Tests for the CCA statistical-filtering layer.

The load-bearing one is `test_never_emits_a_drop`: the additive-only property must
be structural, so it cannot regress into the code later as a "small optimisation".
"""
import json
import shutil
import subprocess
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


def test_a_bom_prefixed_ledger_scores_its_first_row(tmp_path):
    """A ledger created by Windows PowerShell 5.1 starts with a BOM. L2.5 counts its first row
    (the pipeline reads the ledger as utf-8-sig), so the scorecard must score it too, not call
    it malformed.

    Red 2026-10-11: mutation `encoding="utf-8-sig"` became `encoding="utf-8"` in load_rows;
    failed at `assert rep.rows_scored == 2 and rep.rows_skipped_malformed == 0` (1 scored, 1
    malformed).
    """
    p = tmp_path / "ledger.jsonl"
    body = json.dumps(row("CONFIRMED")) + "\n" + json.dumps(row("FALSE_POSITIVE")) + "\n"
    p.write_bytes(b"\xef\xbb\xbf" + body.encode("utf-8"))
    rep = sc.build(p, now=NOW)
    assert rep.rows_scored == 2 and rep.rows_skipped_malformed == 0


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


def _git(root, *args):
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


@pytest.fixture
def git_repo(tmp_path):
    """A one commit repository that ignores .claude/ (local: tools/ has no conftest)."""
    if shutil.which("git") is None:
        pytest.skip("git is required for tree fingerprints")
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.invalid")
    _git(tmp_path, "config", "user.name", "t")
    _git(tmp_path, "config", "core.autocrlf", "false")
    (tmp_path / ".gitignore").write_text(".claude/\n", encoding="utf-8")
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-q", "-m", "init")
    return tmp_path


def test_record_writes_l2_6_as_a_tool_layer(git_repo, monkeypatch, capsys):
    """Node: test_record_writes_l2_6_as_a_tool_layer.

    Mutation: pass source="cli" to pipeline_state.record in cca_scorecard.main.
    Fails: RecordRefused, main returns 2, so the `== 0` assertion fails.

    Red 2026-10-10: source="cli" in cca_scorecard.main; failed at `assert sc.main() == 0` (2 == 0)
    """
    from cca_checks import pipeline_state as ps
    # Layer outputs live under .claude/audits/, which the fingerprint ignores.
    out = git_repo / ".claude" / "audits" / "layer-output.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("[]", encoding="utf-8")
    ps.start("STANDARD", root=git_repo)
    ps.record("L1", output=out, root=git_repo)
    ps.record("L2", counts={"unique": 0}, output=out, root=git_repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=out, root=git_repo)
    monkeypatch.chdir(git_repo)
    monkeypatch.setattr("sys.argv", ["cca_scorecard.py", "--json", "--record"])
    assert sc.main() == 0
    state = ps.load(git_repo)
    assert state is not None
    entry = state.layers["L2.6"]
    assert entry["source"] == "tool" and entry["status"] == "done"


def test_stale_rows_never_count_toward_a_yield_flag(tmp_path):
    """A STALE row says the code moved on after a run deferred the finding, not that the
    auditor was wrong, so it must not count as disposed.

    Red 2026-10-11: baseline (every verdict row increments `disposed`) in cca_scorecard.build;
    failed at `assert rep.yield_flags == []` (got ["bug-auditor"]).
    """
    led = write(tmp_path, [row("STALE") for _ in range(15)])
    rep = sc.build(led, now=NOW)
    assert rep.yield_flags == []
    assert rep.cells == []
