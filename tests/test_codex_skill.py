"""Contract tests for the Codex native CCA skill."""

from pathlib import Path

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


def test_default_codex_skills_root_respects_codex_home(tmp_path, monkeypatch):
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    assert plugin.default_codex_skills_root() == tmp_path / "skills"
