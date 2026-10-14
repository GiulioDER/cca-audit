"""verify --record keeps each proof's verdict in runs/<run_id>/L5.6.json."""
import json

from conftest import findings_output, plan_output, verdicts_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.plugin.tools import cca_tautology_check as tc


def _through_l5(repo):
    state = ps.start("STANDARD", root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 0}, output=findings_output(repo), root=repo)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo), root=repo)
    ps.record("L2.6", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 0}, output=plan_output(repo), root=repo)
    (repo / "app.py").write_text("x = 2\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(repo), root=repo)
    ps.record("L5", root=repo)
    return state


def test_verify_record_writes_each_proofs_verdict(git_repo, monkeypatch):
    """Invariant: after `verify --record`, runs/<run_id>/L5.6.json names every proof's verdict.

    Red 2026-10-13: mutation deleting the `_save_proofs(results)` call in `main`; failed at
    `assert path.exists()`.
    """
    state = _through_l5(git_repo)
    results = [tc.ProofResult("FIX-001", "tests/t.py::a", tc.RED, ""),
               tc.ProofResult("FIX-002", "tests/t.py::b", tc.TAUTOLOGICAL, "")]
    monkeypatch.setattr(tc, "verify", lambda proofs, snapdir, extra=None: results)
    monkeypatch.chdir(git_repo)
    tc.main(["verify", "--proof", "FIX-001=tests/t.py::a", "--proof", "FIX-002=tests/t.py::b",
             "--record"])
    path = git_repo / ".claude" / "audits" / "runs" / state.run_id / "L5.6.json"
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"finding": "FIX-001", "nodeid": "tests/t.py::a", "verdict": "RED"},
        {"finding": "FIX-002", "nodeid": "tests/t.py::b", "verdict": "TAUTOLOGICAL"},
    ]


def test_the_file_does_not_make_l5_6_stale(git_repo, monkeypatch):
    """Invariant: the file lives under .claude/audits/, which the fingerprint ignores.

    Red 2026-10-13: mutation writing the file to `<root>/L5.6.json` instead; failed at
    `assert not ps.outstanding(...)` (L5.6 recorded before the latest edit).
    """
    _through_l5(git_repo)
    monkeypatch.setattr(tc, "verify", lambda proofs, snapdir, extra=None:
                        [tc.ProofResult("FIX-001", "t::a", tc.RED, "")])
    monkeypatch.chdir(git_repo)
    assert tc.main(["verify", "--proof", "FIX-001=t::a", "--record"]) == 0
    ps.record("L5.5", root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    assert not ps.outstanding(ps.load(git_repo), git_repo)


def test_a_second_record_keeps_the_verdicts_it_did_not_rerun(git_repo, monkeypatch):
    """BUG-005. Invariant: a `verify --record` that re-runs only some proofs keeps the
    earlier verdicts of the others and replaces those it re-ran.

    Red 2026-10-13: pre-fix `_save_proofs` overwriting the file; failed at the final
    equality assert (FIX-001's RED verdict was gone).
    """
    state = _through_l5(git_repo)
    monkeypatch.chdir(git_repo)
    first = [tc.ProofResult("FIX-001", "t::a", tc.RED, ""),
             tc.ProofResult("FIX-002", "t::b", tc.RED, "")]
    monkeypatch.setattr(tc, "verify", lambda proofs, snapdir, extra=None: first)
    tc.main(["verify", "--proof", "FIX-001=t::a", "--proof", "FIX-002=t::b", "--record"])
    again = [tc.ProofResult("FIX-002", "t::b2", tc.TAUTOLOGICAL, "")]
    monkeypatch.setattr(tc, "verify", lambda proofs, snapdir, extra=None: again)
    tc.main(["verify", "--proof", "FIX-002=t::b2", "--record"])
    path = git_repo / ".claude" / "audits" / "runs" / state.run_id / "L5.6.json"
    assert json.loads(path.read_text(encoding="utf-8")) == [
        {"finding": "FIX-001", "nodeid": "t::a", "verdict": "RED"},
        {"finding": "FIX-002", "nodeid": "t::b2", "verdict": "TAUTOLOGICAL"},
    ]
