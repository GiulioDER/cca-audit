"""What a run's ledger rows need from its checkout, computed when the run is captured.

Nothing downstream of the run has a checkout: `resolve` reads a pending record on another
host, days later, and RE-call never sees the repository. So `pipeline close` computes, per
fixed finding, the anchors of the finding over the fixed code and the commits that introduced
the lines the fix changed, and the record carries them as `run_facts.json`. `forward.py`
turns the commits into `caused_by` bug ids against the ledger.

A fact that could not be computed is left out and the reason kept, so a row lacks it rather
than carrying a guess.
"""
from __future__ import annotations

from pathlib import Path

from .anchors import fixed_anchor
from .caused_by import fix_diff, introducing_commits
from .forward import _files
from .gitread import GitError

NAME = "run_facts.json"
SCHEMA = 1


def _line(value) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def compute(root: Path, base: str, head: str, l2: list, l4: list) -> dict:
    """`{"schema", "base", "head", "findings": {id: {"anchors", "introduced_by", "errors"}}}`."""
    located = {f.get("id"): f for f in l2 if isinstance(f, dict)}
    findings: dict[str, dict] = {}
    diff: str | None = None
    for entry in l4:
        if not isinstance(entry, dict):
            continue
        finding = entry.get("finding") or entry.get("finding_id") or entry.get("fix_id")
        if not isinstance(finding, str) or finding in findings:
            continue
        facts: dict = {"anchors": [], "errors": []}
        where = located.get(finding, {})
        if isinstance(where.get("file"), str) and where["file"].strip():
            anchor = fixed_anchor(root, base, head, where["file"], _line(where.get("line")))
            if anchor is None:
                facts["errors"].append(f"no anchor for {where['file']}")
            else:
                facts["anchors"].append(anchor.to_json())
        files = _files(entry.get("files"))
        try:
            if files and diff is None:
                diff = fix_diff(root, base, head)  # one diff per run, filtered per finding
            facts["introduced_by"] = (introducing_commits(root, base, diff or "", files)
                                      if files else [])
        except GitError as exc:
            facts["errors"].append(f"blame: {exc}")
        findings[finding] = facts
    return {"schema": SCHEMA, "base": base, "head": head, "findings": findings}
