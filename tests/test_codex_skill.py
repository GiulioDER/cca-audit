"""Contract tests for the Codex native CCA skill."""

import os
from pathlib import Path

import pytest

from cca_checks import plugin

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SOURCE_SKILL = _REPO_ROOT / "cca_checks" / "plugin" / "codex" / "cca-audit"


def test_codex_skill_templates_are_packaged():
    files = dict(plugin.iter_codex_skill())

    assert files["SKILL.md"] == (_SOURCE_SKILL / "SKILL.md").read_text(encoding="utf-8")
    assert files["agents/openai.yaml"] == (
        _SOURCE_SKILL / "agents" / "openai.yaml"
    ).read_text(encoding="utf-8")


def test_codex_skill_reuses_the_canonical_pipeline_and_agent_prompts():
    files = dict(plugin.iter_codex_skill())

    assert files["references/pipeline.md"] == dict(plugin.iter_commands())["audit-fix.md"]
    installed_agents = {
        name.removeprefix("references/agents/"): text
        for name, text in files.items()
        if name.startswith("references/agents/")
    }
    assert installed_agents == dict(plugin.iter_agents())


def test_codex_skill_contains_both_pipeline_checkers():
    files = dict(plugin.iter_codex_skill())

    for name, text in plugin.iter_tools():
        assert files[f"scripts/{name}"] == text


def test_codex_skill_trigger_and_subagent_contract_are_explicit():
    skill = (_SOURCE_SKILL / "SKILL.md").read_text(encoding="utf-8")
    metadata = (_SOURCE_SKILL / "agents" / "openai.yaml").read_text(encoding="utf-8")

    assert "`audit+fix`" in skill
    assert "parallel subagents" in skill
    assert "Never drop an applicable role" in skill
    assert "two of three rule" in skill
    assert "$cca-audit" in metadata


def test_banner_states_one_auditor_count():
    banner = (_REPO_ROOT / "docs" / "banner.svg").read_text(encoding="utf-8")
    assert "auditors ×11" in banner
    assert "11 auditors, non-overlapping scopes" in banner


def test_install_codex_materializes_a_complete_skill(tmp_path):
    result = plugin.install_codex(tmp_path)
    skill = tmp_path / "cca-audit"

    expected = dict(plugin.iter_codex_skill())
    actual = {
        path.relative_to(skill).as_posix(): path.read_text(encoding="utf-8")
        for path in skill.rglob("*")
        if path.is_file()
    }
    assert actual == expected
    assert result.installed == len(expected)
    assert result.backed_up == 0


def test_install_codex_is_idempotent_and_preserves_customization(tmp_path):
    plugin.install_codex(tmp_path)
    second = plugin.install_codex(tmp_path)
    assert second.backed_up == 0

    skill_file = tmp_path / "cca-audit" / "SKILL.md"
    customized = skill_file.read_text(encoding="utf-8") + "\n# local policy\n"
    skill_file.write_text(customized, encoding="utf-8")

    third = plugin.install_codex(tmp_path)

    assert third.backed_up == 1
    assert skill_file.with_suffix(".md.bak").read_text(encoding="utf-8") == customized


def test_install_codex_refuses_a_file_at_the_skill_path(tmp_path):
    skill_path = tmp_path / "cca-audit"
    skill_path.write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="cca-audit"):
        plugin.install_codex(tmp_path)

    assert skill_path.read_text(encoding="utf-8") == "not a directory"


def test_install_codex_preflights_ancestor_conflicts_before_writing(tmp_path):
    skill = tmp_path / "cca-audit"
    skill.mkdir()
    skill_file = skill / "SKILL.md"
    skill_file.write_text("custom skill", encoding="utf-8")
    (skill / "references").write_text("not a directory", encoding="utf-8")

    with pytest.raises(NotADirectoryError, match="references"):
        plugin.install_codex(tmp_path)

    assert skill_file.read_text(encoding="utf-8") == "custom skill"
    assert not (skill / "agents").exists()


def test_install_codex_keeps_the_live_skill_on_a_staged_write_failure(tmp_path, monkeypatch):
    plugin.install_codex(tmp_path)
    skill = tmp_path / "cca-audit"
    skill_file = skill / "SKILL.md"
    skill_file.write_text("custom skill", encoding="utf-8")
    before = {
        path.relative_to(skill).as_posix(): path.read_bytes()
        for path in skill.rglob("*")
        if path.is_file()
    }

    real_write = plugin._write
    calls = 0

    def fail_second_write(destination, text, result):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected write failure")
        real_write(destination, text, result)

    monkeypatch.setattr(plugin, "_write", fail_second_write)
    with pytest.raises(OSError, match="injected write failure"):
        plugin.install_codex(tmp_path)

    after = {
        path.relative_to(skill).as_posix(): path.read_bytes()
        for path in skill.rglob("*")
        if path.is_file()
    }
    assert after == before


def test_install_codex_preserves_the_old_tree_when_rollback_fails(tmp_path, monkeypatch):
    plugin.install_codex(tmp_path)
    skill = tmp_path / "cca-audit"
    original_skill = (skill / "SKILL.md").read_bytes()
    real_replace = plugin.os.replace
    calls = 0

    def fail_commit_and_rollback(source, destination):
        nonlocal calls
        calls += 1
        if calls in {2, 3}:
            raise OSError(f"injected replace failure {calls}")
        real_replace(source, destination)

    monkeypatch.setattr(plugin.os, "replace", fail_commit_and_rollback)
    with pytest.raises(RuntimeError, match="previous skill is preserved"):
        plugin.install_codex(tmp_path)

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
        plugin.install_codex(tmp_path)

    assert not list(outside.iterdir())


def test_install_codex_requires_every_canonical_agent_prompt(tmp_path, monkeypatch):
    monkeypatch.setattr(plugin, "iter_agents", lambda: iter(()))

    with pytest.raises(RuntimeError, match="agent"):
        plugin.install_codex(tmp_path)

    assert not (tmp_path / "cca-audit").exists()


def test_default_codex_skills_root_respects_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert plugin.default_codex_skills_root() == tmp_path / "skills"
