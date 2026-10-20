"""python -m cca_checks.bugs backfill --repo PATH [--repo PATH ...] [--ledger PATH[=OWNER/NAME]]
python -m cca_checks.bugs resolve [--write] [--json] [--stale-days N]
python -m cca_checks.bugs prior --files F [F ...] [--repo OWNER/NAME] [--root PATH]
    [--before YYYY-MM-DD] [--limit N] [--json]

Read-only unless --write. The dry run prints what would be written, so the numbers can
be reviewed before anything lands in the ledger. `resolve` exits 3 when GitHub could not
be asked about some record (the hourly worker retries), otherwise 0. `prior` is read-only
and always exits 0 (a missing ledger or origin prints the empty block).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .backfill import backfill, summarize
from .gitread import GitError
from .model import _CONTROL, _printable  # noqa: F401  (the names stay importable from here)
from .store import bugs_dir, write


def _ledger_arg(spec: str) -> tuple[Path, str | None]:
    path, _, repo = spec.partition("=")
    return Path(path), (repo or None)


def _bind_arg(spec: str) -> tuple[str, str]:
    sha, _, run = spec.partition("=")
    if not re.fullmatch(r"[0-9a-fA-F]{7,40}", sha) or not run:
        raise argparse.ArgumentTypeError(f"expected SHA=RUN with a 7 to 40 digit sha, got {spec!r}")
    return sha.lower(), run


def _date_arg(value: str) -> str:
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None
    return value


def _prior(a: argparse.Namespace) -> int:
    from . import prior
    from .gitread import remote_repo
    from .store import LEDGER

    ledger = bugs_dir() / LEDGER
    repo = a.repo
    reason = None
    if repo is None:
        try:
            repo = remote_repo(a.root)
        except (OSError, GitError):
            repo = None
        if repo is None:
            reason = "no origin remote"
    rows: list[dict] = []
    if reason is None:
        if ledger.is_file():
            rows = prior.load_rows(ledger)
        else:
            reason = "no ledger"
    selected = []
    if repo is not None and rows:
        selected = prior.select(rows, repo, a.files, before=a.before, limit=a.limit)
    if a.json:
        keys = ("bug_id", "finding_id", "severity", "date", "tier", "fix_commit", "summary")
        out = [{**{k: row.get(k) for k in keys}, "files": overlap} for row, overlap in selected]
        print(json.dumps({"repo": repo, "reason": reason, "rows": out}, indent=2))
    else:
        print(prior.render(selected, reason))
    return 0


def _render(summary: dict) -> str:
    return _printable(_render_raw(summary))


def _render_raw(summary: dict) -> str:
    lines = [
        f"bugs: {summary['rows']}  tiers: {summary['tiers']}",
        f"lower bound: {summary['unparsed']} commit(s) matched but could not be parsed",
        f"by repository: {summary['by_repo']}",
    ]
    lines += [f"  {c}: {v}" for c, v in summary["evidence"].items()]
    for label in ("unparsed_commits", "skipped", "ambiguous", "candidates", "unused_bindings",
                  "unknown_ids", "reverted", "cross_repo", "ignored_proofs"):
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
    b.add_argument("--bind", action="append", default=[], type=_bind_arg, metavar="SHA=RUN",
                   help="confirm that ledger run RUN produced fix commit SHA; ledger evidence "
                        "applies only through a binding (the dry run lists candidates)")
    b.add_argument("--write", action="store_true", help="append to the ledger and write memos")
    b.add_argument("--json", action="store_true")
    r = sub.add_parser("resolve", help="write pending runs whose pull request merged")
    r.add_argument("--write", action="store_true", help="append rows and move records")
    r.add_argument("--json", action="store_true")
    r.add_argument("--stale-days", type=int, default=14)
    q = sub.add_parser("prior", help="print the prior fixes recorded for a set of files")
    q.add_argument("--files", nargs="+", required=True)
    q.add_argument("--repo", help="OWNER/NAME; when absent it is read from --root's origin")
    q.add_argument("--root", type=Path, default=Path("."))
    q.add_argument("--before", type=_date_arg, metavar="YYYY-MM-DD")
    q.add_argument("--limit", type=int, default=8)
    q.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    if a.cmd == "prior":
        return _prior(a)

    if a.cmd == "resolve":
        from dataclasses import asdict

        from . import resolve as rs
        report = rs.resolve(bugs_dir(), write=a.write, stale_days=a.stale_days)
        data = asdict(report)
        if a.json:
            print(json.dumps(data, indent=2))
        else:
            print(_printable("\n".join(f"{k}: {v}" for k, v in data.items())))
        return 3 if report.unknown else 0

    try:
        result = backfill(a.repo, [_ledger_arg(s) for s in a.ledger], dict(a.bind))
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
