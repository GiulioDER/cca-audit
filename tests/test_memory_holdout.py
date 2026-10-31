"""The memory holdout withholds the CCA prior-bugs brief from about one DIFF run in three.

Design: docs/superpowers/specs/2026-10-28-auditor-memory-attribution-design.md, section
"Revision 2026-10-31". The brief commands are driven through `bugs` main with a real open run
(`pipeline start --run-id`), so the mode is read from the run state as in a real run. The local
reader is an operator stub whose block is the environment variable STUB_BLOCK.

Red proof, 2026-10-31. Baseline = this branch at 0f0b62e with `cca_checks/bugs/holdout.py`
present but nothing calling it (so no ImportError); every test ran, was collected and failed in
the assertion named:

- test_a_withheld_local_brief_renders_as_empty: baseline; failed at `assert out == empty` (the
  BUG and LESSON lines were printed).
- test_a_brief_arm_run_is_unchanged_and_recorded: baseline; failed at `assert _arm(run) == ...`
  (None, no arm file).
- test_a_withheld_hosted_brief_renders_as_empty: baseline; failed at `assert out == empty` (the
  records were rendered).
- test_hunt_is_never_withheld: baseline; failed at `assert _arm(run) == ...` (None).
- test_an_unreadable_mode_is_not_withheld_and_says_why: baseline; failed at `assert _arm(run)
  == ...` (None).
- test_summary_records_the_arm: baseline; failed at `assert summary["memory_attribution"] is
  None` (the hosted ids were listed, so the citation was counted).

Mutations on the green implementation, one per test, restored afterwards; each failed in the
assertion named:

- test_assignment_is_the_sha256_rule: in `holdout.withheld`, `% DENOM == 0` became
  `% DENOM == 1`; failed at `assert [holdout.withheld(i) for i in ids] == expected`.
- test_inactive_withholds_nothing: in `holdout.withheld`, the `if not ACTIVE: return False`
  guard removed; failed at `assert holdout.withheld(WITHHELD) is False`.
- test_the_dispatch_guard_accepts_a_holdout_prompt: in `bugs.__main__._brief`, the holdout
  wrote the receipt from the real `local_brief.build` result and printed the empty one; failed at
  `assert core.dispatch_reasons(...) == []` (the receipt's ids are not in the prompt).
- test_summary_records_the_arm: in `pipeline_state._write_summary`, `"memory_arm":
  memory_arm(state, root)` became `"memory_arm": None`; failed at
  `assert summary["memory_arm"] == "holdout"`.
- test_hunt_is_never_withheld: in `holdout.assign`, `mode == "DIFF" and` removed; failed at
  `assert _arm(run) == ...` (arm `holdout`, not `brief`).
- test_an_unreadable_mode_is_not_withheld_and_says_why: in `holdout._mode_reason`, the `state is
  None` branch returned ("DIFF", None); failed at `assert _arm(run) == ...` (arm `holdout` and no
  `reason`).
- test_a_completed_run_gets_no_arm: in `holdout.assign`, `or (run / "summary.json").exists()`
  removed; failed at `assert _arm(run) is None` (an arm `brief` with a reason was written).
- test_summary_records_the_arm, second mutation: in `pipeline_state._saved_brief_answer`, the
  holdout branch made never true; failed at `assert summary["prior_block"] == {...zeros}`
  (3 / 0 / 1). The explicit holdout check at the top of `memory_attribution` is a second guard:
  removing it alone leaves the test green, because the hosted ids are already empty; removing
  both is the baseline above, which fails at the `memory_attribution` assertion.

Final review fix I6, 2026-10-31: a `bugs brief` given no run directory (no `--out`, or one
outside every run) honours the arm of the DIFF run open at `--root`, else at the working
directory's project root. Baseline = this branch at 720e740 (arm decided from `--out` only);
each test ran, was collected and failed in the assertion named:

- test_a_brief_without_out_honours_an_open_holdout_run: baseline; failed at `assert out ==
  empty` (the BUG and LESSON lines were printed).
- test_a_brief_with_out_outside_the_run_honours_its_holdout: baseline; same assertion.
- test_a_brief_without_root_finds_the_run_at_the_project_root: baseline; same assertion.
- test_a_brief_arm_run_without_out_gets_the_normal_brief: baseline; failed at `assert _arm(run)
  == ...` (None, no arm file).

Mutations on the green implementation (`cca_checks/bugs/__main__.py` `_brief` and
`cca_checks/bugs/holdout.py` `open_run`), one per test, restored afterwards:

- test_no_open_run_prints_the_normal_brief: in `_brief`, `withhold = run is not None and ...`
  became `withhold = run is None or ...`; failed at `assert out == normal`.
- test_an_open_hunt_run_without_out_keeps_todays_behaviour: in `open_run`, the
  `mode.upper() != "DIFF"` test removed; failed at `assert _arm(run) is None` (an arm `brief`
  was written for the HUNT run).
- test_a_brief_arm_run_without_out_gets_the_normal_brief, second mutation: in `_brief`, the
  open run withheld whatever its arm, after recording it (`withhold = run is not None and
  (holdout.assign(...) or True)`); failed at `assert out == normal and ...`.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

from conftest import plan_output, write_output

from cca_checks import pipeline_state as ps
from cca_checks.bugs import holdout, prior
from cca_checks.bugs.__main__ import main

RESPONSES = Path(__file__).parent / "fixtures" / "audit_memory_responses"
REPO = "GiulioDER/RE-call"
BUG_ID = "RE-call:abcdef012345:BUG-002"
MARKER = "<!-- recall:auditor-brief -->"
BLOCK = (f"{MARKER}\n```text\nBUG RE-call:aaa:BUG-001 [exact] app.py run: a race in run\n"
         "LESSON RE-call:lesson:ccc [file_only] app.py (module): parse once\n```")


def _rule(run_id: str) -> bool:
    return int(hashlib.sha256(run_id.encode("utf-8")).hexdigest(), 16) % 3 == 0


_CANDIDATES = [f"20261031T{n:06d}Z" for n in range(100)]
WITHHELD = next(i for i in _CANDIDATES if _rule(i))
KEPT = next(i for i in _CANDIDATES if not _rule(i))


def _reader(tmp_path, monkeypatch, block: str) -> None:
    package = tmp_path / "operator" / "recall_hooks"
    package.mkdir(parents=True, exist_ok=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "auditor_brief.py").write_text(
        "import os\n"
        "def build_block(cwd, prompt, *, deadline):\n"
        "    return os.environ.get('STUB_BLOCK', '')\n", encoding="utf-8")
    monkeypatch.setenv("CCA_RECALL_ROOT", str(tmp_path / "operator"))
    monkeypatch.setenv("STUB_BLOCK", block)


def _run(root: Path, run_id: str) -> Path:
    return root / ".claude" / "audits" / "runs" / run_id


def _arm(run: Path):
    path = run / holdout.ARM_FILE
    return json.loads(path.read_text(encoding="utf-8")) if path.is_file() else None


def _brief(root: Path, out: Path, capsys) -> str:
    capsys.readouterr()
    assert main(["brief", "--root", str(root), "--out", str(out)]) == 0
    return capsys.readouterr().out


def _plain(tmp_path, monkeypatch, capsys, block: str) -> tuple[str, dict]:
    """What `bugs brief` prints and saves outside any run, with the reader returning `block`."""
    monkeypatch.setenv("STUB_BLOCK", block)
    out = tmp_path / "plain" / "local-prior.json"
    text = _brief(tmp_path, out, capsys)
    return text, json.loads(out.read_text(encoding="utf-8"))


def test_assignment_is_the_sha256_rule():
    """Invariant: a run's arm is `int(sha256(run_id), 16) % 3 == 0`, recomputable by anyone."""
    ids = [f"run-{n:02d}" for n in range(30)]
    expected = [_rule(i) for i in ids]
    assert [holdout.withheld(i) for i in ids] == expected
    assert 5 <= sum(expected) <= 15
    assert holdout.RULE == "int(sha256(run_id), 16) % 3 == 0" and holdout.DENOM == 3


