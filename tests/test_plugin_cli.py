"""Tests for the `cca-audit` console script.

The exit code is the contract here. This command is meant to be chained
(`pip install cca-audit && cca-audit install`) and run in CI, so "wrote
nothing at all" must not exit 0 -- a silent success is how a broken install
reaches a user who then blames the audit for finding nothing.
"""

import pathlib

from cca_checks.plugin import cli


def test_install_command_writes_the_plugin_and_exits_zero(tmp_path, capsys):
    code = cli.main(["install", "--target", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert (tmp_path / ".claude" / "agents").is_dir()
    assert list((tmp_path / ".claude" / "commands").glob("audit-fix*.md"))
    assert "/audit-fix" in out, "the CLI should tell the user how to run it"


def test_install_defaults_to_the_current_directory(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    code = cli.main(["install"])
    capsys.readouterr()

    assert code == 0
    assert (tmp_path / ".claude" / "agents").is_dir()


def test_install_reports_backed_up_customizations(tmp_path, capsys):
    cli.main(["install", "--target", str(tmp_path)])
    target = tmp_path / ".claude" / "agents" / "cca-bug-auditor.md"
    target.write_text(target.read_text(encoding="utf-8") + "\n# tweak\n", encoding="utf-8")
    capsys.readouterr()

    code = cli.main(["install", "--target", str(tmp_path)])
    out = capsys.readouterr().out

    assert code == 0
    assert ".bak" in out, "a user whose customization was displaced must be told"


def test_install_surfaces_a_shadowing_warning(tmp_path, capsys):
    agents_dir = tmp_path / ".claude" / "agents"
    agents_dir.mkdir(parents=True)
    (agents_dir / "mine.md").write_text("---\nname: fp-check\n---\n", encoding="utf-8")

    code = cli.main(["install", "--target", str(tmp_path)])
    captured = capsys.readouterr()

    assert code == 0
    assert "mine.md" in captured.out + captured.err


def test_install_on_a_file_target_exits_nonzero_with_a_message(tmp_path, capsys):
    bogus = tmp_path / "afile"
    bogus.write_text("", encoding="utf-8")

    code = cli.main(["install", "--target", str(bogus)])
    err = capsys.readouterr().err

    assert code != 0
    assert str(bogus) in err


def test_version_flag_reports_the_installed_version(capsys):
    code = cli.main(["--version"])
    out = capsys.readouterr().out

    assert code == 0
    assert out.strip(), "--version must print something"


def test_no_arguments_prints_help_and_exits_nonzero(capsys):
    """A bare `cca-audit` should not look like it succeeded at anything."""
    code = cli.main([])
    captured = capsys.readouterr()

    assert code != 0
    assert "install" in captured.out + captured.err


def test_console_script_entry_point_is_declared():
    """The entry point in pyproject must name a real, callable target.

    A typo here builds and installs cleanly; the failure surfaces only when a
    user types `cca-audit` and gets a traceback from the generated shim.
    """
    pyproject = (
        pathlib.Path(__file__).resolve().parent.parent / "pyproject.toml"
    ).read_text(encoding="utf-8")
    assert 'cca-audit = "cca_checks.plugin.cli:main"' in pyproject
    assert callable(cli.main)
