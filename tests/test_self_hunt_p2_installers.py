"""P2 installer fixes from the CCA self hunt, run 20261120T201246Z.

Each test names its finding. Red proofs: each behaviour test below, run against the pre-fix
installers (the scripts at 61eb961 and the snapshot taken before these fixes), failed in its
assertion.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from cca_checks.plugin import installer

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash")
# Windows PowerShell 5.1 is part of Windows, so this is a platform gate, not a tool CI
# installs: the test runs on a Windows workstation and skips on the Linux CI runner.
_PS51 = Path(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "WindowsPowerShell",
             "v1.0", "powershell.exe")
POWERSHELL = str(_PS51) if sys.platform == "win32" and _PS51.is_file() else None


def _checkout(tmp_path: Path) -> Path:
    """A copy of the installer scripts and their assets, laid out as a checkout."""
    repo = tmp_path / "repo"
    shutil.copytree(ROOT / "claude-code", repo / "claude-code")
    shutil.copytree(ROOT / "cca_checks" / "plugin", repo / "cca_checks" / "plugin",
                    ignore=shutil.ignore_patterns("__pycache__"))
    # A checkout made on Windows may hold the script with CRLF, which Linux bash rejects.
    script = repo / "claude-code" / "install.sh"
    script.write_bytes(script.read_bytes().replace(b"\r\n", b"\n"))
    return repo


def _baks(directory: Path, name: str) -> list[str]:
    return [p.read_text(encoding="utf-8") for p in directory.glob(name + ".bak*")]


def test_a_second_upgrade_keeps_the_customization(tmp_path):
    """STAKES-004. Invariant: a customization set aside as a backup survives later upgrades.
    Failure mode: every upgrade overwrote `<name>.bak`, so the second one replaced the saved
    customization with the previous stock file. Red: "custom" missing from every backup."""
    dest = tmp_path / "cca-bug-auditor.md"
    dest.write_text("custom", encoding="utf-8")
    result = installer.InstallResult()
    installer._write(dest, "stock v1", result)
    installer._write(dest, "stock v2", result)
    assert dest.read_text(encoding="utf-8") == "stock v2"
    assert "custom" in _baks(tmp_path, dest.name)


@pytest.mark.skipif(BASH is None or "system32" in (BASH or "").lower(),
                    reason="needs a POSIX bash (Git Bash on Windows)")
def test_install_sh_installs_the_guards_and_keeps_customizations(tmp_path):
    """CODE-003 and STAKES-004 for install.sh. Invariant: the documented shell install puts
    the commit and stop guards in .claude/hooks/, and a customization survives two upgrades.
    Failure mode: install.sh copied agents, commands and tools only, so a project installed
    that way had no guard and no warning. Red: .claude/hooks/cca_pipeline_guard.py missing."""
    repo = _checkout(tmp_path)  # no pyproject.toml beside it, so the pip step is skipped
    project = tmp_path / "project"
    project.mkdir()
    agent = repo / "cca_checks" / "plugin" / "agents" / "cca-bug-auditor.md"
    installed = project / ".claude" / "agents" / agent.name

    def run():
        done = subprocess.run([BASH, str(repo / "claude-code" / "install.sh")], cwd=project,
                              capture_output=True, text=True, timeout=120)
        # Not the exit code: in local mode the EXIT trap's `[[ -n "$CLEANUP_DIR" ]]` is
        # false, so the script ends with status 1 after a complete install (pre-existing).
        assert "CCA-Audit installed." in done.stdout, done.stdout + done.stderr

    run()
    assert (project / ".claude" / "hooks" / "cca_pipeline_guard.py").is_file()
    assert (project / ".claude" / "hooks" / "cca_gate_core.py").is_file()
    assert (project / ".claude" / "guides" / "qwen-tests.md").is_file()
    installed.write_text("custom", encoding="utf-8")
    agent.write_text("stock v1", encoding="utf-8")
    run()
    agent.write_text("stock v2", encoding="utf-8")
    run()
    assert "custom" in _baks(installed.parent, installed.name)


@pytest.mark.skipif(POWERSHELL is None, reason="needs Windows PowerShell 5.1")
def test_install_ps1_survives_pip_writing_to_stderr(tmp_path):
    """DEPLOY-002. Invariant: install.ps1 decides on pip's exit code, not on its stderr.
    Failure mode: under Windows PowerShell 5.1 with ErrorActionPreference Stop, a pip notice
    on stderr merged by 2>&1 was a terminating error, so the install aborted after copying
    files. Red: non-zero exit and no "Installed cca_checks" line."""
    repo = _checkout(tmp_path)
    (repo / "pyproject.toml").write_text("[project]\nname = 'x'\n", encoding="utf-8")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    # A stand-in python: pip prints a notice on stderr and succeeds; anything else succeeds.
    (bin_dir / "python.bat").write_text(
        '@if "%2"=="pip" echo [notice] A new release of pip is available 1>&2\r\n'
        "@exit /b 0\r\n", encoding="ascii")
    project = tmp_path / "project"
    project.mkdir()
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}"}
    done = subprocess.run([POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                           str(repo / "claude-code" / "install.ps1")], cwd=project, env=env,
                          capture_output=True, text=True, timeout=180)
    assert done.returncode == 0, done.stdout + done.stderr
    assert "Installed cca_checks" in done.stdout
    assert (project / ".claude" / "hooks" / "cca_pipeline_guard.py").is_file()
