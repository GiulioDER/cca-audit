"""Replay POSIX no-follow errors at the public planner boundary on every host.

Baseline c66f148, CI run 40230900203: a directory symlink raises ENOTDIR on
Linux, bypassing the planner's UnsafeArchive to Unavailable translation.
The two refusal cases must fail at their assertion before the production fix.
EACCES is a control: unrelated filesystem errors keep their original identity.
"""
from __future__ import annotations

import errno
import os
from types import SimpleNamespace

import pytest
from bugs_testkit import isolated_git_environment as isolated_git_environment
from test_anchor_recovery import history

from cca_checks.bugs import archive_io, store
from cca_checks.bugs.recover_anchors import Unavailable, plan


@pytest.mark.parametrize("error", [errno.ELOOP, errno.ENOTDIR, errno.EACCES])
def test_posix_component_refusal_preserves_public_contract(tmp_path, monkeypatch, error):
    """No-follow errors become a domain refusal; all acquired handles still close."""
    row, repo, _, _ = history(tmp_path)
    ledger = tmp_path / "ledger"
    store.write([row], ledger, "stamp")
    opened, closed, calls = [], [], []

    def open_component(path, flags, *, dir_fd=None):
        calls.append((path, flags, dir_fd))
        if path == "linked-archives":
            raise OSError(error, "injected POSIX component refusal", path)
        fd = 100 + len(opened)
        opened.append(fd)
        return fd

    # Replace this module's OS adapter only. Global os.name and Path keep the
    # real host implementation; no fake descriptor reaches an actual OS call.
    adapter = SimpleNamespace(name="posix", path=os.path, O_RDONLY=0,
                              O_DIRECTORY=0x100000, O_NOFOLLOW=0x200000,
                              O_NONBLOCK=0x400000, open=open_component, close=closed.append)
    monkeypatch.setattr(archive_io, "os", adapter)
    unavailable, raw_error = False, None
    try:
        plan(ledger, [repo], [tmp_path / "linked-archives"])
    except Unavailable:
        unavailable = True
    except OSError as exc:
        raw_error = exc

    assert closed == list(reversed(opened)), "failed traversal leaked pinned ancestors"
    assert calls[-1][0] == "linked-archives"
    assert calls[-1][1] & adapter.O_NOFOLLOW
    assert calls[-1][1] & adapter.O_DIRECTORY
    assert calls[-1][2] == opened[-1]
    if error == errno.EACCES:
        assert not unavailable and raw_error is not None and raw_error.errno == error
    else:
        assert unavailable and raw_error is None, "POSIX link refusal escaped the domain contract"
