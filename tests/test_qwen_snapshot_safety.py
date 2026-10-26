"""SEC-001 regressions at the public snapshot preparation boundary.

Red proof: restore the pre-fix ``prepare`` from the CCA tautology snapshot.
Both race cases then package the outside sentinel and fail the final byte
assertion. The initial ordinary read is a positive fixture control.
"""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from cca_checks import qwen_tests as q


def test_prepare_does_not_execute_repository_fsmonitor_hook(git_repo):
    """Git metadata reads must not execute a repository-configured client hook."""
    marker = git_repo.parent / (git_repo.name + "-hook-ran")
    hook = git_repo / ".git/fsmonitor-hook.py"
    hook.write_text("from pathlib import Path\nimport sys\n"
                    + f"Path({str(marker)!r}).write_text('ran')\n"
                    + "sys.stdout.buffer.write(b'fixture-token\\0/\\0')\n")
    command = f'"{Path(sys.executable).as_posix()}" "{hook.as_posix()}"'
    def git(*args):
        return subprocess.run(["git", "-C", str(git_repo), *args], check=True,
                              capture_output=True)
    git("config", "core.untrackedCache", "true")
    git("config", "status.showUntrackedFiles", "all")
    git("config", "core.fsmonitor", command)
    (git_repo / "untracked.py").write_text("x = 2\n")
    # Initialize fsmonitor/untracked index state, then isolate prepare's effect.
    git("status", "--porcelain")
    git("status", "--porcelain")
    assert marker.exists(), "the harmless hook fixture must be executable"
    marker.unlink()
    q.prepare(git_repo, "example", "run-no-hooks", ["app.py"], "no-hooks")
    assert not marker.exists(), "snapshot preparation executed a repository Git hook on the client"


@pytest.mark.skipif(os.name != "posix", reason="deterministic POSIX symlink race control")
@pytest.mark.parametrize("replace", ["file", "ancestor"])
def test_prepare_never_reads_outside_file_during_path_replacement(
        git_repo, monkeypatch, replace):
    """The final open must reject the raced link or retain the original directory.

    The test targets ``prepare`` file opening. A plausible mutation back to
    ``Path.open`` packages the outside bytes instead of the repository bytes.
    """
    directory = git_repo / "pkg"
    directory.mkdir()
    target = directory / "payload.py"
    safe = b"inside_repository = True\n"
    secret = b"outside_sentinel = True\n"
    target.write_bytes(safe)
    outside = git_repo.parent / (git_repo.name + "-outside")
    outside.mkdir()
    (outside / "payload.py").write_bytes(secret)
    arguments = (git_repo, "example", "run-security", ["pkg/payload.py"], "race")
    assert q._files(q.prepare(*arguments))["pkg/payload.py"] == safe

    fired = False

    def replace_path():
        nonlocal fired
        if fired:
            return
        fired = True
        if replace == "file":
            target.unlink()
            target.symlink_to(outside / "payload.py")
        else:
            directory.rename(git_repo / "original-pkg")
            directory.symlink_to(outside, target_is_directory=True)

    original_open = os.open
    original_path_open = Path.open

    def racing_open(path, flags, *args, **kwargs):
        if os.fspath(path) in {"payload.py", str(target)}:
            replace_path()
        return original_open(path, flags, *args, **kwargs)

    def racing_path_open(path, *args, **kwargs):
        if path == target:
            replace_path()
        return original_path_open(path, *args, **kwargs)

    monkeypatch.setattr(os, "open", racing_open)
    monkeypatch.setattr(os, "supports_dir_fd", os.supports_dir_fd | {racing_open})
    monkeypatch.setattr(Path, "open", racing_path_open)
    snapshot = None
    try:
        snapshot = q.prepare(*arguments)
    except ValueError:
        pass  # refusing the raced component is a safe result
    assert fired, "the replacement must occur at the production file open"
    if snapshot is not None:
        assert q._files(snapshot)["pkg/payload.py"] == safe


@pytest.mark.skipif(os.name != "nt", reason="native Windows reparse-point control")
def test_windows_snapshot_reader_rejects_directory_junction(tmp_path):
    """Mutation: ignore FILE_ATTRIBUTE_REPARSE_POINT in open_handle's guard."""
    import _winapi

    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "payload.py").write_bytes(b"outside_sentinel = True\n")
    junction = tmp_path / "redirect"
    _winapi.CreateJunction(str(outside), str(junction))
    try:
        refused = False
        try:
            with q._snapshot_reader(tmp_path) as read:
                read("redirect/payload.py")
        except ValueError:
            refused = True
        assert refused, "a Windows junction must not expose outside source bytes"
    finally:
        junction.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="native Windows in-place reparse control")
