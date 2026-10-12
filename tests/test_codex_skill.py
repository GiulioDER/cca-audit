"""Contract tests for the Codex native CCA skill."""

import json
import os
import re
from pathlib import Path

import pytest

from cca_checks.plugin import installer

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_SKILL = _REPO_ROOT / "cca_checks" / "plugin" / "codex" / "cca-audit"


def test_codex_skill_templates_are_packaged():
    files = dict(installer.iter_codex_skill())

    assert files["SKILL.md"] == (_SOURCE_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert files["agents/openai.yaml"] == (
        _SOURCE_SKILL / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")


def test_codex_skill_reuses_the_canonical_pipeline_and_agent_prompts():
    files = dict(installer.iter_codex_skill())

    assert files["references/pipeline.md"] == dict(installer.iter_commands())["audit-fix.md"]
    installed_agents = {
        name.removeprefix("references/agents/"): text
        for name, text in files.items()
        if name.startswith("references/agents/")
    }
    assert installed_agents == dict(installer.iter_agents())


def test_codex_skill_contains_both_pipeline_checkers():
    files = dict(installer.iter_codex_skill())

    for name, text in installer.iter_tools():
        assert files[f"scripts/{name}"] == text


def test_codex_skill_trigger_and_subagent_contract_are_explicit():
    skill = (_SOURCE_SKILL / "SKILL.md").read_text(encoding="utf-8")
    metadata = (_SOURCE_SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert "`audit+fix`" in skill
    assert "parallel subagents" in skill
    assert "Never drop an applicable role" in skill
    assert "two of three rule" in skill
    assert "$cca-audit" in metadata


def test_codex_skill_queues_subagents_behind_the_six_thread_limit():
    """Codex refuses a seventh open subagent, and a finished one keeps its slot until closed.

    Invariant: the skill caps open subagents at six, frees a slot only through `close_agent`
    after the result is recorded, and overrides the canonical "launch ALL in a SINGLE message".
    Failure caught: a DEEP run spawns up to eleven auditors at once and dies on
    `agent thread limit reached` (`agents.max_threads` defaults to 6 in codex-rs).

    Red proof: run against SKILL.md at 800fc1c, which said only "up to the available subagent
    capacity"; it failed on the first assertion below.
    """
    skill = (_SOURCE_SKILL / "SKILL.md").read_text(encoding="utf-8")
    adapter = skill.split("## Apply the Codex adapter", 1)[1].split("\n## ", 1)[0]

    assert "at most six subagents open at once" in skill
    assert "`close_agent`" in skill
    assert "A slot is free only after the close succeeds" in skill
    assert "SINGLE message" in adapter
    assert "Never drop an applicable role" in skill


def test_architecture_diagram_names_every_dispatched_auditor():
    """The diagram's two auditor boxes name exactly the auditors Step 1 dispatches.

    Replaces the banner check: the README shows this diagram, so a prefix added to
    or dropped from the spec without the diagram following would put a wrong
    auditor count on the project page. Red proof: removing ENV from the Core
    auditors sublabel in the diagram JSON fails the set comparison.
    """
    spec = (_REPO_ROOT / "cca_checks" / "plugin" / "commands" / "audit-fix.md").read_text(
        encoding="utf-8"
    )
    step1 = spec[spec.index("## Step 1: Layer 1") : spec.index("## Step 2: Layer 2")]
    dispatched = set(re.findall(r"`([A-Z]+)-`", step1)) | set(
        re.findall(r"Prefix: ([A-Z]+)-", step1)
    )

    diagram = json.loads(
        (_REPO_ROOT / "docs" / "diagrams" / "cca-overview.architecture.json").read_text(
            encoding="utf-8"
        )
    )
    boxes = {c["id"]: c for c in diagram["components"]}
    drawn = boxes["auditors"]["sublabel"].split() + boxes["conditional"]["sublabel"].split()

    assert len(dispatched) == 11
    assert len(drawn) == len(set(drawn))
    assert set(drawn) == dispatched


def test_install_codex_materializes_a_complete_skill(tmp_path):
    result = installer.install_codex(tmp_path)
    skill = tmp_path / "cca-audit"

    expected = dict(installer.iter_codex_skill())
    actual = {
        path.relative_to(skill).as_posix(): path.read_text(encoding="utf-8")
        for path in skill.rglob("*")
        if path.is_file()
    }
    assert actual == expected
    assert result.installed == len(expected)
    assert result.backed_up == 0


def test_install_codex_is_idempotent_and_preserves_customization(tmp_path):
    installer.install_codex(tmp_path)
    second = installer.install_codex(tmp_path)
    assert second.backed_up == 0

    skill_file = tmp_path / "cca-audit" / "SKILL.md"
    customized = skill_file.read_text(encoding="utf-8") + "\n# local policy\n"
    skill_file.write_text(customized, encoding="utf-8")

    third = installer.install_codex(tmp_path)

    assert third.backed_up == 1
    assert skill_file.with_suffix(".md.bak").read_text(encoding="utf-8") == customized


def test_install_codex_refuses_a_file_at_the_skill_path(tmp_path):
    skill_path = tmp_path / "cca-audit"
    skill_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="cca-audit"):
        installer.install_codex(tmp_path)

    assert skill_path.read_text(encoding="utf-8") == "not a directory"


def test_install_codex_preflights_ancestor_conflicts_before_writing(tmp_path):
    skill = tmp_path / "cca-audit"
    skill.mkdir()
    skill_file = skill / "SKILL.md"
    skill_file.write_text("custom skill", encoding="utf-8")
    (skill / "references").write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="references"):
        installer.install_codex(tmp_path)

    assert skill_file.read_text(encoding="utf-8") == "custom skill"
    assert not (skill / "agents").exists()


def test_install_codex_keeps_the_live_skill_on_a_staged_write_failure(tmp_path, monkeypatch):
    installer.install_codex(tmp_path)
    skill = tmp_path / "cca-audit"
    skill_file = skill / "SKILL.md"
    skill_file.write_text("custom skill", encoding="utf-8")
    before = {
        path.relative_to(skill).as_posix(): path.read_bytes()
        for path in skill.rglob("*")
        if path.is_file()
    }

    real_write = installer._write
    calls = 0

    def fail_second_write(destination, text, result):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected write failure")
        real_write(destination, text, result)

    monkeypatch.setattr(installer, "_write", fail_second_write)
    with pytest.raises(OSError, match="injected write failure"):
        installer.install_codex(tmp_path)

    after = {
        path.relative_to(skill).as_posix(): path.read_bytes()
        for path in skill.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_install_codex_preserves_the_old_tree_when_rollback_fails(tmp_path, monkeypatch):
    installer.install_codex(tmp_path)
    skill = tmp_path / "cca-audit"
    original_skill = (skill / "SKILL.md").read_bytes()
    real_replace = installer.os.replace
    calls = 0

    def fail_commit_and_rollback(source, destination):
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError(f"injected replace failure {calls}")
        real_replace(source, destination)

    monkeypatch.setattr(installer.os, "replace", fail_commit_and_rollback)
    with pytest.raises(RuntimeError, match="previous skill is preserved"):
        installer.install_codex(tmp_path)

    preserved = list(tmp_path.glob(".cca-audit-old-*"))
    assert len(preserved) == 1
    assert (preserved[0] / "SKILL.md").read_bytes() == original_skill


def test_install_codex_rejects_links_inside_the_live_skill(tmp_path):
    skill = tmp_path / "cca-audit"
    skill.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = skill / "references"
    try:
        os.symlink(outside, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"directory symlinks unavailable: {exc}")

    with pytest.raises(RuntimeError, match="link|reparse"):
        installer.install_codex(tmp_path)

    assert not list(outside.iterdir())


def test_install_codex_requires_every_canonical_agent_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(installer, "iter_agents", lambda: iter(()))

    with pytest.raises(RuntimeError, match="agent"):
        installer.install_codex(tmp_path)

    assert not (tmp_path / "cca-audit").exists()


def test_default_codex_skills_root_respects_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert installer.default_codex_skills_root() == tmp_path / "skills"
