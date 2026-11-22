"""Red proofs for fixes made during the fix audit (L5.4), Task 7 of the fix audit loop.

A bug a Layer 4 fix introduced did not exist before Layer 4, so its test can only be red
against the tree the fix audit pass audited. `--proof FINDING=NODEID@SNAPDIR` names that
pass's snapshot. These drive the real tool end to end: each proof runs a real pytest
against reverted files, because a mocked run would not show which tree it judged.
"""
from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from cca_checks import pipeline_state as ps
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


def _after_one_pass(root: Path) -> Path:
    """A tree after one fix audit pass: both snapshots taken, the fix audit fix applied."""
    norm = root / "norm.py"
    norm.write_text(PRE_L4, encoding="utf-8")
    (root / "test_norm.py").write_text(TESTS, encoding="utf-8")
    tc.cmd_snapshot([Path("norm.py")], tc.DEFAULT_SNAPDIR)   # Layer 4 start
    norm.write_text(L4, encoding="utf-8")
    tc.cmd_snapshot([Path("norm.py")], PASS_1)               # before the fix audit edit
    norm.write_text(FIX_AUDIT, encoding="utf-8")
    return root


@pytest.fixture
def run(git_repo, monkeypatch):
    monkeypatch.chdir(git_repo)
    return _after_one_pass(git_repo)


def _record_runs(monkeypatch) -> list[str]:
    """Stub every proof run as RED and return the node ids, in the order they ran."""
    ran: list[str] = []

    def run_one(nodeid, extra):
        ran.append(nodeid)
        return tc.RED, ""

    monkeypatch.setattr(tc, "_run_one", run_one)
    return ran


