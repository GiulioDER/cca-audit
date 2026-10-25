"""Bounded local archive reads with every ancestor pinned and links refused."""
from __future__ import annotations

import errno
import os
import stat
from contextlib import contextmanager
from pathlib import Path


class UnsafeArchive(ValueError):
    pass


def _local(path: Path) -> Path:
    raw = str(path)
    if raw.startswith(("\\\\", "//")) or ".." in path.parts:
        raise UnsafeArchive("archive must be a local path without parent traversal")
    absolute = Path(os.path.abspath(path))
    if str(absolute).startswith(("\\\\", "//")):
        raise UnsafeArchive("remote archive refused")
    return absolute


@contextmanager
def _opened(path: Path, directory: bool):
    path = _local(path)
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        create = kernel.CreateFileW
        create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                           ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        create.restype = wintypes.HANDLE
        close = kernel.CloseHandle
        close.argtypes = [wintypes.HANDLE]
        info = kernel.GetFileInformationByHandleEx
        info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        filetype = kernel.GetFileType
        filetype.argtypes = [wintypes.HANDLE]
        drive = kernel.GetDriveTypeW
        drive.argtypes = [wintypes.LPCWSTR]
        if drive(path.anchor) != 3:  # fixed local disk only; no mapped network drive
            raise UnsafeArchive("archive is not on a fixed local disk")
        handles = []
        fd = None
        try:
            parts = [Path(path.anchor)]
            for part in path.parts[1:]:
                parts.append(parts[-1] / part)
            for index, component in enumerate(parts):
                isdir = index < len(parts) - 1 or directory
                handle = create(str(component), 0x80 if isdir else 0x80000000,
                                1, None, 3, 0x02200000, None)
                if handle == ctypes.c_void_p(-1).value:
                    raise OSError(ctypes.get_last_error(), "cannot pin archive component")
                handles.append(handle)
                attrs = (wintypes.DWORD * 2)()
                if not info(handle, 9, ctypes.byref(attrs), ctypes.sizeof(attrs)):
                    raise OSError(ctypes.get_last_error(), "cannot inspect archive component")
                if attrs[0] & 0x400 or bool(attrs[0] & 0x10) != isdir:
                    raise UnsafeArchive("linked or wrong-type archive component")
                if not isdir and filetype(handle) != 1:
                    raise UnsafeArchive("archive is not a disk file")
            if directory:
                yield path
            else:
                fd = msvcrt.open_osfhandle(handles[-1], os.O_RDONLY | os.O_BINARY)
                handles.pop()  # CRT now owns the final handle
                yield fd
        finally:
            if fd is not None:
                os.close(fd)
            for handle in reversed(handles):
                close(handle)
    else:
        fds = []
        try:
            fds.append(os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY))
            for index, part in enumerate(path.parts[1:]):
                isdir = index < len(path.parts) - 2 or directory
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                if isdir:
                    flags |= os.O_DIRECTORY
                try:
                    fd = os.open(part, flags, dir_fd=fds[-1])
                except OSError as exc:
                    # O_NOFOLLOW reports ELOOP for a file symlink, but Linux
                    # reports ENOTDIR with O_DIRECTORY. Both are unsafe paths.
                    if exc.errno in (errno.ELOOP, errno.ENOTDIR):
                        raise UnsafeArchive("linked or wrong-type archive component") from None
                    raise
                fds.append(fd)
            yield fds[-1]
        finally:
            for fd in reversed(fds):
                os.close(fd)


def read(path: Path, limit: int) -> bytes:
    """Read at most limit+1 bytes from a verified regular local file."""
    with _opened(path, False) as fd:
        if not isinstance(fd, int):
            raise UnsafeArchive("archive handle is not a file descriptor")
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise UnsafeArchive("archive is not a regular file")
        chunks = []
        remaining = limit + 1
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
        if len(data) > limit:
            raise UnsafeArchive("archive file exceeds recovery limit")
        return data


def directories(root: Path) -> list[Path]:
    """Enumerate only local directories, refusing linked roots before enumeration."""
    with _opened(root, True) as pinned:
        names = os.listdir(pinned)
        result = []
        for name in names:
            path = root / name
            try:
                with _opened(path, True):
                    result.append(path)
            except (OSError, UnsafeArchive):
                continue
        return sorted(result)