def test_a_withheld_local_brief_renders_as_empty(git_repo, tmp_path, monkeypatch, capsys):
    """Invariant: a withheld DIFF run's `bugs brief` prints, and saves, exactly what it does when
    the ledger has no record for the files, and records arm `holdout`."""
    _reader(tmp_path, monkeypatch, "")
    empty, empty_receipt = _plain(tmp_path, monkeypatch, capsys, "")
    monkeypatch.setenv("STUB_BLOCK", BLOCK)
    ps.start("STANDARD", run_id=WITHHELD, root=git_repo)
    run = _run(git_repo, WITHHELD)
    out = _brief(git_repo, run / ps.LOCAL_PRIOR, capsys)
    assert out == empty
    receipt = json.loads((run / ps.LOCAL_PRIOR).read_text(encoding="utf-8"))
    assert receipt == empty_receipt
    assert not re.search(r"^(BUG|LESSON) ", receipt["block"], re.MULTILINE)
    assert _arm(run) == {"run_id": WITHHELD, "arm": "holdout", "rule": holdout.RULE}


def test_a_brief_arm_run_is_unchanged_and_recorded(git_repo, tmp_path, monkeypatch, capsys):
    """Invariant: a DIFF run outside the holdout gets the normal brief and records arm `brief`."""
    _reader(tmp_path, monkeypatch, BLOCK)
    normal, _ = _plain(tmp_path, monkeypatch, capsys, BLOCK)
    ps.start("STANDARD", run_id=KEPT, root=git_repo)
    run = _run(git_repo, KEPT)
    out = _brief(git_repo, run / ps.LOCAL_PRIOR, capsys)
    assert _arm(run) == {"run_id": KEPT, "arm": "brief", "rule": holdout.RULE}
    assert out == normal and "BUG RE-call:aaa:BUG-001" in out


