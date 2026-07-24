"""Tests for `cca-audit install` -- the pip-native replacement for `curl | bash`.

Two distinct failure classes are covered here, and they fail at different times:

1. **Packaging.** The agent and command markdown is the product; `cca_checks`
   is only the verifier it shells out to. If those files are missing from the
   wheel, `pip install cca-audit` succeeds, `cca-audit install` succeeds, and
   the user gets an empty `.claude/` -- a silent, total failure that no import
   test can see. `test_every_shipped_*` asserts the invariant (everything in
   the source tree is reachable through `importlib.resources`), not a count.

2. **Overwrite semantics.** `claude-code/README.md` tells users to CONFIGURE
   this tool by editing the very files the installer writes. The install
   surface and the config surface are the same files, so an unconditional
   overwrite makes upgrade == silent config loss. The shell installer solved
   this with a `.bak` copy; this must match it, or the two install paths
   disagree about whether your customizations survive an upgrade.
"""

import pathlib

import pytest

from cca_checks import plugin

_REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
_SRC_AGENTS = _REPO_ROOT / "cca_checks" / "plugin" / "agents"
_SRC_COMMANDS = _REPO_ROOT / "cca_checks" / "plugin" / "commands"


def test_every_shipped_agent_is_reachable_as_a_package_resource():
    """Every cca-*.md in the source tree must be readable via importlib.resources.

    Asserts set equality against the source tree rather than a hardcoded count:
    a new auditor added tomorrow is covered automatically, and a file that
    stops being packaged is caught even if someone also updates a count.
    """
    on_disk = {p.name for p in _SRC_AGENTS.glob("cca-*.md")}
    packaged = {name for name, _ in plugin.iter_agents()}
    assert on_disk, "no agent markdown found in the source tree -- wrong path?"
    assert packaged == on_disk


def test_every_shipped_command_is_reachable_as_a_package_resource():
    on_disk = {p.name for p in _SRC_COMMANDS.glob("audit-fix*.md")}
    packaged = {name for name, _ in plugin.iter_commands()}
    assert on_disk, "no command markdown found in the source tree -- wrong path?"
    assert packaged == on_disk


def test_packaged_agent_content_is_not_empty():
    """A resource that resolves but reads empty ships a broken agent.

    `importlib.resources` happily returns an empty string for a file that was
    truncated by a packaging mistake, so "it resolved" is not sufficient.
    """
    for name, text in plugin.iter_agents():
        assert text.strip(), f"packaged agent {name} is empty"
        assert text.lstrip().startswith("---"), (
            f"packaged agent {name} lost its YAML frontmatter; Claude Code will "
            "not register an agent without it"
        )


def test_install_writes_agents_and_commands(tmp_path):
    result = plugin.install(tmp_path)

    agents = {p.name for p in (tmp_path / ".claude" / "agents").glob("*.md")}
    commands = {p.name for p in (tmp_path / ".claude" / "commands").glob("*.md")}

    assert agents == {name for name, _ in plugin.iter_agents()}
    assert commands == {name for name, _ in plugin.iter_commands()}
    assert result.installed == len(agents) + len(commands)
    assert result.backed_up == 0


def test_install_is_idempotent_and_does_not_back_up_identical_files(tmp_path):
    """Re-running the installer on an unmodified tree must not litter .bak files.

    The shell installer only backs up when the destination *differs*. A
    version that backed up unconditionally would produce a `.bak` per file on
    every upgrade, which trains users to ignore them -- and the one time a
    `.bak` mattered they would.
    """
    plugin.install(tmp_path)
    second = plugin.install(tmp_path)

    assert second.backed_up == 0
    assert not list((tmp_path / ".claude" / "agents").glob("*.bak"))


def test_install_preserves_a_customized_file_as_bak(tmp_path):
    """The configuration surface IS the install surface -- customization must survive."""
    plugin.install(tmp_path)
    target = tmp_path / ".claude" / "agents" / "cca-bug-auditor.md"
    customized = target.read_text(encoding="utf-8") + "\n# my local tweak\n"
    target.write_text(customized, encoding="utf-8")

    result = plugin.install(tmp_path)

    assert result.backed_up == 1
    assert target.with_suffix(".md.bak").read_text(encoding="utf-8") == customized
    # and the fresh version actually landed
    assert "# my local tweak" not in target.read_text(encoding="utf-8")


def test_install_reports_a_shadowing_agent_name(tmp_path):
    """A pre-existing agent declaring a name we dispatch silently shadows ours.

    Our files are named `cca-*.md` but their frontmatter `name:` is generic
    (`bug-auditor`, ...), so a project that already defines one of those names
    has a collision that a filename glob cannot see. The shell installer warns;
    so must this one.
    """
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "my-own.md").write_text("---\nname: bug-auditor\n---\n", encoding="utf-8")

    result = plugin.install(tmp_path)

    assert any("my-own.md" in w for w in result.warnings), result.warnings


def test_install_does_not_warn_about_our_own_files(tmp_path):
    """cca-*.md files are ours; re-running must not warn about the ones we just wrote."""
    plugin.install(tmp_path)
    result = plugin.install(tmp_path)
    assert result.warnings == []


def test_install_creates_the_target_tree_when_absent(tmp_path):
    target = tmp_path / "brand" / "new" / "project"
    plugin.install(target)
    assert (target / ".claude" / "agents").is_dir()
    assert (target / ".claude" / "commands").is_dir()


def test_install_refuses_a_target_that_is_a_file(tmp_path):
    """Fail loudly rather than raising a confusing mkdir error deep in the call."""
    bogus = tmp_path / "not-a-dir"
    bogus.write_text("", encoding="utf-8")
    with pytest.raises(NotADirectoryError):
        plugin.install(bogus)
