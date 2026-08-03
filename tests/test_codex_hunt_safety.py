"""Safety contract for executing commands declared by hunt targets."""

from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SKILL = _REPO_ROOT / "cca_checks" / "plugin" / "codex" / "cca-audit" / "SKILL.md"


def test_codex_skill_gates_commands_from_untrusted_hunt_targets():
    skill = " ".join(_SKILL.read_text(encoding="utf-8").lower().split())

    assert "untrusted repository execution" in skill
    assert "explicit authorization" in skill
    assert "repository supplied command" in skill
    assert "static analysis only" in skill
