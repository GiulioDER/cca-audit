"""The anchors a `recall_records` query sends, and the ones a Step 2.5b row copies.

Three questions, one per `bugs anchors` mode, all answered over the working tree:

* `for_diff`: the symbols the change touches, for the brief's prior-bugs query. A changed line
  anchors to its innermost enclosing symbol; a deletion to the symbol around the deleted span.
* `for_findings`: the place of each L2 finding, for the PRIOR_FP query.
* `at`: given `file:line` places, for a FALSE_POSITIVE row's `symbol` and `code_sha`.

RE-call refuses more than `MAX_ANCHORS` anchors in one query, so a larger set is cut and the
answer says `truncated`, never silently.
"""
from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path

from .anchors import (
    Anchor,
    anchor_for,
    read_source,
    symbol_sha,
    well_formed_file,
    well_formed_symbol,
)
from .gitread import git

MAX_ANCHORS = 200
_HUNK = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,(\d+))? @@", re.M)


def _ref(ref: str) -> tuple[str, int]:
    file, sep, line = ref.rpartition(":")
    if sep and file and line.strip().isdigit():
        return file, int(line)
    return ref, 0


def at(root: Path, refs: list[str]) -> list[dict]:
    """One object per `file:line`: the ref and its anchor, or null and why."""
    out = []
    for ref in refs:
        file, line = _ref(ref)
        try:
            anchor: dict | None = anchor_for(Path(root), file, line).to_json()
            error = None
        except (OSError, ValueError) as exc:
            anchor, error = None, f"{type(exc).__name__}: {exc}"
        out.append({"ref": ref, "anchor": anchor, **({"error": error} if error else {})})
    return out


def _query(repo: str | None, anchors: list[Anchor]) -> dict:
    unique = list(dict.fromkeys(anchors))
    return {"repo": repo, "anchors": [a.to_json() for a in unique[:MAX_ANCHORS]],
            "truncated": len(unique) > MAX_ANCHORS, "total": len(unique)}


def _findings(path: Path) -> list[dict]:
    value = json.loads(Path(path).read_bytes())
    if not isinstance(value, list):
        raise ValueError(f"{path} is not a JSON array of findings")
    return [f for f in value if isinstance(f, dict)]


def _line(value: object) -> int:
    try:
        return max(int(value or 0), 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def located(root: Path, path: Path) -> list[dict]:
    """The L2 findings in `path`, each with `anchor` set where its place resolves."""
    return locate(root, _findings(path))


def locate(root: Path, findings: list[dict]) -> list[dict]:
    """`findings`, each with `anchor` set where its place resolves in the checkout."""
    out = []
    for finding in findings:
        file = finding.get("file")
        anchor = None
        if isinstance(file, str) and file.strip():
            try:
                anchor = anchor_for(Path(root), file, _line(finding.get("line"))).to_json()
            except (OSError, ValueError):
                anchor = None
        out.append({**finding, "anchor": anchor})
    return out


def for_findings(root: Path, path: Path, repo: str | None) -> dict:
    anchors = [Anchor(**f["anchor"]) for f in located(root, path) if f["anchor"]]
    return _query(repo, anchors)


def for_records(path: Path, repo: str | None) -> dict:
    """The anchors of the bug records in a saved `recall_records` answer: the code join's query.

    Only bug records of `repo`, the ones the block may list; a lesson's or a false positive's
    anchors never reach the brief, so they are never joined.
    """
    from .envelope import load

    records, _, why = load(path)
    if why:
        raise ValueError(why)
    anchors = []
    for record in records:
        rec = record["recall_record"]
        if record["kind"] != "bug" or (repo is not None
                                       and str(rec.get("repo", "")).lower() != repo.lower()):
            continue
        for raw in rec.get("anchors") or []:
            if isinstance(raw, dict) and well_formed_file(raw.get("file")) \
                    and well_formed_symbol(raw.get("symbol")) \
                    and isinstance(raw.get("code_sha"), str):
                anchors.append(Anchor(raw["file"], raw["symbol"], raw["code_sha"]))
    return _query(repo, anchors)


def _changed_lines(diff: str) -> dict[str, list[int]]:
    """New-side line numbers per file in a `git diff -U0` text; a deletion gives its position."""
    lines: dict[str, list[int]] = {}
    current = None
    for row in diff.split("\n"):
        if row.startswith("+++ "):
            name = row[4:]
            current = name[2:] if name.startswith("b/") else None
            continue
        match = _HUNK.match(row)
        if match and current is not None:
            start, count = int(match.group(1)), int(match.group(2) or "1")
            span = range(start, start + count) if count else [max(start, 1)]
            lines.setdefault(current, []).extend(span)
    return lines


def for_diff(root: Path, base: str, repo: str | None) -> dict:
    """The anchors of every symbol the working tree changes against `base`."""
    diff = git(Path(root), "diff", "-U0", "--no-color", "--no-ext-diff", base, "--")
    anchors: list[Anchor] = []
    for file, numbers in _changed_lines(diff).items():
        if not (Path(root) / file).is_file():
            continue  # deleted by the change: nothing of it is left to anchor
        for number in dict.fromkeys(numbers):
            try:
                anchors.append(anchor_for(Path(root), file, number))
            except (OSError, ValueError):
                break
    return _query(repo, anchors)


def local_sha(root: Path) -> Callable[[dict], str | None]:
    """The checkout's `code_sha` for an anchor's file and symbol, or None when it is gone."""
    def sha(anchor: dict) -> str | None:
        file, symbol = anchor.get("file"), anchor.get("symbol")
        # The anchor comes from a saved answer: never read outside the repository.
        if not (well_formed_file(file) and well_formed_symbol(symbol)):
            return None
        assert isinstance(file, str)
        try:
            return symbol_sha(read_source(Path(root) / file), symbol)
        except (OSError, KeyError, SyntaxError, ValueError):
            return None
    return sha