def test_a_withheld_hosted_brief_renders_as_empty(git_repo, tmp_path, capsys):
    """Invariant: a withheld run's `bugs prior --records` prints the PRIOR BUGS block of an
    answer with no records, and no LESSONS block."""
    answer = json.loads((RESPONSES / "bug_answer.json").read_text(encoding="utf-8"))
    nothing = tmp_path / "nothing.json"
    nothing.write_text(json.dumps({**answer, "records": []}), encoding="utf-8")
    assert main(["prior", "--records", str(nothing), "--repo-name", REPO]) == 0
    empty = capsys.readouterr().out
    ps.start("STANDARD", run_id=WITHHELD, root=git_repo)
    run = _run(git_repo, WITHHELD)
    (run / ps.PRIOR_RECORDS).write_bytes((RESPONSES / "bug_answer.json").read_bytes())
    assert main(["prior", "--records", str(run / ps.PRIOR_RECORDS), "--repo-name", REPO]) == 0
    out = capsys.readouterr().out
    assert out == empty
    assert prior.RECORDS_HEADING in out and prior.LESSONS_HEADING not in out
    assert _arm(run) == {"run_id": WITHHELD, "arm": "holdout", "rule": holdout.RULE}


def test_hunt_is_never_withheld(git_repo, tmp_path, monkeypatch, capsys):
    """Invariant: a HUNT run whose id the rule withholds still gets its brief, arm `brief`."""
    _reader(tmp_path, monkeypatch, BLOCK)
    ps.start("DEEP", mode="HUNT", run_id=WITHHELD, root=git_repo)
    run = _run(git_repo, WITHHELD)
    out = _brief(git_repo, run / ps.LOCAL_PRIOR, capsys)
    assert _arm(run) == {"run_id": WITHHELD, "arm": "brief", "rule": holdout.RULE}
    assert "BUG RE-call:aaa:BUG-001" in out


def test_an_unreadable_mode_is_not_withheld_and_says_why(git_repo, tmp_path, monkeypatch,
                                                         capsys):
    """Invariant: with no open run state the mode is unknown, so nothing is withheld and the
    arm file says why."""
    _reader(tmp_path, monkeypatch, BLOCK)
    run = _run(git_repo, WITHHELD)
    out = _brief(git_repo, run / ps.LOCAL_PRIOR, capsys)
    assert _arm(run) == {"run_id": WITHHELD, "arm": "brief", "rule": holdout.RULE,
                         "reason": "no open run state"}
    assert "BUG RE-call:aaa:BUG-001" in out


