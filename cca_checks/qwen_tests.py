"""Signed Qwen test receipts. No repository command runs on the client.

The independently pinned worker key authenticates provenance, not the semantic
honesty of repository tests. The optional cryptography dependency is imported
only at verification time. A missing verifier is unavailable, never a pass.
"""
from __future__ import annotations

import argparse
import base64
import gzip
import hashlib
import io
import json
import os
import queue
import re
import stat
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request
from contextlib import contextmanager
from pathlib import Path

from .toolpath import resolve_tool

SCHEMA = "qwen-tests-v1"
MAX_ARCHIVE = 16 * 1024 * 1024
MAX_EXPANDED = 64 * 1024 * 1024
MAX_FILE = 2 * 1024 * 1024
_GIT_ENUM_TIMEOUT = 30.0
FORBIDDEN = {".git", ".ssh", ".aws", ".azure", ".claude", ".codex", "node_modules",
             "__pycache__", ".venv", "venv", "secrets", "credentials", "token_store",
             ".pytest_cache"}


def canonical(value: dict) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()


def _safe_name(name: str) -> bool:
    if not re.fullmatch(r"[A-Za-z0-9_./-]{1,512}", name) or name.startswith(("/", "-")):
        return False
    for p in name.split("/"):
        p = p.lower()
        if p in FORBIDDEN | {"", ".", "..", ".env"} or p.startswith(".env.") or p.endswith("."):
            return False
        if p.endswith((".key", ".pem", ".p12", ".pfx", ".pyc")):
            return False
    return True


def _git_command(root: Path, *args: str) -> list[str]:
    # The audited root and the caller's cwd must not supply the executable.
    binary = resolve_tool("git", cwd=str(root))
    if binary is None or resolve_tool("git") != binary:
        raise ValueError("trusted Git executable is unavailable")
    return [binary, "-c", "core.fsmonitor=false", "-c", "core.hooksPath=" + os.devnull,
            "-C", str(root), *args]


def _git(root: Path, *args: str) -> bytes:
    return subprocess.run(_git_command(root, *args), check=True,
                          capture_output=True, timeout=30).stdout


