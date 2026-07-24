"""Tests for analyzer path resolution.

`resolve_tool` had no direct coverage until 2026-07-24: every caller monkeypatches
it away, so its behaviour was argued for in a long docstring and never executed.
It shipped returning `os.path.realpath(found)`, which silently broke the Rust
layer in CI -- `~/.cargo/bin/cargo` is a symlink to `rustup`, and rustup dispatches
on `argv[0]`, so the resolved launch ran rustup with cargo's arguments. Nine tests
failed with `error: unexpected argument '--manifest-path'`. In production the same
bug would have been quieter: every Rust claim escalating to UNCERTAIN with no
stated reason.

These drive the *decision* -- which path is returned, and which are refused -- by
faking the two filesystem queries it makes. A first draft built real files and real
symlinks instead, and was worse: on Windows `shutil.which` skips extension-less
files and `os.symlink` needs developer mode, so the refusal tests passed because
`which` found nothing at all. They would have passed with the security check
deleted. End-to-end coverage of the launch itself lives in test_cargo_repro.py and
test_clippy_check.py, which exercise a real toolchain in CI.
"""
import os

import pytest

from cca_checks import toolpath
from cca_checks.toolpath import resolve_tool


@pytest.fixture
def fs(monkeypatch):
    """Fake `shutil.which` + symlink resolution for the module under test.

    `links` maps a path to what it ultimately points at; anything absent resolves
    to itself, so a plain (non-symlink) binary needs no entry.
    """
    def setup(which_result, links=None):
        links = {os.path.abspath(k): os.path.abspath(v) for k, v in (links or {}).items()}
        monkeypatch.setattr(
            toolpath.shutil, "which",
            lambda name, path=None: which_result,
        )
        monkeypatch.setattr(
            toolpath.os.path, "realpath",
            lambda p: links.get(os.path.abspath(p), os.path.abspath(p)),
        )
    return setup


REPO = os.path.abspath("/repo")
SYSBIN = os.path.abspath("/sys/bin")


def test_a_symlinked_tool_launches_by_its_link_name(fs):
    """The regression: multi-call binaries dispatch on argv[0].

    Returning the realpath is what made `cargo` run as `rustup`. The assertion is
    on the BASENAME, because that is the byte rustup actually branches on --
    asserting merely that some path came back would pass against the broken
    version and prove nothing.
    """
    cargo = os.path.join(SYSBIN, "cargo")
    fs(cargo, links={cargo: os.path.join(SYSBIN, "rustup")})

    got = resolve_tool("cargo", cwd=REPO)

    assert got is not None, "a tool on PATH must resolve"
    assert os.path.basename(got) == "cargo", (
        f"resolved through the symlink to {os.path.basename(got)!r}; a multi-call "
        "binary would now dispatch as the wrong tool"
    )


def test_the_returned_path_is_absolute(fs):
    fs(os.path.join(SYSBIN, "pyright"))

    got = resolve_tool("pyright", cwd=REPO)

    assert got is not None
    assert os.path.isabs(got)


def test_a_binary_planted_in_the_audited_root_is_refused(fs):
    """The hijack case this module exists for."""
    fs(os.path.join(REPO, "semgrep"))

    assert resolve_tool("semgrep", cwd=REPO) is None


def test_a_symlink_planted_in_the_audited_root_is_also_refused(fs):
    """Checking only the resolved target would let this through.

    The link sits in the repo root and points at a binary that is innocuous now.
    Whoever controls the repo controls where it points next, so the link's own
    location has to be disqualifying on its own.
    """
    link = os.path.join(REPO, "semgrep")
    fs(link, links={link: os.path.join(SYSBIN, "semgrep")})

    assert resolve_tool("semgrep", cwd=REPO) is None


def test_a_symlink_elsewhere_aiming_into_the_audited_root_is_refused(fs):
    """The mirror case: checking only the link's own location would let this through."""
    link = os.path.join(SYSBIN, "semgrep")
    fs(link, links={link: os.path.join(REPO, "semgrep")})

    assert resolve_tool("semgrep", cwd=REPO) is None


def test_a_project_local_venv_tool_is_still_allowed(fs):
    """The refusal must stay narrow.

    `.venv/bin/pyright` is the standard Python layout. Rejecting anything under
    the audited tree would switch the deterministic layer off for the most common
    setup, which costs far more than the rare planted binary it would catch.
    """
    fs(os.path.join(REPO, ".venv", "bin", "pyright"))

    assert resolve_tool("pyright", cwd=REPO) is not None


def test_a_missing_tool_is_none(fs):
    fs(None)

    assert resolve_tool("definitely-not-a-real-tool", cwd=REPO) is None
