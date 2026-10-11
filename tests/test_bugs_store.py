"""Tests for cca_checks.bugs.store and the bugs CLI."""
import json

from bugs_testkit import commit, make_repo, publish

from cca_checks.bugs.__main__ import main
from cca_checks.bugs.model import CONDITIONS, EVIDENCED, BugRow
from cca_checks.bugs.store import bugs_dir, existing_ids, write

MSG = "fix(store): 1 audit fix from CCA (FAST tier)\n\nP1:\n- BUG-001: a real defect. +test\n"


def _row(finding="BUG-001"):
    return BugRow(project="widgets", repo="acme/widgets", finding_id=finding,
                  fix_commit="ab" * 20, date="2026-09-01", files=["a.py"],
                  summary="a real defect. +test",
                  evidence={c: EVIDENCED for c in CONDITIONS}, sources=["git:ab"])


def test_bugs_dir_honours_the_environment(tmp_path, monkeypatch):
    """Red proof: bugs_dir ignores CCA_BUGS_DIR -> the home path comes back."""
    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path / "x"))
    assert bugs_dir() == tmp_path / "x"


def test_writing_twice_writes_nothing_the_second_time(tmp_path):
    """Red proof: write without the `bug_id in have` check -> the second call appends 2."""
    rows = [_row(), _row("BUG-002")]
    assert write(rows, tmp_path, "2026-10-11T00:00:00Z") == (2, 2)
    assert write(rows, tmp_path, "2026-10-11T00:00:00Z") == (0, 0)
    lines = (tmp_path / "bugs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["tier"] == "proved"
    assert existing_ids(tmp_path / "bugs.jsonl") == {r.bug_id for r in rows}
    assert len(list((tmp_path / "memos").glob("*.md"))) == 2


def test_an_existing_memo_is_never_overwritten(tmp_path):
    """Red proof: write the memo without the exists() check -> the hand note is replaced."""
    row = _row()
    memo = tmp_path / "memos" / "widgets__abababababab__BUG-001.md"
    memo.parent.mkdir(parents=True)
    memo.write_text("hand note\n", encoding="utf-8")
    assert write([row], tmp_path, "2026-10-11T00:00:00Z") == (1, 0)
    assert memo.read_text(encoding="utf-8") == "hand note\n"


def _published(tmp_path):
    repo = make_repo(tmp_path / "widgets")
    commit(repo, "a.py", "a\n", MSG)
    publish(repo)
    return repo


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    """Red proof: main calls write() whatever --write says -> the directory exists."""
    target = tmp_path / "ledger"
    monkeypatch.setenv("CCA_BUGS_DIR", str(target))
    assert main(["backfill", "--repo", str(_published(tmp_path)), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 1 and "written" not in out
    assert not target.exists()


def test_write_creates_a_missing_directory(tmp_path, monkeypatch, capsys):
    """Review Focus 4. Red proof: write() mkdir without parents=True -> FileNotFoundError in the call."""
    target = tmp_path / "does" / "not" / "exist"
    monkeypatch.setenv("CCA_BUGS_DIR", str(target))
    assert main(["backfill", "--repo", str(_published(tmp_path)), "--write", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"] == {"rows": 1, "memos": 1, "dir": str(target)}
    assert (target / "bugs.jsonl").exists()
    assert (target / "unparsed.txt").read_text(encoding="utf-8") == ""


def test_the_top_level_cli_forwards_bugs(tmp_path, monkeypatch, capsys):
    """Red proof: forward a.bugs_args[1:] instead of a.bugs_args -> argparse exits 2."""
    from cca_checks.__main__ import main as top

    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path / "ledger"))
    assert top(["bugs", "backfill", "--repo", str(_published(tmp_path)), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 1


def test_a_cli_binding_reaches_the_backfill(tmp_path, monkeypatch, capsys):
    """Red proof: main passes {} instead of dict(a.bind) -> the bug stays historical."""
    from bugs_testkit import run_git

    repo = _published(tmp_path)
    audits = tmp_path / "audits"
    audits.mkdir()
    rows = [{"ts": "2026-09-01T09:00:00Z", "run_id": "r1", "verdict": "CONFIRMED", "fix_id": "FIX-1"},
            {"run_id": "r1", "fix_id": "FIX-1", "result": "fixed", "architect_verdict": "APPROVED"}]
    (audits / "AUDITOR_SCORECARD.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    (audits / "redproof-r1.json").write_text(
        json.dumps([{"finding": "FIX-1", "verdict": "RED"}]), encoding="utf-8")
    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path / "ledger"))
    msg = "fix(store): 1 audit fix from CCA (FAST tier)\n\nP1:\n- FIX-1: a real defect. +test\n"
    run_git(repo, "commit", "-q", "--amend", "-m", msg)
    sha = run_git(repo, "rev-parse", "HEAD").strip()[:12]
    run_git(repo, "update-ref", "refs/remotes/origin/master", "HEAD")
    argv = ["backfill", "--repo", str(repo), "--json",
            "--ledger", f"{audits / 'AUDITOR_SCORECARD.jsonl'}=acme/widgets", "--bind", f"{sha}=r1"]
    assert main(argv) == 0
    assert json.loads(capsys.readouterr().out)["tiers"] == {"proved": 1}


def test_text_output_cannot_carry_terminal_escapes():
    """Security review. Red proof: _render without _printable -> the ESC byte reaches the terminal.

    Commit subjects come from repositories the author may not control; printed raw, an ANSI
    sequence in one can rewrite or hide what the terminal shows.
    """
    from cca_checks.bugs.__main__ import _render

    summary = {"rows": 0, "tiers": {}, "unparsed": 1, "by_repo": {}, "evidence": {},
               "unparsed_commits": ["acme/x 0123 fix: audit fixes from CCA \x1b[2J\x1b]0;pwned\x07"],
               "skipped": [], "ambiguous": [], "candidates": [], "unused_bindings": [],
               "unknown_ids": [], "reverted": [], "cross_repo": [], "ignored_proofs": [],
               "malformed_ledger_lines": 0}
    text = _render(summary)
    assert "\x1b" not in text and "\x07" not in text
    assert "fix: audit fixes from CCA" in text