@contextmanager
def _snapshot_reader(root: Path):
    """Pin the root and open every component without following a link.

    POSIX lookup stays relative to held descriptors. Windows holds each opened
    directory without write/delete-sharing, so its absolute child path cannot be
    redirected while the corresponding handles are alive.
    """
    if os.name == "nt":
        with _windows_snapshot_reader(root) as read:
            yield read
        return
    if os.open not in os.supports_dir_fd or not hasattr(os, "O_NOFOLLOW"):
        raise ValueError("safe snapshot reads are unavailable on this platform")
    directories = []
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        parent = os.open(root.anchor, directory_flags)
        directories.append(parent)
        for component in root.parts[1:]:
            parent = os.open(component, directory_flags, dir_fd=parent)
            directories.append(parent)

        def read(name):
            held = []
            try:
                parent = directories[-1]
                parts = name.split("/")
                for component in parts[:-1]:
                    parent = os.open(component, directory_flags, dir_fd=parent)
                    held.append(parent)
                flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
                descriptor = os.open(parts[-1], flags, dir_fd=parent)
                with os.fdopen(descriptor, "rb") as stream:
                    info = os.fstat(stream.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ValueError("snapshot contains a non-file")
                    if info.st_size > MAX_FILE:
                        raise ValueError("snapshot exceeds file size limit")
                    return stream.read(MAX_FILE + 1)
            except FileNotFoundError:
                return None  # tracked deletion in the working tree
            except OSError as exc:
                raise ValueError("snapshot path is unsafe or unreadable") from exc
            finally:
                for descriptor in reversed(held):
                    os.close(descriptor)
        yield read
    except OSError as exc:
        raise ValueError("snapshot root is unsafe or unreadable") from exc
    finally:
        for descriptor in reversed(directories):
            os.close(descriptor)


@contextmanager
def _windows_snapshot_reader(root: Path):
    """Reject reparse points and pin directories against mutation or replacement."""
    if sys.platform != "win32":
        raise ValueError("Windows snapshot handles are unavailable on this platform")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    if root.drive.startswith("\\\\"):
        raise ValueError("safe snapshot reads require a local Windows drive")
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    create = kernel.CreateFileW
    create.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                       wintypes.LPVOID, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    create.restype = wintypes.HANDLE
    close = kernel.CloseHandle
    close.argtypes = (wintypes.HANDLE,)
    close.restype = wintypes.BOOL
    attributes = kernel.GetFileInformationByHandleEx
    attributes.argtypes = (wintypes.HANDLE, ctypes.c_int, wintypes.LPVOID, wintypes.DWORD)
    attributes.restype = wintypes.BOOL
    file_type = kernel.GetFileType
    file_type.argtypes = (wintypes.HANDLE,)
    file_type.restype = wintypes.DWORD

    class AttributeTag(ctypes.Structure):
        _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]

    def open_handle(path, directory):
        # Directories deny write/delete sharing to prevent in-place reparse changes.
        # OPEN_REPARSE_POINT exposes
        # the component itself, including junctions, for the attribute check.
        handle = create(str(path), 0x80000000, 1 if directory else 3,
                        None, 3, 0x02200000, None)
        if handle == wintypes.HANDLE(-1).value:
            error = ctypes.get_last_error()
            if error in (2, 3):
                raise FileNotFoundError(error, "snapshot component was deleted")
            raise ValueError("snapshot path is unsafe or unreadable")
        try:
            info = AttributeTag()
            if not attributes(handle, 9, ctypes.byref(info), ctypes.sizeof(info)):
                raise ValueError("snapshot file attributes are unavailable")
            if info.attributes & 0x400 or bool(info.attributes & 0x10) != directory:
                raise ValueError("snapshot contains a reparse point or non-file")
            if not directory and file_type(handle) != 1:
                raise ValueError("snapshot contains a non-file")
            return handle
        except BaseException:
            close(handle)
            raise

    held_root = []
    try:
        current = Path(root.anchor)
        held_root.append(open_handle(current, True))
        for component in root.parts[1:]:
            current /= component
            held_root.append(open_handle(current, True))

        def read(name):
            held = []
            handle = None
            try:
                current = root
                parts = name.split("/")
                for component in parts[:-1]:
                    current /= component
                    held.append(open_handle(current, True))
                handle = open_handle(current / parts[-1], False)
                descriptor = msvcrt.open_osfhandle(handle, os.O_RDONLY | os.O_BINARY)
                handle = None  # the descriptor now owns the Windows handle
                with os.fdopen(descriptor, "rb") as stream:
                    if os.fstat(stream.fileno()).st_size > MAX_FILE:
                        raise ValueError("snapshot exceeds file size limit")
                    return stream.read(MAX_FILE + 1)
            except FileNotFoundError:
                return None
            finally:
                if handle is not None:
                    close(handle)
                for handle in reversed(held):
                    close(handle)
        yield read
    finally:
        for handle in reversed(held_root):
            close(handle)


