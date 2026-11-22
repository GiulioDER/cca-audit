"""Red proofs for fixes made during the fix audit (L5.4), Task 7 of the fix audit loop.

A bug a Layer 4 fix introduced did not exist before Layer 4, so its test can only be red
against the tree the fix audit pass audited. `--proof FINDING=NODEID@SNAPDIR` names that
pass's snapshot. These drive the real tool end to end: each proof runs a real pytest
against reverted files, because a mocked run would not show which tree it judged.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cca_checks.plugin.tools import cca_tautology_check as tc

PRE_L4 = "def norm(s):\n    return s.strip()\n"        # the original bug: no lowercasing
L4 = "def norm(s):\n    return s.lower()\n"            # fixes it and drops the strip
FIX_AUDIT = "def norm(s):\n    return s.strip().lower()\n"
TESTS = '''\
import pytest
from norm import norm

def test_lowercases():
    assert norm("A") == "a"

def test_strips():
    assert norm(" a ") == "a"

@pytest.mark.parametrize("raw", ["a@b"])
def test_keeps_an_at_sign(raw):
    assert norm(raw) == raw
'''
PASS_1 = Path(".claude/audits/.fixaudit_snapshot/pass-1")
LOWERS = "test_norm.py::test_lowercases"
STRIPS = "test_norm.py::test_strips"
AT_SIGN = "test_norm.py::test_keeps_an_at_sign[a@b]"


@pytest.fixture
def run(git_repo, monkeypatch):
    """A tree after one fix audit pass: both snapshots taken, the fix audit fix applied."""
    monkeypatch.chdir(git_repo)
    norm = git_repo / "norm.py"
    norm.write_text(PRE_L4, encoding="utf-8")
    (git_repo / "test_norm.py").write_text(TESTS, encoding="utf-8")
    tc.cmd_snapshot([Path("norm.py")], tc.DEFAULT_SNAPDIR)   # Layer 4 start
    norm.write_text(L4, encoding="utf-8")
    tc.cmd_snapshot([Path("norm.py")], PASS_1)               # before the fix audit edit
    norm.write_text(FIX_AUDIT, encoding="utf-8")
    return git_repo


def _cli(capsys, *proofs: str) -> list[tuple[str, str, str]]:
    """(finding, nodeid, verdict) per proof, as `verify --json` prints them."""
    capsys.readouterr()
    try:
        tc.main(["verify", "--json", *(arg for p in proofs for arg in ("--proof", p))])
    except SystemExit as exc:  # a refused snapshot is an outcome the tests compare
        return [("refused", str(exc), "")]
    return [(r["finding"], r["nodeid"], r["verdict"])
            for r in json.loads(capsys.readouterr().out)]


def _left_clean(root: Path) -> bool:
    """The fixed content is back and no snapshot is marked as left reverted."""
    return ((root / "norm.py").read_text(encoding="utf-8") == FIX_AUDIT
            and not (tc.DEFAULT_SNAPDIR / tc.IN_PROGRESS).exists()
            and not (PASS_1 / tc.IN_PROGRESS).exists())


def test_a_fix_audit_proof_is_red_against_its_own_pass_snapshot(run, capsys):
    """Invariant: a proof suffixed @SNAPDIR runs against that snapshot's pre-fix copy.

    Failure mode: the suffix was ignored or kept in the node id, so a fix audit proof was
    judged against the pre-L4 tree (TAUTOLOGICAL) or never collected (INCONCLUSIVE).
    Red: tests/test_fixaudit_proofs.py::test_a_fix_audit_proof_is_red_against_its_own_pass_snapshot
    against the pre-fix tool (HEAD f867276) fails the verdict assertion with INCONCLUSIVE;
    against the mutation `where = snapdir` in `verify` (the own snapshot ignored) it fails
    the same assertion with TAUTOLOGICAL.
    """
    assert _cli(capsys, f"FA-1-1={STRIPS}@{PASS_1.as_posix()}") == [("FA-1-1", STRIPS, tc.RED)]
    assert _left_clean(run)


def test_the_same_proof_against_the_pre_l4_snapshot_reads_tautological(run):
    """Control, passes before and after by design: it shows why the pass snapshot is needed.

    The bug the fix audit found did not exist before Layer 4, so its test passes there.
    """
    [result] = tc.verify([("FA-1-1", STRIPS)])
    assert result.verdict == tc.TAUTOLOGICAL
    assert _left_clean(run)


def test_a_proof_without_a_suffix_uses_the_default_snapdir(run, capsys):
    """Control, passes before and after by design: an unsuffixed proof keeps its old meaning.

    Against PASS_1 this proof would read TAUTOLOGICAL, so RED shows the default snapshot.
    """
    assert _cli(capsys, f"FIX-1={LOWERS}") == [("FIX-1", LOWERS, tc.RED)]
    assert _left_clean(run)


def test_a_node_id_holding_an_at_sign_is_not_split(run, capsys):
    """Invariant: text after the last @ is a snapshot only when it holds a manifest.

    Failure mode: a parametrize id such as [a@b] was split, so the proof was refused for a
    missing snapshot `b]` or run under a truncated node id.
    Red: tests/test_fixaudit_proofs.py::test_a_node_id_holding_an_at_sign_is_not_split against
    the mutation `if node and where:` in `main` (the manifest check dropped) fails the
    comparison with [("refused", "no snapshot at b] ...", "")].
    """
    assert _cli(capsys, f"FA-1-2={AT_SIGN}") == [("FA-1-2", AT_SIGN, tc.TAUTOLOGICAL)]
    assert _left_clean(run)


def test_proofs_naming_two_snapshots_keep_their_order_and_their_own_trees(run, capsys):
    """Invariant: each proof is judged against its own snapshot, results in input order.

    Failure mode: results came back grouped by snapshot, so a reader pairing them with the
    proofs it passed would credit one finding with another's verdict.
    Red: tests/test_fixaudit_proofs.py::test_proofs_naming_two_snapshots_keep_their_order_and_their_own_trees
    against the mutation `return list(ordered.values())` in `verify` fails the comparison
    with FIX-2 before FA-1-1.
    """
    assert _cli(capsys, f"FIX-1={LOWERS}", f"FA-1-1={STRIPS}@{PASS_1.as_posix()}",
                f"FIX-2={STRIPS}") == [("FIX-1", LOWERS, tc.RED),
                                       ("FA-1-1", STRIPS, tc.RED),
                                       ("FIX-2", STRIPS, tc.TAUTOLOGICAL)]
    assert _left_clean(run)


def test_a_crash_against_a_pass_snapshot_leaves_the_tree_fixed(run, monkeypatch):
    """Invariant: a proof run that blows up against one snapshot still restores its files.

    Failure mode: the second snapshot's cycle reverted norm.py to the L4 copy and the crash
    skipped its restore, so the fix audit fix was lost from the tree.
    Red: tests/test_fixaudit_proofs.py::test_a_crash_against_a_pass_snapshot_leaves_the_tree_fixed
    against the mutation in `_verify_against` that restores only after every proof ran
    (`finally:` replaced by `except Exception: raise` and an unguarded restore) fails the
    norm.py assertion with the L4 copy.
    """
    real = tc._run_one

    def run_one(nodeid, extra):
        if nodeid == STRIPS:
            raise RuntimeError("boom")
        return real(nodeid, extra)

    monkeypatch.setattr(tc, "_run_one", run_one)
    with pytest.raises(RuntimeError):
        tc.verify([("FIX-1", LOWERS), ("FA-1-1", STRIPS, PASS_1)])
    assert (run / "norm.py").read_text(encoding="utf-8") == FIX_AUDIT
    assert _left_clean(run)


def test_an_unusable_snapshot_is_refused_before_any_file_is_reverted(run, monkeypatch):
    """Invariant: every named snapshot is checked before the first revert.

    Failure mode: snapshots were checked as their turn came, so proofs against a good one
    ran (and its files were reverted) before another was refused as left in progress.
    Red: tests/test_fixaudit_proofs.py::test_an_unusable_snapshot_is_refused_before_any_file_is_reverted
    against the mutation that checks each manifest inside the loop of `verify` fails
    `assert ran == []` with ["test_norm.py::test_lowercases"].
    """
    (PASS_1 / tc.IN_PROGRESS).write_text("left by an interrupted verify\n", encoding="utf-8")
    ran: list[str] = []
    monkeypatch.setattr(tc, "_run_one", lambda nodeid, extra: (ran.append(nodeid), (tc.RED, ""))[1])
    with pytest.raises(SystemExit, match="did not restore"):
        tc.verify([("FIX-1", LOWERS), ("FA-1-1", STRIPS, PASS_1)])
    assert ran == []
