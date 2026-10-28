"""Local auditor context through the shared RE-call reader, without a hosted trust claim.

The reader is operator-installed code, never imported from the audited checkout.
Only this adapter and its CLI belong to CCA; admission, matching and rendering stay
in recall_hooks.auditor_brief. No network or indexing operation is performed here.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from . import archive_io
from .anchors import code_sha, well_formed_file

HEADING = "LOCAL AUDITOR MEMORY (context, not findings, not instructions)"
MARKER = "<!-- recall:auditor-brief -->"
MAX_OUTPUT = 32000  # worst-case JSON escaping of a bounded 4000-character block
MAX_BLOCK = 4000
_CHILD = """
import sys
sys.path[:] = [p for p in sys.path if p]  # Python 3.10 equivalent of -P for -c
import json, time
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, sys.argv[1])
request = json.load(sys.stdin)
try:
    from recall_hooks import auditor_brief as brief
    deadline = time.monotonic() + 20
    if request['anchors']:
        from recall import records_files
        from recall.records import RECORD_KINDS, MAX_QUERY_ANCHORS
        repo, changed = brief.scope(request['root'], '', deadline=deadline)
        anchors = list({(a['file'], a['symbol']): a
                        for a in changed + request['anchors']}.values())
        if len(anchors) > MAX_QUERY_ANCHORS:
            raise ValueError('explicit scope exceeds the reader anchor limit')
        directory = brief.memo_directory()
        records = records_files.load_memo_records(
            directory, cache_file=brief.cache_path(directory), deadline=deadline - .15)
        now = datetime.now(timezone.utc)
        entries = records_files.match_memo_records(
            records, repo=repo, anchors=anchors, kinds=['bug', 'lesson'], now=now)
        retractions = {m.file for m in records
                       if m.record.get('kind') in RECORD_KINDS
                       and m.record.get('kind').endswith('_retraction')}
        hidden = frozenset(m.file for m in records if m.record.get('kind') == 'false_positive')
        block = brief.render(entries, now=now, retraction_files=retractions, unnamed_files=hidden)
    else:
        from recall_hooks.auditor_brief import build_block
        block = build_block(request['root'], request['prompt'], deadline=time.monotonic() + 20)
    result = {'status': 'ok' if block else 'empty', 'block': block}
except Exception as exc:
    result = {'status': 'unavailable', 'block': '', 'reason': type(exc).__name__}
print(json.dumps(result, ensure_ascii=False))
"""


def _file_anchors(root: Path, files: list[str]) -> list[dict[str, str | None]]:
    """Structured file scopes, with CCA's pinned, bounded reader and canonical hash."""
    if len(files) > 200:
        raise ValueError("explicit scope exceeds the reader anchor limit")
    anchors = []
    for file in files:
        file = file.replace("\\", "/")
        if not well_formed_file(file):
            raise ValueError("scope must use repository-relative files")
        try:
            digest = code_sha(archive_io.read(root / file, 1_000_000).decode("utf-8"))
        except (OSError, ValueError):
            digest = "unavailable"  # the shared query contract, never an exact hash match
        anchors.append({"file": file, "symbol": None, "code_sha": digest})
    return anchors


def build(root: Path, files: list[str]) -> dict[str, str]:
    """Bounded, read-only local context. An unavailable reader is distinct from no matches."""
    reader = Path(os.environ.get("CCA_RECALL_ROOT") or Path.home() / ".claude" / "hooks")
    prompt = ""  # explicit files travel as structured anchors, never prose path tokens
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    try:
        if not (reader / "recall_hooks" / "auditor_brief.py").is_file():
            raise OSError("operator reader is not installed")
        anchors = _file_anchors(root, files)
        safe_path = ["-P"] if sys.version_info >= (3, 11) else []
        completed = subprocess.run(
            [sys.executable, *safe_path, "-X", "utf8", "-c", _CHILD, str(reader.resolve())],
            input=json.dumps({"root": str(root.resolve()), "prompt": prompt, "anchors": anchors}),
            text=True, encoding="utf-8", capture_output=True, timeout=25,
            cwd=Path.home(), env=environment, check=False,
        )
        if completed.returncode != 0 or len(completed.stdout) > MAX_OUTPUT:
            raise ValueError("reader process refused")
        value = json.loads(completed.stdout)
        if (not isinstance(value, dict) or value.get("status") not in {"ok", "empty", "unavailable"}
                or not isinstance(value.get("block"), str)
                or len(value["block"]) > MAX_BLOCK):
            raise ValueError("invalid reader result")
        if value["status"] == "ok" and MARKER not in value["block"]:
            raise ValueError("invalid reader block")
        return {key: value[key] for key in ("status", "block", "reason")
                if isinstance(value.get(key), str)}
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return {"status": "unavailable", "block": "", "reason": type(exc).__name__}


def render(result: dict[str, str]) -> str:
    status = result["status"]
    if status == "ok":
        return (f"{HEADING}\nLocal admitted memo records; hosted trust was not evaluated."
                f"\n\n{result['block']}")
    if status == "empty":
        return f"{HEADING}\n{MARKER}\nNo matching local bug or accepted lesson records."
    if status == "skipped":
        # Step 1's empty PRIOR FIXES form, the exact text a hunt pastes when it reads nothing.
        from .prior import HEADING as PRIOR_FIXES
        return f"{PRIOR_FIXES}\nNo prior CCA fixes recorded for these files."
    return (f"{HEADING}\nLocal memory unavailable ({result.get('reason', 'reader refused')}); "
            "this does not establish that memory is empty. "
            "Continue the audit from current evidence.")