def _git_names(root: Path) -> list[str]:
    limit, maximum = 10000, 10000 * 513
    proc = subprocess.Popen(
        _git_command(root, "ls-files", "-z", "--cached", "--others", "--exclude-standard"),
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=0,
    )
    names, pending = set(), bytearray()
    total = count = 0
    chunks: queue.Queue[bytes] = queue.Queue(maxsize=2)
    stop, read_failed = threading.Event(), threading.Event()

    def offer(chunk):
        while not stop.is_set():
            try:
                chunks.put(chunk, timeout=.05)
                return
            except queue.Full:
                pass

    def pump():
        assert proc.stdout is not None
        try:
            while not stop.is_set():
                chunk = proc.stdout.read(8192)
                offer(chunk)
                if not chunk:
                    return
        except (OSError, ValueError):
            read_failed.set()
            offer(b"")

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    deadline = time.monotonic() + _GIT_ENUM_TIMEOUT
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ValueError("snapshot enumeration timed out")
            try:
                chunk = chunks.get(timeout=remaining)
            except queue.Empty:
                raise ValueError("snapshot enumeration timed out") from None
            if not chunk:
                break
            total += len(chunk)
            if total > maximum:
                raise ValueError("snapshot enumeration exceeds byte limit")
            pending.extend(chunk)
            while b"\0" in pending:
                raw, _, rest = pending.partition(b"\0")
                pending = bytearray(rest)
                count += 1
                if count > limit or len(raw) > 512:
                    raise ValueError("snapshot enumeration exceeds entry limit")
                names.add(raw.decode("utf-8"))
            if len(pending) > 512:
                raise ValueError("snapshot enumeration exceeds path limit")
        try:
            code = proc.wait(timeout=max(.001, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            raise ValueError("snapshot enumeration timed out") from None
        if pending or read_failed.is_set() or code != 0:
            raise ValueError("snapshot enumeration did not complete")
        return sorted(names)
    finally:
        stop.set()
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=30)
        reader.join(timeout=5)
        if proc.stdout is not None:
            proc.stdout.close()


def prepare(root: Path, project: str, run_id: str, selectors: list[str], key: str) -> dict:
    """Package actual working bytes deterministically, excluding private/generated paths."""
    root = root.resolve()
    if Path(_git(root, "rev-parse", "--show-toplevel").decode().strip()).resolve() != root:
        raise ValueError("prepare requires the target Git root")
    names = _git_names(root)
    buffer = io.BytesIO()
    total = count = 0
    with _snapshot_reader(root) as read, \
            gzip.GzipFile(fileobj=buffer, mode="wb", mtime=0) as compressed:
        with tarfile.open(fileobj=compressed, mode="w|", format=tarfile.USTAR_FORMAT) as archive:
            for name in names:
                if not _safe_name(name):
                    continue
                data = read(name)
                if data is None:
                    continue  # tracked deletion in the working tree
                total += len(data)
                count += 1
                if len(data) > MAX_FILE or total > MAX_EXPANDED or count > 10000:
                    raise ValueError("snapshot exceeds file or expanded size limit")
                member = tarfile.TarInfo(name)
                member.size, member.mode, member.mtime = len(data), 0o444, 0
                archive.addfile(member, io.BytesIO(data))
    content = buffer.getvalue()
    if not count or len(content) > MAX_ARCHIVE:
        raise ValueError("empty or oversized snapshot")
    return {"project": project, "snapshot_b64": base64.b64encode(content).decode(),
            "snapshot_sha256": hashlib.sha256(content).hexdigest(),
            "revision": _git(root, "rev-parse", "HEAD").decode().strip(),
            "cca_run_id": run_id, "selectors": selectors,
            "idempotency_key": key, "profile": "pytest"}


def read_json(path: Path, maximum: int = 24 * 1024 * 1024) -> dict:
    if path.stat().st_size > maximum:
        raise ValueError("JSON input exceeds size limit")
    value = json.loads(path.read_bytes())
    if not isinstance(value, dict):
        raise ValueError("JSON input must be an object")
    return value


def load_operator_config(path: Path, root: Path) -> dict:
    """Never let the audited repository select its own endpoint or signing key."""
    path = path.resolve()
    if path == root.resolve() or root.resolve() in path.parents:
        raise ValueError("Qwen configuration must be outside the audited repository")
    value = read_json(path, 8192)
    if set(value) != {"url", "token_env", "public_key"}:
        raise ValueError("invalid operator Qwen configuration")
    key = Path(value["public_key"])
    if not key.is_absolute() or root.resolve() in key.resolve().parents:
        raise ValueError("worker verification key must be pinned outside the repository")
    if not isinstance(value["token_env"], str) or not re.fullmatch(
            r"[A-Z][A-Z0-9_]{0,127}", value["token_env"]):
        raise ValueError("invalid token environment variable")
    if not isinstance(value["url"], str) or not re.fullmatch(
            r"https?://[A-Za-z0-9.:-]+/mcp", value["url"]):
        raise ValueError("invalid Qwen MCP endpoint")
    return value


class Client:
    """Bounded Streamable HTTP MCP client using an operator-pinned endpoint."""
    def __init__(self, config: dict):
        self.config = config
        self.session: str | None = None
        self.serial = 0
        token = os.environ.get(config["token_env"], "")
        if not token:
            raise ValueError("Qwen token is unavailable in the configured environment")
        self.headers = {"Authorization": "Bearer " + token,
                        "Accept": "application/json, text/event-stream",
                        "Content-Type": "application/json"}

    def _rpc(self, method, params, notification=False):
        self.serial += 1
        data = {"jsonrpc": "2.0", "method": method, "params": params}
        if not notification:
            data["id"] = self.serial
        headers = dict(self.headers)
        if self.session:
            headers["Mcp-Session-Id"] = self.session
            headers["MCP-Protocol-Version"] = self.headers.get("MCP-Protocol-Version", "2024-11-05")
        request = urllib.request.Request(self.config["url"], data=canonical(data), headers=headers)
        # No redirects or ambient proxy: never forward the bearer credential to
        # a destination selected by an HTTP response or the shell environment.
        class NoRedirect(urllib.request.HTTPRedirectHandler):
            def redirect_request(self, req, fp, code, msg, headers, newurl):
                return None
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
        with opener.open(request, timeout=30) as response:
            self.session = response.headers.get("Mcp-Session-Id", self.session)
            negotiated = response.headers.get("MCP-Protocol-Version")
            if negotiated:
                self.headers["MCP-Protocol-Version"] = negotiated
            body = response.read(256 * 1024 + 1)
        if notification:
            return None
        if len(body) > 256 * 1024:
            raise ValueError("MCP response exceeds limit")
        if body.lstrip().startswith(b"{"):
            reply = json.loads(body)
        else:
            messages = [json.loads(line[5:].strip()) for line in body.splitlines()
                        if line.startswith(b"data:")]
            reply = next((m for m in messages if m.get("id") == self.serial), {})
        if reply.get("id") != self.serial or "error" in reply or "result" not in reply:
            raise ValueError("MCP returned an invalid or failed response")
        return reply["result"]

    def call(self, name: str, arguments: dict) -> dict:
        if self.session is None:
            initialized = self._rpc("initialize", {
                "protocolVersion": "2024-11-05", "capabilities": {},
                "clientInfo": {"name": "cca-qwen-tests", "version": "1"},
            })
            if not isinstance(initialized, dict):
                raise ValueError("MCP initialization result must be an object")
            version = initialized.get("protocolVersion")
            if version not in {"2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25"}:
                raise ValueError("unsupported MCP protocol version")
            self.headers["MCP-Protocol-Version"] = version
            self._rpc("notifications/initialized", {}, notification=True)
        result = self._rpc("tools/call", {"name": name, "arguments": arguments})
        if not isinstance(result, dict):
            raise ValueError("MCP tool result must be an object")
        if result.get("isError"):
            raise ValueError("Qwen refused or could not execute the test request")
        value = result.get("structuredContent")
        if value is None:
            texts = [c["text"] for c in result.get("content", []) if c.get("type") == "text"]
            value = json.loads(texts[0]) if len(texts) == 1 else None
        if not isinstance(value, dict) or value.get("ok") is not True:
            raise ValueError("Qwen returned no successful job response")
        return value


def verify(envelope: dict, request: dict, public_key: Path) -> dict:
    try:
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
    except ImportError as exc:
        raise ValueError(
            "remote receipt verification unavailable; install cca-audit[remote-tests]"
        ) from exc
    receipt = envelope.get("receipt")
    if not isinstance(receipt, dict) or receipt.get("schema") != SCHEMA:
        raise ValueError("no supported terminal receipt")
    key = serialization.load_pem_public_key(public_key.read_bytes())
    if not isinstance(key, Ed25519PublicKey):
        raise ValueError("pinned key must be Ed25519")
    key_id = hashlib.sha256(key.public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)).hexdigest()
    if receipt.get("key_id") != key_id:
        raise ValueError("worker signing identity mismatch")
    try:
        key.verify(base64.b64decode(envelope["signature"], validate=True), canonical(receipt))
    except Exception as exc:
        raise ValueError("invalid worker receipt signature") from exc
    for field in ("project", "profile", "cca_run_id", "revision", "snapshot_sha256", "selectors"):
        if receipt.get(field) != request.get(field) or field not in request:
            raise ValueError("receipt does not match expected " + field)
    snapshot = base64.b64decode(request["snapshot_b64"], validate=True)
    if (len(snapshot) > MAX_ARCHIVE or
            hashlib.sha256(snapshot).hexdigest() != request["snapshot_sha256"]):
        raise ValueError("expected snapshot digest is invalid")
    if receipt.get("state") not in {"passed", "failed", "inconclusive", "timed_out",
                                     "cancelled", "infrastructure_error"}:
        raise ValueError("job has not completed")
    roots = receipt.get("test_roots")
    if not isinstance(roots, list) or not 1 <= len(roots) <= 8 or any(
            not isinstance(root, str) or not _safe_name(root) for root in roots):
        raise ValueError("receipt has no approved test roots")
    if any(not any(selector.split("::", 1)[0] == root or
                   selector.split("::", 1)[0].startswith(root + "/") for root in roots)
           for selector in receipt["selectors"]):
        raise ValueError("receipt selector is outside approved test roots")
    report = receipt.get("report")
    if receipt["state"] in {"passed", "failed"}:
        if not isinstance(report, dict):
            raise ValueError("completed test receipt has no structured report")
        for name in ("collected", "passed", "failed", "skipped", "errors", "exit_code"):
            if type(report.get(name)) is not int or report[name] < 0:
                raise ValueError("invalid test counts")
        if report["collected"] < 1 or report["passed"] + report["failed"] > report["collected"]:
            raise ValueError("empty or inconsistent test counts")
        if receipt["state"] == "passed" and (report["exit_code"] != 0 or report["passed"] < 1
                                              or report["failed"] or report["errors"]):
            raise ValueError("passing receipt contradicts its test counts")
        if receipt["state"] == "failed" and (report["exit_code"] != 1
                                              or report["failed"] < 1 or report["errors"]):
            raise ValueError("failing receipt contradicts its test counts")
    return receipt