def _restore_writes_junk(monkeypatch, snapdir: Path) -> None:
    """Make every restore out of `snapdir`'s fixed/ land JUNK, so its hash check fails."""
    fixed = (snapdir / "fixed" / "norm.py").resolve()
    real = shutil.copy2

    def copy2(src, dst, *a, **k):
        if Path(src).resolve() == fixed:
            Path(dst).write_text("JUNK\n", encoding="utf-8")
            return dst
        return real(src, dst, *a, **k)

    monkeypatch.setattr(tc.shutil, "copy2", copy2)


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
    ran (and its files were reverted) before another was refused as missing.
    Red: tests/test_fixaudit_proofs.py::test_an_unusable_snapshot_is_refused_before_any_file_is_reverted
    against the mutation that checks each manifest inside the loop of `verify` fails
    `assert ran == []` with ["test_norm.py::test_lowercases"]. Fix round 1 made the snapshot
    unusable by a missing manifest instead of a stranded sentinel, because the directory scan
    now catches a sentinel before the loop and would hide that mutation.
    """
    (PASS_1 / tc.MANIFEST).unlink()
    ran = _record_runs(monkeypatch)
    with pytest.raises(SystemExit, match="no snapshot"):
        tc.verify([("FIX-1", LOWERS), ("FA-1-1", STRIPS, PASS_1)])
    assert ran == []


def test_a_failed_restore_never_lets_a_second_spelling_overwrite_the_fix(run, monkeypatch):
    """Invariant: after a restore fails its hash check, fixed/ and the sentinel survive.

    Failure mode (the review probe of fix round 1): the default snapshot and the same
    directory spelled absolute were two groups; the first restore failed, the second group
    stashed the bad tree over fixed/, restored it, passed its hash check and deleted the
    sentinel, so the fix and the recovery marker were gone and the gate exited 0.
    Red: tests/test_fixaudit_proofs.py::test_a_failed_restore_never_lets_a_second_spelling_overwrite_the_fix
    against ce30128 fails the fixed/norm.py assertion with "JUNK".
    """
    ran = _record_runs(monkeypatch)
    _restore_writes_junk(monkeypatch, tc.DEFAULT_SNAPDIR)
    absolute = (run / tc.DEFAULT_SNAPDIR).resolve()
    refused = None
    try:
        tc.verify([("A", LOWERS), ("B", STRIPS, absolute)])
    except SystemExit as exc:
        refused = str(exc)
    fixed = tc.DEFAULT_SNAPDIR / "fixed" / "norm.py"
    assert fixed.read_text(encoding="utf-8") == FIX_AUDIT
    assert (tc.DEFAULT_SNAPDIR / tc.IN_PROGRESS).exists()
    assert refused is not None and "RESTORE FAILED" in refused
    assert ran == [LOWERS, STRIPS]   # one cycle ran both, then stopped


def test_a_failed_restore_stops_verify_before_the_next_snapshot(run, monkeypatch):
    """Invariant: after a failed restore, no later snapshot's cycle runs (ruling 1).

    Failure mode: the next snapshot stashed the tree the failed restore left as its fix and
    judged proofs against it.
    Red: tests/test_fixaudit_proofs.py::test_a_failed_restore_stops_verify_before_the_next_snapshot
    against the mutation `raise SystemExit(` to `print(` in the failed restore branch of
    `_verify_against` fails `assert ran == [LOWERS]` with STRIPS also run.
    """
    ran = _record_runs(monkeypatch)
    _restore_writes_junk(monkeypatch, tc.DEFAULT_SNAPDIR)
    refused = None
    try:
        tc.verify([("FIX-1", LOWERS), ("FA-1-1", STRIPS, PASS_1)])
    except SystemExit as exc:
        refused = str(exc)
    assert ran == [LOWERS]
    assert refused is not None and "cp -r" in refused


def test_one_snapshot_under_two_spellings_is_reverted_once(run, monkeypatch):
    """Invariant: proofs naming one directory in two spellings share one cycle (ruling 2).

    Failure mode: each spelling got its own stash, revert and restore of the same files.
    Red: tests/test_fixaudit_proofs.py::test_one_snapshot_under_two_spellings_is_reverted_once
    against the mutation `groups.setdefault(where, ...)` (key not resolved) in `verify` fails
    `assert reverts == 1` with 2.
    """
    ran = _record_runs(monkeypatch)
    prefix = (tc.DEFAULT_SNAPDIR / "prefix").resolve()
    real = shutil.copy2
    reverts = 0

    def copy2(src, dst, *a, **k):
        nonlocal reverts
        reverts += Path(src).resolve().is_relative_to(prefix)
        return real(src, dst, *a, **k)

    monkeypatch.setattr(tc.shutil, "copy2", copy2)
    results = tc.verify([("A", LOWERS), ("B", STRIPS, (run / tc.DEFAULT_SNAPDIR).resolve())])
    assert reverts == 1
    assert [r.finding for r in results] == ["A", "B"] and ran == [LOWERS, STRIPS]
    assert _left_clean(run)


def test_a_stranded_sentinel_in_another_snapshot_blocks_verify(run, monkeypatch):
    """Invariant: a sentinel in any snapshot under .claude/audits stops verify before a revert.

    Failure mode: a killed verify of pass 1 left norm.py at the L4 copy and the fix only in
    pass-1/fixed; a verify naming only the default snapshot stashed that tree as the fix.
    Red: tests/test_fixaudit_proofs.py::test_a_stranded_sentinel_in_another_snapshot_blocks_verify
    against the mutation `for sentinel in []:` (the scan removed) in `verify` fails
    `assert ran == []` with [LOWERS].
    """
    (PASS_1 / tc.IN_PROGRESS).write_text("left by an interrupted verify\n", encoding="utf-8")
    ran = _record_runs(monkeypatch)
    refused = None
    try:
        tc.verify([("FIX-1", LOWERS)])
    except SystemExit as exc:
        refused = str(exc)
    assert ran == []
    assert refused is not None and "pass-1" in refused


def test_the_proof_file_names_each_proofs_snapshot(git_repo, monkeypatch):
    """Invariant: runs/<run_id>/L5.6.json says which snapshot each proof was red against.

    Failure mode: a fix audit proof and an original one were stored alike, so nothing after
    the run could tell which tree a RED was taken on.
    Red: tests/test_fixaudit_proofs.py::test_the_proof_file_names_each_proofs_snapshot against
    the mutation deleting `row["snapdir"] = r.snapdir` in `_save_proofs` fails the rows
    comparison with no snapdir keys.
    """
    monkeypatch.chdir(git_repo)
    state = ps.start("STANDARD", root=git_repo)   # before the snapshots, as Layer 4 takes them
    _after_one_pass(git_repo)
    _record_runs(monkeypatch)
    tc._save_proofs(tc.verify([("FIX-1", LOWERS), ("FA-1-1", STRIPS, PASS_1)]))
    path = git_repo / ".claude" / "audits" / "runs" / state.run_id / "L5.6.json"
    rows = json.loads(path.read_text(encoding="utf-8"))
    assert [(r["finding"], r.get("snapdir")) for r in rows] == [
        ("FIX-1", tc.DEFAULT_SNAPDIR.as_posix()), ("FA-1-1", PASS_1.as_posix())]


def test_a_proof_with_more_than_three_elements_is_rejected(run, monkeypatch):
    """Invariant: verify refuses a proof tuple longer than three, before any revert.

    Failure mode: extra elements were ignored, so a caller's misplaced argument was dropped.
    Red: tests/test_fixaudit_proofs.py::test_a_proof_with_more_than_three_elements_is_rejected
    against the mutation deleting the `if len(own) > 1:` check in `verify` fails
    `assert ran == []` with [STRIPS].
    """
    ran = _record_runs(monkeypatch)
    rejected = False
    try:
        tc.verify([("FA-1-1", STRIPS, PASS_1, "extra")])
    except ValueError:
        rejected = True
    assert ran == []
    assert rejected


def _strand(monkeypatch, snapdir: Path) -> None:
    """Leave `snapdir` as a failed restore leaves it: sentinel kept, the fix only in fixed/."""
    _record_runs(monkeypatch)
    _restore_writes_junk(monkeypatch, snapdir)
    with pytest.raises(SystemExit, match="RESTORE FAILED"):
        tc.verify([("A", LOWERS, snapdir)])


def test_a_snapshot_over_a_stranded_verify_is_refused(run, monkeypatch):
    """Invariant: `snapshot` never deletes a snapshot whose verify left it stranded.

    Failure mode (fix round 2 review): after a failed restore, retaking the snapshot ran
    rmtree on it, deleting fixed/ (the only copy of the fix) and the sentinel. The directory
    sits outside .claude/audits, so only the check of the target itself can catch it.
    Red: tests/test_fixaudit_proofs.py::test_a_snapshot_over_a_stranded_verify_is_refused
    against 0ae3535, and against the mutation deleting `_refuse_if_stranded(snapdir)` at the
    top of `cmd_snapshot`, fails the fixed/norm.py assertion (the file is gone).
    """
    custom = Path("snaps/custom")
    tc.cmd_snapshot([Path("norm.py")], custom)
    _strand(monkeypatch, custom)
    refused = None
    try:
        tc.cmd_snapshot([Path("norm.py")], custom)
    except SystemExit as exc:
        refused = str(exc)
    fixed = custom / "fixed" / "norm.py"
    assert fixed.is_file() and fixed.read_text(encoding="utf-8") == FIX_AUDIT
    assert (custom / tc.IN_PROGRESS).exists()
    assert refused is not None and "did not restore" in refused


def test_a_snapshot_is_refused_while_another_snapshot_is_stranded(run, monkeypatch):
    """Invariant: no snapshot is taken while any snapshot under .claude/audits is stranded.

    Failure mode: a stranded pass 1 left norm.py reverted (or junk); a pass 2 snapshot taken
    then recorded that tree as the pre-fix content the next proofs are judged against.
    Red: tests/test_fixaudit_proofs.py::test_a_snapshot_is_refused_while_another_snapshot_is_stranded
    against 0ae3535, and against the mutation deleting `_refuse_if_any_stranded()` in
    `cmd_snapshot`, fails `assert not (pass_2 / tc.MANIFEST).exists()`.
    """
    _strand(monkeypatch, PASS_1)
    pass_2 = PASS_1.parent / "pass-2"
    refused = None
    try:
        tc.cmd_snapshot([Path("norm.py")], pass_2)
    except SystemExit as exc:
        refused = str(exc)
    assert not (pass_2 / tc.MANIFEST).exists()
    assert refused is not None and "pass-1" in refused


def test_the_restore_failure_names_the_exception_that_stopped_the_proofs(run, monkeypatch):
    """Invariant: a restore failure raised during another exception names that exception.

    Failure mode: the SystemExit replaced the exception that stopped the proofs, and a CLI
    user saw only the restore failure, never why the proofs stopped.
    Red: tests/test_fixaudit_proofs.py::test_the_restore_failure_names_the_exception_that_stopped_the_proofs
    against 0ae3535, and against the mutation deleting `in_flight = exc` in
    `_verify_against`, fails the "RuntimeError('boom')" assertion.
    """
    _restore_writes_junk(monkeypatch, tc.DEFAULT_SNAPDIR)

    def run_one(nodeid, extra):
        raise RuntimeError("boom")

    monkeypatch.setattr(tc, "_run_one", run_one)
    refused = None
    try:
        tc.verify([("A", LOWERS)])
    except SystemExit as exc:
        refused = str(exc)
    assert refused is not None and "RuntimeError('boom')" in refused


def test_a_restore_copy_that_raises_ends_in_the_recovery_message(run, monkeypatch):
    """Invariant: a restore copy that raises OSError surfaces as the recovery SystemExit.

    Failure mode: a locked file on Windows made the restore copy raise PermissionError, which
    escaped bare, without the instruction that names fixed/ as the only copy of the fix.
    Red: tests/test_fixaudit_proofs.py::test_a_restore_copy_that_raises_ends_in_the_recovery_message
    against 1bf1a49, and against the mutation `except OSError as exc:` to
    `except FileNotFoundError as exc:` in `_verify_against`, fails
    `isinstance(raised, SystemExit)` with a PermissionError; against the mutation deleting
    `failure.__cause__ = copy_error` it fails the `__cause__` assertion.
    """
    _record_runs(monkeypatch)
    fixed = (tc.DEFAULT_SNAPDIR / "fixed" / "norm.py").resolve()
    real = shutil.copy2

    def copy2(src, dst, *a, **k):
        if Path(src).resolve() == fixed:
            raise PermissionError("norm.py is locked")
        return real(src, dst, *a, **k)

    monkeypatch.setattr(tc.shutil, "copy2", copy2)
    raised = None
    try:
        tc.verify([("A", LOWERS)])
    except BaseException as exc:  # noqa: BLE001 (which exception escapes is the observation)
        raised = exc
    assert isinstance(raised, SystemExit) and "RESTORE FAILED" in str(raised)
    assert isinstance(raised.__cause__, PermissionError)
    assert (tc.DEFAULT_SNAPDIR / tc.IN_PROGRESS).exists()
    assert fixed.read_text(encoding="utf-8") == FIX_AUDIT