def test_the_dispatch_guard_accepts_a_holdout_prompt(git_repo, tmp_path, monkeypatch, capsys):
    """Invariant: an auditor prompt built from a holdout run's printed brief passes the L1
    dispatch guard, so the holdout never blocks a run."""
    from cca_checks.pipeline_state import core

    _reader(tmp_path, monkeypatch, BLOCK)
    ps.start("STANDARD", run_id=WITHHELD, root=git_repo)
    run = _run(git_repo, WITHHELD)
    printed = _brief(git_repo, run / ps.LOCAL_PRIOR, capsys)
    prompt = f"Audit app.py.\n{printed}"
    assert core.dispatch_reasons(git_repo, core.read_state(git_repo), "bug-auditor",
                                 prompt) == []


def test_summary_records_the_arm(git_repo, capsys):
    """Invariant: a holdout run closes with `memory_arm: holdout`, `memory_attribution: null`
    and block counts of what the auditors saw (nothing), even when a finding cites an id the
    saved answer holds."""
    subprocess.run(["git", "-C", str(git_repo), "remote", "add", "origin",
                    f"https://github.com/{REPO}.git"], check=True)
    ps.start("FAST", run_id=WITHHELD, root=git_repo)
    run = _run(git_repo, WITHHELD)
    (run / ps.PRIOR_RECORDS).write_bytes((RESPONSES / "bug_answer.json").read_bytes())
    assert main(["prior", "--records", str(run / ps.PRIOR_RECORDS), "--repo-name", REPO]) == 0
    capsys.readouterr()
    finding = {"id": "BUG-001", "auditor": "cca-bug-auditor", "category": "x", "priority": "P2",
               "file": "app.py", "line": 1, "claim": "c", "high_stakes": False,
               "memory_ids": [BUG_ID]}
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 1}, output=write_output(git_repo, [finding]),
              root=git_repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(git_repo, [{"id": "BUG-001", "verdict": "CONFIRMED"}]),
              root=git_repo)
    ps.record("L3", counts={"to_fix": 0, "deferred": 1},
              output=plan_output(git_repo, deferred=1, ids=["BUG-001"]), root=git_repo)
    ps.record("L6", detail="APPROVED", root=git_repo)
    ps.close(root=git_repo)
    summary = json.loads((run / "summary.json").read_text(encoding="utf-8"))
    assert summary["memory_attribution"] is None
    assert summary["memory_arm"] == "holdout"
    assert summary["prior_block"] == {"exact": 0, "symbol_changed": 0, "file_only": 0}
    assert summary["lessons_block"] == 0
    assert summary["prior_brief"] == {"source": "hosted", "ids": []}


def test_inactive_withholds_nothing(git_repo, tmp_path, monkeypatch, capsys):
    """Invariant: with the holdout switched off, a withheld id gets its brief and no arm."""
    monkeypatch.setattr(holdout, "ACTIVE", False)
    _reader(tmp_path, monkeypatch, BLOCK)
    ps.start("STANDARD", run_id=WITHHELD, root=git_repo)
    run = _run(git_repo, WITHHELD)
    out = _brief(git_repo, run / ps.LOCAL_PRIOR, capsys)
    assert holdout.withheld(WITHHELD) is False
    assert "BUG RE-call:aaa:BUG-001" in out
    assert _arm(run) is None


def test_a_completed_run_gets_no_arm(git_repo, tmp_path, monkeypatch, capsys):
    """Invariant: replaying a closed run's brief (its directory holds summary.json) writes no
    arm file and withholds nothing, so a run that predates the holdout never joins an arm."""
    _reader(tmp_path, monkeypatch, BLOCK)
    run = _run(git_repo, WITHHELD)
    run.mkdir(parents=True)
    (run / "summary.json").write_text("{}", encoding="utf-8")
    out = _brief(git_repo, run / ps.LOCAL_PRIOR, capsys)
    assert _arm(run) is None
    assert "BUG RE-call:aaa:BUG-001" in out


def _bare(argv: list[str], capsys) -> str:
    capsys.readouterr()
    assert main(["brief", *argv]) == 0
    return capsys.readouterr().out