def _files(request: dict) -> dict[str, bytes]:
    content = base64.b64decode(request["snapshot_b64"], validate=True)
    result: dict[str, bytes] = {}
    total = 0
    maximum_tar = MAX_EXPANDED + 10000 * 1024 + 10240
    with gzip.GzipFile(fileobj=io.BytesIO(content)) as compressed:
        raw = compressed.read(maximum_tar + 1)
    if len(raw) > maximum_tar:
        raise ValueError("expected snapshot exceeds decompressed size limit")
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:") as archive:
        for member in archive:
            if not member.isfile() or not _safe_name(member.name) or member.name in result:
                raise ValueError("invalid expected snapshot member")
            total += member.size
            if not 0 <= member.size <= MAX_FILE or total > MAX_EXPANDED or len(result) >= 10000:
                raise ValueError("expected snapshot exceeds size limit")
            stream = archive.extractfile(member)
            if stream is None:
                raise ValueError("unreadable expected snapshot")
            result[member.name] = stream.read(MAX_FILE + 1)
    return result


def proof(red: dict, red_request: dict, green: dict, green_request: dict,
          public_key: Path) -> str:
    r, g = verify(red, red_request, public_key), verify(green, green_request, public_key)
    if any(r[k] != g[k] for k in (
            "cca_run_id", "project", "selectors", "image", "runner_version", "test_roots")):
        raise ValueError("proof pair differs in run, project, tests or execution image")
    selectors = g["selectors"]
    if len(selectors) != 1 or "::" not in selectors[0]:
        raise ValueError("red proof requires exactly one explicit test node")
    if r["snapshot_sha256"] == g["snapshot_sha256"]:
        raise ValueError("proof baseline and fixed snapshots are identical")
    before, after = _files(red_request), _files(green_request)
    selected = selectors[0].split("::", 1)[0]
    protected = {n for n in set(before) | set(after)
                 if n == selected or n.endswith("conftest.py") or n.startswith("tests/")
                 or any(n == root or n.startswith(root + "/") for root in g["test_roots"])
                 or n in {"pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini"}}
    if any(before.get(n) != after.get(n) for n in protected):
        raise ValueError("test or harness changed between baseline and fixed snapshots")
    if before == after:
        raise ValueError("baseline has no source difference")
    gr = g.get("report") or {}
    if g["state"] != "passed" or any(gr.get(k) != v for k, v in (
            ("collected", 1), ("passed", 1), ("failed", 0), ("skipped", 0), ("errors", 0))):
        return "INCONCLUSIVE"
    if r["state"] == "passed":
        return "TAUTOLOGICAL"
    rr = r.get("report") or {}
    node_hash = hashlib.sha256(selectors[0].encode()).hexdigest()
    if r["state"] == "failed" and all(rr.get(k) == v for k, v in (
            ("collected", 1), ("passed", 0), ("failed", 1), ("skipped", 0), ("errors", 0))) \
            and rr.get("assertion_failures") == [node_hash]:
        return "RED"
    return "INCONCLUSIVE"