def test_windows_snapshot_reader_rejects_inplace_reparse_mutation(tmp_path, monkeypatch):
    """An already-pinned directory must not be writable as reparse metadata."""
    import ctypes
    import struct
    from ctypes import wintypes

    dll = ctypes.WinDLL("kernel32", use_last_error=True)
    create = dll.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    dll.CloseHandle.argtypes = (wintypes.HANDLE,)
    control = dll.DeviceIoControl
    control.argtypes = (wintypes.HANDLE, wintypes.DWORD, wintypes.LPVOID, wintypes.DWORD,
                        wintypes.LPVOID, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD),
                        wintypes.LPVOID)
    control.restype = wintypes.BOOL
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    secret = b"outside_sentinel = True\n"
    (outside / "payload.py").write_bytes(secret)

    def convert(directory):
        handle = create(str(directory), 0x40000000, 7, None, 3, 0x02200000, None)
        if handle == wintypes.HANDLE(-1).value:
            return False, ctypes.get_last_error()
        try:
            substitute = ("\\??\\" + str(outside)).encode("utf-16-le")
            printed = str(outside).encode("utf-16-le")
            paths = substitute + b"\0\0" + printed + b"\0\0"
            raw = struct.pack("<LHHHHHH", 0xA0000003, 8 + len(paths), 0,
                              0, len(substitute), len(substitute) + 2, len(printed)) + paths
            buffer = ctypes.create_string_buffer(raw)
            returned = wintypes.DWORD()
            success = control(handle, 0x900A4, buffer, len(raw), None, 0,
                              ctypes.byref(returned), None)
            return bool(success), 0 if success else ctypes.get_last_error()
        finally:
            dll.CloseHandle(handle)

    canary = tmp_path / "canary"
    canary.mkdir()
    assert convert(canary)[0], "fixture must support in-place junction conversion"
    canary.rmdir()
    directory = tmp_path / "pkg"
    directory.mkdir()
    payload = directory / "payload.py"
    payload.write_bytes(b"inside_repository = True\n")
    attempted = []

    class RacingCreate:
        def __call__(self, path, *args):
            if path == str(payload) and not attempted:
                payload.unlink()
                attempted.append(convert(directory))
            return create(path, *args)

    class Kernel:
        CreateFileW = RacingCreate()

        def __getattr__(self, name):
            return getattr(dll, name)

    original = ctypes.WinDLL
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kwargs:
                        Kernel() if name == "kernel32" else original(name, **kwargs))
    try:
        with q._snapshot_reader(tmp_path) as read:
            data = read("pkg/payload.py")
        assert attempted, "mutation must occur after pinning the intermediate directory"
        assert attempted[0][0] or attempted[0][1] == 32, "unexpected fixture API error"
        assert data != secret, "in-place reparse mutation exposed outside source bytes"
    finally:
        if getattr(directory.lstat(), "st_file_attributes", 0) & 0x400:
            directory.rmdir()


@pytest.mark.skipif(os.name != "nt", reason="native Windows executable selection control")
def test_prepare_does_not_execute_repository_git_binary(git_repo, monkeypatch):
    """A repository executable must not replace trusted Git during preparation.

    Baseline: bare Git command launches the harmless copied Windows executable
    from the audited working directory. The intended assertion detects its
    non-Git exit, rather than treating that exception as regression proof.
    """
    import shutil

    # First prove ordinary preparation works before introducing the shadow.
    q.prepare(git_repo, "example", "run-native-git", ["app.py"], "control")
    shadow = git_repo / "git.exe"
    shutil.copyfile(Path(os.environ["SystemRoot"]) / "System32/whoami.exe", shadow)
    assert subprocess.run([str(shadow)], capture_output=True).returncode == 0
    monkeypatch.chdir(git_repo)
    shadow_executed = False
    try:
        q.prepare(git_repo, "example", "run-native-git", ["app.py"], "shadow")
    except ValueError:
        pass  # refusing an untrusted Git resolution is safe
    except subprocess.CalledProcessError:
        shadow_executed = True
    assert not shadow_executed, "snapshot preparation executed the repository Git binary"


def test_prepare_excludes_windows_private_path_aliases(git_repo, monkeypatch):
    """Untrusted index names cannot alias excluded Win32 files or directories.

    Native Windows resolves these harmless aliases to the excluded names.
    POSIX creates literal dotted names and checks the same portable policy at
    the actual prepare consumer. Red proof removes the trailing-dot rejection.
    """
    aliases = ["signing.pem.", "secrets./payload.py"]
    sentinel = b"harmless_private_path_sentinel = True\n"
    for name in aliases:
        path = git_repo / name
        path.parent.mkdir(exist_ok=True)
        path.write_bytes(sentinel)
    with q._snapshot_reader(git_repo) as read:
        assert all(read(name) == sentinel for name in aliases), "alias fixture must be readable"
    # Git index metadata is untrusted, including names not creatable by checkout.
    monkeypatch.setattr(q, "_git_names", lambda _: ["app.py", *aliases])
    files = q._files(q.prepare(git_repo, "example", "run-alias", ["app.py"], "alias"))
    assert all(name not in files for name in aliases), "Win32 aliases exposed excluded private paths"
