"""Helpers for the remote semgrep tests: archives and a fake semgrep. Not collected."""
from __future__ import annotations

import io
import json
import pathlib
import sys
import tarfile

FAKE = r'''
import json, pathlib, sys, time
marker = pathlib.Path({marker!r})
marker.write_text(json.dumps(sys.argv[1:]), encoding="utf-8")
if "--version" in sys.argv:
    print("9.9.9")
    sys.exit(0)
if {mode!r} == "crash":
    sys.exit(2)
if {mode!r} == "sleep":
    time.sleep(30)
print(json.dumps({{"version": "9.9.9", "results": [], "errors": [],
                  "paths": {{"scanned": [sys.argv[-1]]}}}}))
'''


def tar_of(entries: list[tuple[str, bytes, str]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tf:
        for name, data, kind in entries:
            info = tarfile.TarInfo(name)
            if kind == "symlink":
                info.type, info.linkname = tarfile.SYMTYPE, "/etc/passwd"
                tf.addfile(info)
            elif kind == "hardlink":
                info.type, info.linkname = tarfile.LNKTYPE, "rules.yaml"
                tf.addfile(info)
            else:
                info.size = len(data)
                tf.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def request(op: str, files: dict[str, bytes] | None = None, protocol: int = 1) -> bytes:
    head = json.dumps({"protocol": protocol, "op": op}).encode()
    return tar_of([("request.json", head, "file"),
                   *[(n, d, "file") for n, d in (files or {}).items()]])


def fake_semgrep(tmp_path: pathlib.Path, mode: str = "ok") -> tuple[list[str], pathlib.Path]:
    marker = tmp_path / f"semgrep-ran-{mode}.json"
    script = tmp_path / f"fake_semgrep_{mode}.py"
    script.write_text(FAKE.format(marker=str(marker), mode=mode), encoding="utf-8")
    return [sys.executable, str(script)], marker
