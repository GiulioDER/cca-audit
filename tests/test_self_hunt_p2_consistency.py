"""P2 duplication and performance fixes from the CCA self hunt, run 20261120T201246Z.

CODE-001 and PERF-001 are behaviour tests, proven red against the snapshot taken before
these fixes. CODE-004 adds no code: its test is a drift guard over rules that agree today,
so it passes on the pre-fix code by design; its red proof is a mutation (one auditor type
removed from `L1_AUDITOR_TYPES` fails it).
"""
from __future__ import annotations

import re
from pathlib import Path

from bugs_testkit import commit, make_repo

from cca_checks import pipeline_state as ps
from cca_checks.bugs import anchors, query_anchors
from cca_checks.plugin import installer

ROOT = Path(__file__).resolve().parents[1]
SPEC = ROOT / "cca_checks" / "plugin" / "commands" / "audit-fix.md"


def test_the_launch_guard_and_the_run_summary_read_the_same_brief_lines():
    """CODE-001. Invariant: the record ids the launch guard requires in every auditor prompt
    are the ids the run summary lists. Failure mode: two regexes; the summary's needed a
    word-only match label, so a `[symbol-changed]` line was required at launch and missing
    from the summary. Red: [] != ['p:1:BUG-1'] on the pre-fix pipeline_state."""
    block = "BUG p:1:BUG-1 [symbol-changed] a.py f\nLESSON p:lesson:1 [exact] b.py g\n"
    summary_ids = [record_id for _kind, record_id, _match in ps._BRIEF_LINE.findall(block)]
    assert summary_ids == ps.core.brief_ids(block) == ["p:1:BUG-1", "p:lesson:1"]
    assert (ps.LOCAL_PRIOR, ps.PRIOR_RECORDS) == ps.core.BRIEF_FILES


def _spec_l1_types() -> set[str]:
    step1 = SPEC.read_text(encoding="utf-8").split("## Step 1:", 1)[1].split("## Step 2:", 1)[0]
    table = re.findall(r"^\| \d+ \| [^|]+\| `([a-z-]+)` \|", step1, re.MULTILINE)
    domain = re.findall(r"^subagent_type: ([a-z-]+)", step1, re.MULTILINE)
    return set(table) | set(domain)


def test_every_l1_auditor_the_spec_dispatches_is_known_everywhere():
    """CODE-004 drift guard. Invariant: every L1 auditor type audit-fix.md dispatches is in
    the launch guard's roster (else the guard lets it launch without the brief), and in the
    installer's and both shell installers' name-collision checks."""
    types = _spec_l1_types()
    assert len(types) >= 9, types  # the parse found the table and the domain blocks
    assert types <= ps.core.L1_AUDITOR_TYPES, types - ps.core.L1_AUDITOR_TYPES
    scripts = [(ROOT / "claude-code" / name).read_text(encoding="utf-8")
               for name in ("install.sh", "install.ps1")]
    for name in types:
        assert installer._DISPATCHED_NAMES.search(f"name: {name}\n"), name
        assert f"cca-{name}.md" in installer._REQUIRED_CODEX_AGENT_FILES, name
        stem = name.removesuffix("-auditor")
        assert all(stem in script for script in scripts), name


def test_anchoring_a_diff_parses_each_changed_file_once(tmp_path, monkeypatch):
    """PERF-001. Invariant: `bugs anchors --diff` parses a changed file once, however many of
    its lines changed. Failure mode: two ast.parse calls per changed line (41.6 s on the
    diff of PR 127). Red: 120 parses for 60 changed lines on the pre-fix code."""
    repo = make_repo(tmp_path / "repo")
    body = "".join(f"def perf_001_f{i}():\n    return {i}\n\n" for i in range(60))
    commit(repo, "m.py", body, "base")
    (repo / "m.py").write_text(body.replace("return ", "return 1 + "), encoding="utf-8",
                               newline="\n")
    calls = []
    real = anchors.ast.parse
    monkeypatch.setattr(anchors.ast, "parse", lambda *a, **k: calls.append(1) or real(*a, **k))
    query_anchors.for_diff(repo, "HEAD", None)
    assert len(calls) <= 2, len(calls)
