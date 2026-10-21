"""python -m cca_checks.bugs backfill --repo PATH [--repo PATH ...] [--ledger PATH[=OWNER/NAME]]
python -m cca_checks.bugs resolve [--write] [--json] [--stale-days N]
python -m cca_checks.bugs prior --files F [F ...] [--repo-name OWNER/NAME] [--root PATH]
    [--before YYYY-MM-DD] [--limit N] [--similar PATH [PATH ...]] [--cross-repo] [--json]
python -m cca_checks.bugs rerender --all [--modified STAMP] [--json]
python -m cca_checks.bugs backfill-caused-by --repo-root PATH [--write] [--json]
python -m cca_checks.bugs roundtrip-check --dump FILE [FILE ...] [--json]

Read-only unless --write. The dry run prints what would be written, so the numbers can
be reviewed before anything lands in the ledger. `resolve` exits 3 when GitHub could not
be asked about some record (the hourly worker retries), otherwise 0. `prior` is read-only
and exits 0 on any data or environment problem (a missing ledger or origin prints the empty
block), and 2 on a usage error (argparse). `rerender --all` rewrites every memo from the
ledger, the one sanctioned overwrite of memos, and never writes the ledger; it exits 1 when a
ledger line could not be read (that memo is left as it was). `backfill-caused-by` blames each
fix of one checked-out repository against its parent; with --write it rewrites only the lines
it changed, keeping the old ledger as `bugs.jsonl.before-caused-by`. Run `rerender --all`
after it, so the memos carry the edges. `roundtrip-check` exits 0 only when every ledger row
has an equal record in RE-call's dump and the dump holds nothing else, 1 when it lists a
difference, and 2 when the dump cannot be read or is truncated.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .backfill import backfill, summarize
from .github import owners_from_env
from .gitread import GitError
from .model import _printable
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
    # The filters compare dates as strings, so only the zero padded form orders correctly.
    try:
        datetime.strptime(value, "%Y-%m-%d")
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}") from None
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise argparse.ArgumentTypeError(f"expected YYYY-MM-DD, got {value!r}")
    return value


def _responses(paths: list[Path]) -> tuple[list[dict], str | None]:
    # Saved RE-call answers: an unreadable or malformed file is skipped and named, never fatal.
    from . import prior

    responses: list[dict] = []
    errors: list[str] = []
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError, RecursionError) as exc:
            errors.append(f"{path}: {type(exc).__name__}")
            continue
        if not isinstance(value, dict):
            errors.append(f"{path}: {type(value).__name__} is not an object")
            continue
        if reason := prior.unwrap_error(value):
            errors.append(f"{path}: {reason}")
            continue
        responses.append(value)
    return responses, "; ".join(errors) or None


def _emit(text: str) -> None:
    # A piped stdout on Windows is cp1252 with strict errors: write only what it can encode.
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    print(text.encode(encoding, errors="replace").decode(encoding))


def _positive_int(value: str) -> int:
    try:
        number = int(value)
    except ValueError:
        raise argparse.ArgumentTypeError(f"expected an integer, got {value!r}") from None
    if number < 1:
        raise argparse.ArgumentTypeError(f"expected an integer of at least 1, got {value!r}")
    return number


def _owners(repo: str | None) -> set[str]:
    named = set(owners_from_env())
    if named:
        return named
    return {repo.split("/", 1)[0].lower()} if repo else set()


def _prior(a: argparse.Namespace) -> int:
    from . import prior
    from .gitread import remote_repo
    from .store import LEDGER

    ledger = bugs_dir() / LEDGER
    repo = a.repo_name
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
    near: list[tuple[dict, list[str]]] | None = None
    unjoined = 0
    similar_error = None
    if a.similar is not None:
        responses, similar_error = _responses(a.similar)
        near = []
        if repo is not None and rows:
            exclude = {str(row.get("bug_id")) for row, _ in selected}
            near, unjoined = prior.similar(rows, responses, repo, _owners(repo), a.cross_repo,
                                           a.before, exclude, limit=min(a.limit, prior.SIMILAR_MAX))
    if a.json:
        keys = ("bug_id", "finding_id", "severity", "date", "tier", "fix_commit", "summary")
        out = [{**{k: row.get(k) for k in keys}, "files": overlap} for row, overlap in selected]
        data: dict = {"repo": repo, "reason": reason, "rows": out}
        if near is not None:
            data["similar"] = [{**{k: row.get(k) for k in keys}, "repo": row.get("repo"),
                                "files": files} for row, files in near]
            data["unjoined"] = unjoined
            if similar_error:
                data["similar_error"] = similar_error
        print(json.dumps(data, indent=2))
    elif near is None:
        _emit(prior.render(selected, reason))
    else:
        _emit(prior.render(selected, reason, similar_rows=near, repo=repo or ""))
    return 0


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _rerender(a: argparse.Namespace) -> int:
    from dataclasses import asdict

    from .records import rerender_all

    report = rerender_all(bugs_dir(), a.modified or _now())
    data = {**asdict(report), "dir": str(bugs_dir())}
    if a.json:
        print(json.dumps(data, indent=2))
    else:
        _emit(_printable("\n".join(f"{k}: {v}" for k, v in data.items())))
    return 1 if report.skipped else 0


def _backfill_caused_by(a: argparse.Namespace) -> int:
    from dataclasses import asdict

    from .caused_by import backfill_caused_by

    report = backfill_caused_by(bugs_dir(), a.repo_root, write=a.write)
    data = {**asdict(report), "written": bool(a.write and report.changed)}
    if a.json:
        print(json.dumps(data, indent=2))
    else:
        _emit(_printable("\n".join(f"{k}: {v}" for k, v in data.items())))
    return 0


def _roundtrip_check(a: argparse.Namespace) -> int:
    from dataclasses import asdict

    from . import roundtrip

    try:
        result = roundtrip.check(bugs_dir(), roundtrip.load_dumps(a.dump))
    except (OSError, ValueError, RecursionError) as exc:
        print(f"roundtrip-check refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    if a.json:
        print(json.dumps({**asdict(result), "ok": result.ok}, indent=2))
    else:
        lines = [f"rows: {result.rows}  records: {result.records}  ok: {result.ok}"]
        for label in ("missing", "changed", "orphan", "unreadable"):
            lines += [f"{label}: {item}" for item in getattr(result, label)]
        _emit(_printable("\n".join(lines)))
    return 0 if result.ok else 1


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
    q.add_argument("--repo-name", metavar="OWNER/NAME",
                   help="the repository; when absent it is read from --root's origin")
    q.add_argument("--root", type=Path, default=Path("."))
    q.add_argument("--before", type=_date_arg, metavar="YYYY-MM-DD")
    q.add_argument("--limit", type=_positive_int, default=8)
    q.add_argument("--similar", nargs="+", action="extend", type=Path, metavar="PATH",
                   help="saved recall_search responses to join to the ledger")
    q.add_argument("--cross-repo", action="store_true",
                   help="let --similar show rows of other repositories of the allowed owners")
    q.add_argument("--json", action="store_true")
    w = sub.add_parser("rerender", help="rewrite every memo from its ledger row")
    w.add_argument("--all", action="store_true", required=True,
                   help="required: every memo is rewritten, hand edits included")
    w.add_argument("--modified", metavar="STAMP",
                   help="the memos' `modified` stamp (default: now, UTC)")
    w.add_argument("--json", action="store_true")
    c = sub.add_parser("backfill-caused-by",
                       help="set caused_by on ledger rows of one checked-out repository")
    c.add_argument("--repo-root", type=Path, required=True, metavar="PATH",
                   help="a clone of the repository whose rows to fill")
    c.add_argument("--write", action="store_true",
                   help="rewrite the changed ledger lines (the old ledger is kept beside it)")
    c.add_argument("--json", action="store_true")
    t = sub.add_parser("roundtrip-check",
                       help="compare the ledger with RE-call's dump of the records it serves")
    t.add_argument("--dump", type=Path, nargs="+", required=True, metavar="FILE",
                   help="`recall records --all --json` output; several files are pages, in order")
    t.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    if a.cmd == "roundtrip-check":
        return _roundtrip_check(a)

    if a.cmd == "rerender":
        return _rerender(a)

    if a.cmd == "backfill-caused-by":
        return _backfill_caused_by(a)

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
        modified = _now()
        rows, memos = write(result.rows, root, modified)
        text = "".join(f"{line}\n" for line in result.unparsed)
        (root / "unparsed.txt").write_text(text, encoding="utf-8", newline="\n")
        summary["written"] = {"rows": rows, "memos": memos, "dir": str(root)}
    print(json.dumps(summary, indent=2) if a.json else _render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
