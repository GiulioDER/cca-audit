"""Integration tests for the tautological-test detector.

These build a real package with a real defect, snapshot it, fix it, and drive the
detector end-to-end — the check itself is about executing code, so testing it by
mocking would reproduce exactly the mistake it exists to catch.
"""
import json
import subprocess
import sys
from pathlib import Path

import cca_tautology_check as tc
import pytest

BUGGY = '''\
def net_payout(gross, fee_bps):
    """Payout net of a bps fee."""
    return gross + gross * (fee_bps / 100.0)
'''

FIXED = '''\
def net_payout(gross, fee_bps):
    """Payout net of a bps fee."""
    return gross - gross * (fee_bps / 10000.0)


def assert_sane(x):
    return x
'''

TESTS = '''\
from money import net_payout

def test_genuine_proof():
    # fails pre-fix (1500.0), passes post-fix (995.0)
    assert net_payout(1000.0, 50.0) == 995.0

def test_tautological():
    # passes both pre- and post-fix: proves nothing
    assert net_payout(1000.0, 0.0) == 1000.0

def test_errors_prefix():
    # imports a symbol only the FIX introduces -> ImportError pre-fix
    from money import assert_sane
    assert assert_sane(1) == 1
'''


@pytest.fixture
def proj(tmp_path, monkeypatch):
    (tmp_path / "money.py").write_text(BUGGY, encoding="utf-8")
    (tmp_path / "test_money.py").write_text(TESTS, encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return tmp_path


def snapshot_then_fix(proj):
    tc.cmd_snapshot([Path("money.py")], tc.DEFAULT_SNAPDIR)
    (proj / "money.py").write_text(FIXED, encoding="utf-8")   # Layer 4 applies the fix


def test_detects_genuine_proof_tautology_and_inconclusive(proj):
    snapshot_then_fix(proj)
    results = tc.verify([
        ("FIX-001", "test_money.py::test_genuine_proof"),
        ("FIX-002", "test_money.py::test_tautological"),
        ("FIX-003", "test_money.py::test_errors_prefix"),
    ])
    by = {r.finding: r.verdict for r in results}
    assert by["FIX-001"] == tc.RED             # really was red
    assert by["FIX-002"] == tc.TAUTOLOGICAL    # <- the detection
    assert by["FIX-003"] == tc.INCONCLUSIVE    # errored: not proof, not silently accepted


def test_working_tree_is_restored_to_the_FIXED_content(proj):
    snapshot_then_fix(proj)
    tc.verify([("FIX-001", "test_money.py::test_genuine_proof")])
    assert (proj / "money.py").read_text(encoding="utf-8") == FIXED


def test_restore_happens_even_when_a_proof_run_blows_up(proj, monkeypatch):
    snapshot_then_fix(proj)
    monkeypatch.setattr(tc, "_run_one", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        tc.verify([("FIX-001", "test_money.py::test_genuine_proof")])
    assert (proj / "money.py").read_text(encoding="utf-8") == FIXED   # not left pre-fix


def test_fixed_content_is_stashed_before_any_revert(proj):
    snapshot_then_fix(proj)
    tc.verify([("FIX-001", "test_money.py::test_genuine_proof")])
    stashed = tc.DEFAULT_SNAPDIR / "fixed" / "money.py"
    assert stashed.exists() and stashed.read_text(encoding="utf-8") == FIXED


def test_missing_snapshot_is_a_loud_error_not_a_silent_pass(proj):
    (proj / "money.py").write_text(FIXED, encoding="utf-8")   # fixed, but never snapshotted
    with pytest.raises(SystemExit, match="no snapshot"):
        tc.verify([("FIX-001", "test_money.py::test_genuine_proof")])


def test_empty_snapshot_is_also_an_error(proj):
    tc.cmd_snapshot([Path("does_not_exist.py")], tc.DEFAULT_SNAPDIR)
    with pytest.raises(SystemExit, match="empty"):
        tc.verify([("FIX-001", "test_money.py::test_genuine_proof")])


def test_cli_exit_code_is_nonzero_when_a_proof_is_not_a_proof(proj):
    snapshot_then_fix(proj)
    r = subprocess.run([sys.executable, str(Path(tc.__file__)), "verify",
                        "--proof", "FIX-002=test_money.py::test_tautological", "--json"],
                       capture_output=True, text=True)
    assert r.returncode == 1, r.stdout + r.stderr          # it is a GATE, not a report
    assert json.loads(r.stdout)[0]["verdict"] == tc.TAUTOLOGICAL


def test_cli_exit_code_zero_when_all_proofs_genuine(proj):
    snapshot_then_fix(proj)
    r = subprocess.run([sys.executable, str(Path(tc.__file__)), "verify",
                        "--proof", "FIX-001=test_money.py::test_genuine_proof"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "1/1 genuine" in r.stdout