def test_a_brief_without_out_honours_an_open_holdout_run(git_repo, tmp_path, monkeypatch,
                                                         capsys):
    """Invariant (I6): `bugs brief` with no `--out`, as the auditor-memory skill runs it, inside
    an open DIFF run the rule withholds prints the empty form and records arm `holdout`; the
    guard is satisfied by Step 1's receipt, so this print must not leak the records."""
    _reader(tmp_path, monkeypatch, "")
    empty, _ = _plain(tmp_path, monkeypatch, capsys, "")
    monkeypatch.setenv("STUB_BLOCK", BLOCK)
    ps.start("STANDARD", run_id=WITHHELD, root=git_repo)
    run = _run(git_repo, WITHHELD)
    out = _bare(["--root", str(git_repo)], capsys)
    assert out == empty
    assert _arm(run) == {"run_id": WITHHELD, "arm": "holdout", "rule": holdout.RULE}


def test_a_brief_with_out_outside_the_run_honours_its_holdout(git_repo, tmp_path, monkeypatch,
                                                              capsys):
    """Invariant (I6): an `--out` outside every run directory names no run, so the open run
    decides, and its holdout holds."""
    _reader(tmp_path, monkeypatch, "")
    empty, _ = _plain(tmp_path, monkeypatch, capsys, "")
    monkeypatch.setenv("STUB_BLOCK", BLOCK)
    ps.start("STANDARD", run_id=WITHHELD, root=git_repo)
    out = _bare(["--root", str(git_repo), "--out", str(tmp_path / "elsewhere" / "b.json")],
                capsys)
    assert out == empty
    assert _arm(_run(git_repo, WITHHELD))["arm"] == "holdout"


def test_a_brief_without_root_finds_the_run_at_the_project_root(git_repo, tmp_path,
                                                                monkeypatch, capsys):
    """Invariant (I6): with no `--root`, the open run is looked for at the working directory's
    project root (its Git top level), here from a subdirectory."""
    _reader(tmp_path, monkeypatch, "")
    empty, _ = _plain(tmp_path, monkeypatch, capsys, "")
    monkeypatch.setenv("STUB_BLOCK", BLOCK)
    ps.start("STANDARD", run_id=WITHHELD, root=git_repo)
    sub = git_repo / "sub"
    sub.mkdir()
    monkeypatch.chdir(sub)
    out = _bare([], capsys)
    assert out == empty
    assert _arm(_run(git_repo, WITHHELD))["arm"] == "holdout"


def test_a_brief_arm_run_without_out_gets_the_normal_brief(git_repo, tmp_path, monkeypatch,
                                                           capsys):
    """Invariant (I6): an open DIFF run outside the holdout gets the normal brief from a bare
    `bugs brief`, and its arm file says `brief` (the same file and rule as Step 1)."""
    _reader(tmp_path, monkeypatch, BLOCK)
    normal, _ = _plain(tmp_path, monkeypatch, capsys, BLOCK)
    ps.start("STANDARD", run_id=KEPT, root=git_repo)
    run = _run(git_repo, KEPT)
    out = _bare(["--root", str(git_repo)], capsys)
    assert _arm(run) == {"run_id": KEPT, "arm": "brief", "rule": holdout.RULE}
    assert out == normal and "BUG RE-call:aaa:BUG-001" in out


def test_no_open_run_prints_the_normal_brief(git_repo, tmp_path, monkeypatch, capsys):
    """Invariant (I6): with no open run, a bare `bugs brief` behaves as before: the normal
    brief, and no arm file anywhere."""
    _reader(tmp_path, monkeypatch, BLOCK)
    normal, _ = _plain(tmp_path, monkeypatch, capsys, BLOCK)
    out = _bare(["--root", str(git_repo)], capsys)
    assert out == normal and "BUG RE-call:aaa:BUG-001" in out
    assert not list(git_repo.rglob(holdout.ARM_FILE))


def test_an_open_hunt_run_without_out_keeps_todays_behaviour(git_repo, tmp_path, monkeypatch,
                                                             capsys):
    """Invariant (I6): only a DIFF run is looked up; an open HUNT run whose id the rule would
    withhold gets the normal brief and no arm is written for it here."""
    _reader(tmp_path, monkeypatch, BLOCK)
    ps.start("DEEP", mode="HUNT", run_id=WITHHELD, root=git_repo)
    run = _run(git_repo, WITHHELD)
    out = _bare(["--root", str(git_repo)], capsys)
    assert "BUG RE-call:aaa:BUG-001" in out
    assert _arm(run) is None