def _matches_live(root: Path, request: dict):
    current = prepare(root, request["project"], request["cca_run_id"], request["selectors"],
                      request["idempotency_key"])
    if any(current[k] != request[k] for k in ("snapshot_sha256", "revision")):
        raise ValueError("receipt snapshot does not match the current audited working tree")


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    sub = parser.add_subparsers(dest="action", required=True)
    prep = sub.add_parser("prepare", help="write a deterministic bounded MCP request")
    for flag in ("project", "run-id", "idempotency-key", "out"):
        prep.add_argument("--" + flag, required=True)
    prep.add_argument("--selector", action="append", required=True)
    for action in ("submit", "status", "cancel", "verify", "proof"):
        p = sub.add_parser(action)
        p.add_argument("--config", required=True, type=Path)
        p.add_argument("--out", type=Path)
        if action in {"submit", "verify"}:
            p.add_argument("--request", required=True, type=Path)
        if action in {"status", "cancel"}:
            p.add_argument("--job-id", required=True)
        if action == "verify":
            p.add_argument("--receipt", required=True, type=Path)
            p.add_argument("--allow-skips", type=int, default=0)
        if action == "proof":
            p.add_argument("--manifest", required=True, type=Path)
            p.add_argument("--record", action="store_true")
    a = parser.parse_args(argv)
    try:
        if a.action == "prepare":
            result = prepare(a.root, a.project, a.run_id, a.selector, a.idempotency_key)
            Path(a.out).write_bytes(canonical(result) + b"\n")
            print(json.dumps({"snapshot_sha256": result["snapshot_sha256"], "request": a.out}))
            return 0
        config = load_operator_config(a.config, a.root)
        key = Path(config["public_key"])
        code = 0
        if a.action == "submit":
            request = read_json(a.request)
            _matches_live(a.root, request)
            result = Client(config).call("test_start", request)
        elif a.action in {"status", "cancel"}:
            result = Client(config).call("test_" + a.action, {"job_id": a.job_id})
        elif a.action == "verify":
            request = read_json(a.request)
            _matches_live(a.root, request)
            receipt = verify(read_json(a.receipt, 128 * 1024), request, key)
            report = receipt.get("report") or {}
            accepted = (receipt["state"] == "passed" and a.allow_skips >= 0
                        and report.get("skipped", 0) <= a.allow_skips)
            result = {"tests_accepted": accepted, "receipt": receipt,
                      "lint": "not covered; run the separate lint gate"}
            code = 0 if accepted else 2
        else:
            from .plugin.tools import cca_tautology_check as taut

            rows = read_json(a.manifest, 128 * 1024).get("proofs")
            if not isinstance(rows, list) or not 1 <= len(rows) <= 32:
                raise ValueError("proof manifest requires one to 32 proof pairs")
            results = []
            identities = set()
            state = None
            for row in rows:
                if not isinstance(row, dict) or set(row) != {
                        "finding", "red_receipt", "red_request", "green_receipt", "green_request"}:
                    raise ValueError("invalid proof manifest row")
                if (not re.fullmatch(r"[A-Za-z0-9_.-]{1,96}", row["finding"]) or
                        row["finding"] in identities):
                    raise ValueError("invalid or duplicate proof finding")
                identities.add(row["finding"])
                red_request, green_request = (
                    read_json(Path(row[k])) for k in ("red_request", "green_request"))
                _matches_live(a.root, green_request)
                if a.record:
                    from . import pipeline_state

                    state = pipeline_state.load(a.root)
                    if state is None or state.run_id != green_request["cca_run_id"]:
                        raise ValueError("proof does not belong to the open CCA run")
                verdict = proof(read_json(Path(row["red_receipt"]), 128 * 1024), red_request,
                                read_json(Path(row["green_receipt"]), 128 * 1024),
                                green_request, key)
                results.append(taut.ProofResult(row["finding"], green_request["selectors"][0],
                                               verdict, "signed Qwen test worker receipt pair"))
            result = {"proofs": [{"finding": r.finding, "nodeid": r.nodeid,
                                   "verdict": r.verdict} for r in results]}
            code = 0 if all(r.verdict == "RED" for r in results) else 2
            if a.record:
                # Reuse CCA's writer and gate; never manufacture a separate L5.6.
                if a.root.resolve() != Path.cwd().resolve():
                    raise ValueError("record proof from the audited root directory")
                if state is None:
                    raise ValueError("proof does not belong to the open CCA run")
                if taut._planned_p1() > len(results):
                    raise ValueError("proof manifest does not cover the open run's P1 count")
                taut._save_proofs(results)
                from . import deferred

                saved = deferred.runs_dir(a.root) / state.run_id / "L5.6.json"
                stored = json.loads(saved.read_bytes())
                if any(not any(s.get("finding") == r.finding and s.get("nodeid") == r.nodeid
                               and s.get("verdict") == r.verdict for s in stored) for r in results):
                    raise ValueError("remote proof output was not persisted")
                status, detail = taut._l5_6_entry(results)
                recorded = taut._record(status, detail)
                code = recorded or code
        if a.out:
            a.out.write_bytes(canonical(result) + b"\n")
        print(json.dumps(result, sort_keys=True))
        return code
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, TypeError) as exc:
        # Known policy errors are safe fixed messages. HTTP/OS exception strings
        # may contain private host paths or URLs; keep those out of receipts.
        reason = str(exc) if isinstance(exc, ValueError) else type(exc).__name__
        print(json.dumps({"tests_accepted": False, "error": reason}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
