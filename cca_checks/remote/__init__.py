"""Run semgrep on an operator-configured remote host, for machines that cannot run it.

The host side is runner.py, installed as the only command a dedicated key may start.
This side builds the tar archive the runner reads and calls ssh. Two operations exist,
the version probe and a semgrep scan, and nothing here can send anything else: no
function takes a command, and the archive's member names are fixed.

The destination comes from CCA_REMOTE_SEMGREP in the environment, never from the
repository under audit, and must be a plain ssh name: a value such as
"-oProxyCommand=..." would otherwise be read by ssh as an option.
"""
from __future__ import annotations

import functools
import io
import json
import os
import re
import subprocess
import tarfile

from ..config import TIMEOUT_S
from ..toolpath import PROBE_TIMEOUT_S, resolve_tool
from .runner import MAX_BYTES, PROTOCOL, TARGET_EXTS

ENV = "CCA_REMOTE_SEMGREP"
# Neither part may start with "-": "-F<file>" would make ssh read a config from the cwd,
# which in hunt mode is the audited repository.
_DEST = re.compile(r"[A-Za-z0-9_.][A-Za-z0-9._-]*(@[A-Za-z0-9_.][A-Za-z0-9._-]*)?")
_MARGIN_S = 30


def destination() -> tuple[str | None, str | None]:
    raw = os.environ.get(ENV, "").strip()
    if not raw:
        return None, None
    if not _DEST.fullmatch(raw):
        return None, f"{ENV}={raw!r} is not a plain ssh destination (alias or user@host); ignored"
    return raw, None


def build_request(op: str, files: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as archive:
        entries = {"request.json": json.dumps({"protocol": PROTOCOL, "op": op}).encode()}
        entries.update(files or {})
        for name, data in entries.items():
            info = tarfile.TarInfo(name)
            info.size, info.mode = len(data), 0o600
            archive.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def _ssh_argv(dest: str) -> list[str] | None:
    ssh = resolve_tool("ssh")
    if ssh is None:
        return None
    return [ssh, "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "--", dest]


def _call(dest: str, payload: bytes, timeout: float) -> tuple[dict | None, str | None]:
    argv = _ssh_argv(dest)
    if argv is None:
        return None, "ssh is not on PATH"
    try:
        proc = subprocess.run(argv, input=payload, capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"no answer within {timeout:g}s"
    except OSError as exc:
        return None, f"ssh could not start ({exc})"
    try:
        doc = json.loads(proc.stdout.decode("utf-8", "replace"))
    except ValueError:
        lines = proc.stderr.decode("utf-8", "replace").strip().splitlines()
        tail = lines[-1] if lines else "no output"
        return None, f"no runner answer (ssh exit {proc.returncode}: {tail})"
    if not isinstance(doc, dict):
        return None, "malformed runner answer"
    if not doc.get("ok"):
        return None, f"runner refused: {doc.get('error')}"
    return doc, None


@functools.cache
def remote_semgrep_version(dest: str) -> tuple[str | None, str | None]:
    doc, why = _call(dest, build_request("version"), PROBE_TIMEOUT_S + _MARGIN_S)
    if doc is None:
        return None, why
    version = doc.get("semgrep")
    return (str(version), None) if version else (None, "runner gave no version")


def remote_semgrep(dest: str, rules: bytes, target: bytes,
                   ext: str) -> tuple[dict | None, str | None]:
    if ext not in TARGET_EXTS:
        return None, f"only .py and .rs files are sent to {dest}, not .{ext}"
    if len(rules) + len(target) > MAX_BYTES:
        return None, "larger than 5 MB; not sent"
    files = {"rules.yaml": rules, f"target.{ext}": target}
    doc, why = _call(dest, build_request("semgrep", files), TIMEOUT_S + _MARGIN_S)
    if doc is None:
        return None, why
    result = doc.get("result")
    if not isinstance(result, dict):
        return None, "runner answer has no semgrep result"
    return result, None


def semgrep_status(local_reason: str) -> tuple[str | None, str | None]:
    """Given why local semgrep cannot run, return (why taint stays unavailable, or None;
    what serves it instead, or None)."""
    dest, invalid = destination()
    if invalid:
        return f"{local_reason}; {invalid}", None
    if dest is None:
        return local_reason, None
    version, why = remote_semgrep_version(dest)
    if version is None:
        return f"local: {local_reason}; remote {dest}: {why}", None
    return None, f"semgrep {version} on {dest}"
