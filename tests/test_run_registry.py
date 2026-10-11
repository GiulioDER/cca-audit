"""The open run registry: how a hook finds a run opened below the session's directory.

Every test here reads the registry FILE rather than `registered_roots()` where it matters:
`registered_roots()` skips an entry whose run-state.json is gone, so after `close` or
`abort` it would read empty even if nothing had been unregistered.
"""

from __future__ import annotations

import json
import os
import pathlib

from conftest import write_output

from cca_checks import pipeline_state as ps

core = ps.core


def _entries(registry) -> dict:
    """The registry's entries; a registry never written holds none."""
    if not registry.exists():
        return {}
    return json.loads(registry.read_text(encoding="utf-8"))["runs"]


def _finish_fast_run(root) -> None:
    ps.record("L1", output=write_output(root), root=root)
    ps.record("L2", counts={"unique": 0}, output=write_output(root), root=root)
    ps.record("L2.5", counts={"confirmed": 0, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=write_output(root), root=root)
    ps.record("L3", counts={"to_fix": 0}, output=write_output(root), root=root)
    ps.record("L6", detail="APPROVED", root=root)


def test_start_registers_the_realpath_and_close_unregisters(git_repo, _private_run_registry):
    """Invariant: an open run is listed under its realpath; a closed one is not.

    Red 2026-10-11: mutation `_registry("register", ...)` removed from `pipeline_state.start`;
    failed at `assert list(_entries(registry)) == [os.path.realpath(git_repo)]`.
    Red 2026-10-11: mutation `_registry("unregister", root)` removed from `close`; failed at
    `assert _entries(registry) == {}`.
    """
    registry = _private_run_registry
    state = ps.start("FAST", root=git_repo)
    assert list(_entries(registry)) == [os.path.realpath(git_repo)]
    assert _entries(registry)[os.path.realpath(git_repo)]["run_id"] == state.run_id
    assert core.registered_roots() == [pathlib.Path(os.path.realpath(git_repo))]
    _finish_fast_run(git_repo)
    ps.close(root=git_repo)
    assert _entries(registry) == {}


def test_abort_unregisters(git_repo, _private_run_registry):
    """Red 2026-10-11: mutation `_registry("unregister", root)` removed from `abort`; failed at
    `assert _entries(registry) == {}`.
    """
    registry = _private_run_registry
    ps.start("DEEP", root=git_repo)
    ps.abort("unrelated work", root=git_repo)
    assert _entries(registry) == {}


def test_abort_of_an_unparseable_state_unregisters(git_repo, _private_run_registry):
    """A run whose state cannot be read is still taken out of the registry by `abort`.

    Red 2026-10-11: mutation `_registry("unregister", root)` moved inside the `try` of
    `abort`, after `load(root)` (so an unparseable state skips it); failed at
    `assert _entries(registry) == {}`.
    """
    registry = _private_run_registry
    ps.start("DEEP", root=git_repo)
    ps.state_path(git_repo).write_text("{oops", encoding="utf-8")
    ps.abort("the state is corrupt", root=git_repo)
    assert _entries(registry) == {}


def test_cca_run_registry_is_honoured(git_repo, tmp_path_factory, monkeypatch):
    """`$CCA_RUN_REGISTRY` names the file; the home default is not written.

    HOME and USERPROFILE point at a scratch directory, so even a regression that ignored the
    variable could not write to the real `~/.claude`.
    Red 2026-10-11: mutation `registry_path` ignores the variable and always returns the home
    default; failed at `assert core.registry_path() == chosen`.
    """
    home = tmp_path_factory.mktemp("home")
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    chosen = tmp_path_factory.mktemp("elsewhere") / "nested" / "runs.json"
    monkeypatch.setenv("CCA_RUN_REGISTRY", str(chosen))
    assert core.registry_path() == chosen
    ps.start("FAST", root=git_repo)
    assert chosen.is_file()
    assert os.path.realpath(git_repo) in _entries(chosen)
    assert not (home / ".claude" / "cca" / "open-runs.json").exists()


def test_registered_roots_skips_an_entry_whose_state_is_gone(
    git_repo, tmp_path_factory, _private_run_registry
):
    """Red 2026-10-11: mutation the `if state_file(key).exists()` filter removed from
    `registered_roots`; failed at `assert core.registered_roots() == [...]`.
    """
    gone = tmp_path_factory.mktemp("gone")
    ps.start("FAST", root=git_repo)
    core.register(gone, "ghost")
    assert len(_entries(_private_run_registry)) == 2
    assert core.registered_roots() == [pathlib.Path(os.path.realpath(git_repo))]


def test_a_registry_that_cannot_be_written_is_a_warning_not_a_refusal(
    git_repo, tmp_path_factory, monkeypatch, capsys
):
    """The registry is a lookup aid: `start` and `abort` succeed without it, and say so.

    Red 2026-10-11: mutation `_registry` returns without printing the warning; failed at
    `assert "run registry" in capsys.readouterr().err`.
    """
    blocker = tmp_path_factory.mktemp("blocked") / "a-file"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("CCA_RUN_REGISTRY", str(blocker / "open-runs.json"))
    state = ps.start("FAST", root=git_repo)
    assert state.run_id and ps.state_path(git_repo).exists()
    assert "run registry" in capsys.readouterr().err
    assert ps.abort("done with it", root=git_repo) is not None
    assert not ps.state_path(git_repo).exists()
