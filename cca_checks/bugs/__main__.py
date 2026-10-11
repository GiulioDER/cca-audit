"""python -m cca_checks.bugs backfill --repo PATH [--repo PATH ...] [--ledger PATH[=OWNER/NAME]]

Read-only unless --write. The dry run prints what would be written, so the numbers can
be reviewed before anything lands in the ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .backfill import backfill, summarize
from .gitread import GitError
from .store import bugs_dir, write


def _ledger_arg(spec: str) -> tuple[Path, str | None]:
    path, _, repo = spec.partition("=")
    return Path(path), (repo or None)


def _render(summary: dict) -> str:
    lines = [
        f"bugs: {summary['rows']}  tiers: {summary['tiers']}",
        f"lower bound: {summary['unparsed']} commit(s) matched but could not be parsed",
        f"by repository: {summary['by_repo']}",
    ]
    lines += [f"  {c}: {v}" for c, v in summary["evidence"].items()]
    for label in ("unparsed_commits", "skipped", "ambiguous", "ignored_proofs"):
        lines += [f"{label}: {item}" for item in summary[label]]
    if summary["malformed_ledger_lines"]:
        lines.append(f"malformed ledger lines skipped: {summary['malformed_ledger_lines']}")
    if "written" in summary:
        lines.append(f"written: {summary['written']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cca_checks bugs")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="rebuild the historical tier (read-only unless --write)")
    b.add_argument("--repo", action="append", type=Path, required=True,
                   help="a clone to read; clones of one GitHub repository count once")
    b.add_argument("--ledger", action="append", default=[], metavar="PATH[=OWNER/NAME]",
                   help="a scorecard ledger; name its repository if it is not inside a clone")
    b.add_argument("--write", action="store_true", help="append to the ledger and write memos")
    b.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    try:
        result = backfill(a.repo, [_ledger_arg(s) for s in a.ledger])
    except GitError as exc:
        print(f"backfill stopped: {exc}", file=sys.stderr)
        return 1
    summary = summarize(result)
    if a.write:
        root = bugs_dir()
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows, memos = write(result.rows, root, modified)
        text = "".join(f"{line}\n" for line in result.unparsed)
        (root / "unparsed.txt").write_text(text, encoding="utf-8", newline="\n")
        summary["written"] = {"rows": rows, "memos": memos, "dir": str(root)}
    print(json.dumps(summary, indent=2) if a.json else _render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
