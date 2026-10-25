"""Adapter boundary tests. Red mutations and their results are in the proof receipt.

The shared RE-call reader already tests admission, anchors and retractions. These
tests protect delivery to CCA, including an operator reader independent of target
code and PYTHONPATH, and explicit unavailable versus empty states.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest
from bugs_testkit import commit, make_repo, publish
from bugs_testkit import isolated_git_environment as isolated_git_environment

from cca_checks.bugs import local_brief
from cca_checks.bugs.__main__ import main
from cca_checks.bugs.anchors import Anchor, code_sha
from cca_checks.bugs.model import BugRow
from cca_checks.bugs.records import LessonRow, render_memo


def _reader(tmp_path, monkeypatch, body):
    runtime = tmp_path / "operator"
    package = runtime / "recall_hooks"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text("", encoding="utf-8")
    support = '''from pathlib import Path
_root = ''
def scope(cwd, prompt, *, deadline):
    global _root
    _root = cwd
    return 'o/r', []
def memo_directory(): return Path('.')
def cache_path(directory): return Path('unused-cache')
def render(entries, **kwargs): return build_block(_root, '', deadline=0)
'''
    (package / "auditor_brief.py").write_text(support + body, encoding="utf-8")
    recall = runtime / "recall"
    recall.mkdir()
    (recall / "__init__.py").write_text("", encoding="utf-8")
    (recall / "records.py").write_text(
        "RECORD_KINDS = ('bug', 'lesson')\nMAX_QUERY_ANCHORS = 200\n", encoding="utf-8")
    (recall / "records_files.py").write_text(
        "def load_memo_records(*args, **kwargs): return []\n"
        "def match_memo_records(records, **kwargs): return kwargs['anchors']\n", encoding="utf-8")
    monkeypatch.setenv("CCA_RECALL_ROOT", str(runtime))
    return runtime


def test_cli_delivers_both_kinds_with_explicit_local_provenance(tmp_path, monkeypatch, capsys):
    _reader(tmp_path, monkeypatch, '''def build_block(cwd, prompt, *, deadline):
    return "<!-- recall:auditor-brief -->\\nBUG bug-123\\nLESSON accepted-456"
''')
    receipt = tmp_path / "run" / "local-prior.json"
    assert main(["brief", "--root", str(tmp_path), "--out", str(receipt)]) == 0
    output = capsys.readouterr().out
    assert "BUG bug-123" in output and "LESSON accepted-456" in output
    assert "Local admitted memo records; hosted trust was not evaluated." in output
    assert "LESSON accepted-456" in json.loads(receipt.read_text())["block"]


def test_audited_checkout_and_pythonpath_cannot_supply_reader(tmp_path, monkeypatch):
    target = tmp_path / "target"
    target.mkdir()
    poison = target / "recall_hooks"
    poison.mkdir()
    (poison / "__init__.py").write_text("raise RuntimeError('audited code ran')", encoding="utf-8")
    monkeypatch.chdir(target)
    monkeypatch.setenv("PYTHONPATH", str(target))
    _reader(tmp_path, monkeypatch, '''def build_block(cwd, prompt, *, deadline):
    return "<!-- recall:auditor-brief --> operator"
''')
    assert local_brief.build(target, ["a.py"])["status"] == "ok"


def test_scope_is_data_and_root_is_absolute(tmp_path, monkeypatch):
    _reader(tmp_path, monkeypatch, '''import json
def render(entries, **kwargs):
    return "<!-- recall:auditor-brief -->" + json.dumps([_root, entries])
''')
    file = "a file 'quoted'.py"
    result = local_brief.build(tmp_path, [file])
    root, anchors = json.loads(result["block"].split("-->", 1)[1])
    assert root == str(tmp_path.resolve())
    assert anchors[0]["file"] == file


def test_empty_and_unavailable_are_distinct(tmp_path, monkeypatch):
    _reader(tmp_path, monkeypatch, "def build_block(*args, **kwargs): return ''\n")
    assert "No matching local" in local_brief.render(local_brief.build(tmp_path, []))
    monkeypatch.setenv("CCA_RECALL_ROOT", str(tmp_path / "absent"))
    # An operator reader is required, even if another copy happens to be installed.
    result = local_brief.build(tmp_path, [])
    assert result["status"] == "unavailable"
    assert "does not establish that memory is empty" in local_brief.render(result)


def test_timeout_is_explicit_and_fail_open(tmp_path, monkeypatch):
    _reader(tmp_path, monkeypatch, "")
    def timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired("reader", 25)

    monkeypatch.setattr(subprocess, "run", timeout)
    result = local_brief.build(tmp_path, [])
    assert result["status"] == "unavailable"
    assert "Continue the audit from current evidence" in local_brief.render(result)


def test_malformed_reader_output_is_unavailable(tmp_path, monkeypatch):
    _reader(tmp_path, monkeypatch, "")
    def bad(*args, **kwargs):
        return subprocess.CompletedProcess([], 0, '{"status":"ok","block":"no marker"}', "")

    monkeypatch.setattr(subprocess, "run", bad)
    assert local_brief.build(tmp_path, ["a.py"])["status"] == "unavailable"


def test_unicode_block_is_delivered_without_json_expansion_loss(tmp_path, monkeypatch):
    _reader(tmp_path, monkeypatch, '''def build_block(*args, **kwargs):
    return "<!-- recall:auditor-brief -->" + "記憶" * 1000
''')
    result = local_brief.build(tmp_path, [])
    assert result["status"] == "ok"
    assert result["block"].endswith("記憶" * 1000)


def test_real_reader_preserves_spaced_unicode_scope_on_clean_checkout(tmp_path, monkeypatch):
    """Optional integration: real operator reader, synthetic Git and native memos, no DB/network."""
    runtime = Path.home() / ".claude/hooks"
    if not (runtime / "recall_hooks/auditor_brief.py").is_file():
        pytest.skip("operator's optional RE-call reader is not installed")
    monkeypatch.setenv("CCA_RECALL_ROOT", str(runtime))
    root = tmp_path / "target"
    make_repo(root, remote="https://github.com/o/r.git")
    file = "café space.py"
    source = "value = 1\n"
    commit(root, file, source, "fixture")
    publish(root)
    memos = tmp_path / "memos"
    memos.mkdir()
    bug = BugRow(project="demo", repo="o/r", finding_id="BUG-001", fix_commit="1" * 40,
                 date="2026-01-01", files=[file], summary="synthetic remembered defect",
                 anchors=[Anchor(file, None, code_sha(source))])
    members = [bug, replace(bug, finding_id="BUG-002"), replace(bug, finding_id="BUG-003")]
    lesson = LessonRow(project="demo", repo="o/r", date="2026-01-01",
                       rule="synthetic accepted check", pattern="synthetic pattern",
                       anchors=[Anchor(file, None, None)], members=[row.id for row in members],
                       accepted_by="owner", accepted_at="2026-01-01")
    rows = [(f"bug-{index}", row) for index, row in enumerate(members)] + [("lesson", lesson)]
    for name, row in rows:
        (memos / f"{name}.md").write_text(render_memo(row, modified="2026-01-01T00:00:00Z"),
                                          encoding="utf-8")
    monkeypatch.setenv("RECALL_AUDITOR_MEMOS", str(memos))
    result = local_brief.build(root, [file])
    assert result["status"] == "ok", result
    assert bug.id in result["block"] and lesson.id in result["block"]
    assert file in result["block"]
