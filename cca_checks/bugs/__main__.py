"""python -m cca_checks.bugs backfill --repo PATH [--repo PATH ...] [--ledger PATH[=OWNER/NAME]]
python -m cca_checks.bugs resolve [--write] [--json] [--stale-days N]
python -m cca_checks.bugs prior --records FILE [--code FILE] [--repo-name OWNER/NAME] [--root PATH]
    [--before YYYY-MM-DD] [--limit N] [--json]
python -m cca_checks.bugs prior --files F [F ...] [--repo-name OWNER/NAME] [--root PATH]
    [--before YYYY-MM-DD] [--limit N] [--json]
python -m cca_checks.bugs anchors (--diff [--base REV] | --findings L2.json | --records FILE
    | --at FILE:LINE ...)
    [--root PATH] [--repo-name OWNER/NAME]
python -m cca_checks.bugs match-fp --findings L2.json --records FILE [--out FILE] [--root PATH]
python -m cca_checks.bugs retract-fp ID --reason TEXT [--by NAME]
python -m cca_checks.bugs rerender --all [--modified STAMP] [--json]
python -m cca_checks.bugs backfill-caused-by --repo-root PATH [--write] [--json]
python -m cca_checks.bugs roundtrip-check --dump FILE [FILE ...] [--json]

Read-only unless --write. The dry run prints what would be written, so the numbers can
be reviewed before anything lands in the ledger. `resolve` exits 3 when GitHub could not
be asked about some record (the hourly worker retries), otherwise 0. `prior` is read-only
and exits 0 on any data or environment problem (a missing ledger, origin or readable records
file prints the empty block), and 2 on a usage error. With `--records` it renders the anchored
block from a saved `recall_records` answer (DIFF mode), with `--files` the ledger join (HUNT
mode). `anchors` prints the anchors a `recall_records` query or a Step 2.5b row needs.
`match-fp` is the mechanical PRIOR_FP matcher; it writes its result to `--out` and exits 0, and
`CCA_PRIOR_FP=off` turns every suppression into an attachment. `retract-fp` appends a
`false_positive_retraction` row (exit 1 when ID is not a false positive of the ledger).
`rerender --all` rewrites every memo from the ledger, the one sanctioned overwrite of memos,
and never writes the ledger; it exits 1 when a ledger line could not be read (that memo is
left as it was). `backfill-caused-by` blames each fix of one checked-out repository against
its parent; it only reads the ledger, and with --write it appends each new edge to
`caused_by.jsonl` beside it. Run `rerender --all` after it, so the memos carry the edges.
`roundtrip-check` exits 0 only when every ledger row has an equal record in RE-call's dump and
the dump holds nothing else, 1 when it lists a difference, and 2 when the dump cannot be read
or is truncated.
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


def _repo(a: argparse.Namespace) -> str | None:
    from .gitread import remote_repo

    if getattr(a, "repo_name", None):
        return a.repo_name
    try:
        return remote_repo(a.root)
    except (OSError, GitError):
        return None


def _prior(a: argparse.Namespace) -> int:
    from . import prior
    from .store import LEDGER

    if a.code is not None and a.records is None:
        print("bugs prior: --code needs --records", file=sys.stderr)
        return 2
    if a.records is None and not a.files:
        print("bugs prior: give --records FILE (DIFF mode) or --files (HUNT mode)",
              file=sys.stderr)
        return 2
    repo = _repo(a)
    reason = None if repo is not None else "no origin remote"
    if a.records is not None:
        return _prior_records(a, repo, reason)
    ledger = bugs_dir() / LEDGER
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
        _emit(prior.render(selected, reason))
    return 0


def _prior_records(a: argparse.Namespace, repo: str | None, reason: str | None) -> int:
    from . import envelope, prior

    records, _, why = envelope.load(a.records)
    reason = reason or why
    code = None
    if a.code is not None:
        code, _, code_why = envelope.load(a.code)
        if code_why:
            # A code answer that cannot be read labels nothing, rather than guessing.
            code = None
            reason = "; ".join(r for r in (reason, f"code: {code_why}") if r)
    selected = []
    if repo is not None and why is None:
        selected = prior.select_records(records, repo, before=a.before, limit=a.limit)
    if a.json:
        print(json.dumps({"repo": repo, "reason": reason,
                          "counts": prior.block_counts(selected),
                          "records": [r.get("id") for r in selected]}, indent=2))
    else:
        _emit(prior.render_records(selected, reason, code, repo or ""))
    return 0


def _anchors(a: argparse.Namespace) -> int:
    from . import query_anchors

    try:
        if a.at:
            out: object = query_anchors.at(a.root, a.at)
        elif a.findings is not None:
            out = query_anchors.for_findings(a.root, a.findings, _repo(a))
        elif a.records is not None:
            out = query_anchors.for_records(a.records, _repo(a))
        else:
            out = query_anchors.for_diff(a.root, a.base, _repo(a))
    except (OSError, ValueError, GitError) as exc:
        print(f"bugs anchors: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(out, indent=1))
    return 0


def _match_fp(a: argparse.Namespace) -> int:
    from . import match_fp, query_anchors
    from .records import RetractionRow, read_ledger

    try:
        findings = query_anchors.located(a.root, a.findings)
    except (OSError, ValueError) as exc:
        print(f"bugs match-fp: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 2
    from . import envelope

    _, value, why = envelope.load(a.records)
    rows, _ = read_ledger(bugs_dir())
    retracted = {r.target_id for r in rows.values()
                 if isinstance(r, RetractionRow) and r.kind == "false_positive_retraction"}
    on = match_fp.enabled()
    matches = [] if why else match_fp.match(
        findings, value, repo=_repo(a), retracted=retracted,
        local=query_anchors.local_sha(a.root), on=on)
    out = {"schema": 1, "switch": "on" if on else "off", "repo": _repo(a),
           "generation_id": value.get("generation_id") if isinstance(value, dict) else None,
           "matches": [m.to_json() for m in matches], "errors": [why] if why else []}
    text = json.dumps(out, indent=1)
    if a.out is not None:
        a.out.parent.mkdir(parents=True, exist_ok=True)
        a.out.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0


def _retract_fp(a: argparse.Namespace) -> int:
    import getpass

    from .records import FalsePositiveRow, RetractionRow, read_ledger
    from .store import write as write_rows

    root = bugs_dir()
    rows, _ = read_ledger(root)
    target = rows.get(a.id)
    if not isinstance(target, FalsePositiveRow):
        print(f"bugs retract-fp: {a.id!r} is not a false_positive row of the ledger",
              file=sys.stderr)
        return 1
    if not a.reason.strip():
        print("bugs retract-fp: --reason must not be empty", file=sys.stderr)
        return 2
    stamp = _now()
    row = RetractionRow(kind="false_positive_retraction", project=target.project,
                        repo=target.repo, date=stamp[:10], target_id=target.id,
                        reason=a.reason.strip(), retracted_by=a.by or getpass.getuser())
    added, memos = write_rows([row], root, stamp)
    print(json.dumps({"id": row.id, "rows": added, "memos": memos, "dir": str(root)}))
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
    from .store import ANNOTATIONS

    report = backfill_caused_by(bugs_dir(), a.repo_root, write=a.write)
    data = {**asdict(report), "log": str(bugs_dir() / ANNOTATIONS),
            "note": ("bugs.jsonl was only read. Memos change only through "
                     "`python -m cca_checks.bugs rerender --all`."
                     if a.write else "dry run: nothing was written; --write appends to the log")}
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
    q = sub.add_parser("prior", help="print the prior bugs block for the auditor brief")
    q.add_argument("--records", type=Path, metavar="FILE",
                   help="a saved recall_records answer for the changed code's anchors (DIFF)")
    q.add_argument("--code", type=Path, metavar="FILE",
                   help="the code tenant's recall_records answer for the records' anchors; "
                        "labels each anchor unchanged, changed or gone")
    q.add_argument("--files", nargs="+", default=[],
                   help="HUNT mode: join the local ledger on these files")
    q.add_argument("--repo-name", metavar="OWNER/NAME",
                   help="the repository; when absent it is read from --root's origin")
    q.add_argument("--root", type=Path, default=Path("."))
    q.add_argument("--before", type=_date_arg, metavar="YYYY-MM-DD")
    q.add_argument("--limit", type=_positive_int, default=8)
    q.add_argument("--json", action="store_true")
    n = sub.add_parser("anchors", help="code anchors for a recall_records query or a row")
    how = n.add_mutually_exclusive_group(required=True)
    how.add_argument("--diff", action="store_true",
                     help="the symbols the working tree changes against --base")
    how.add_argument("--findings", type=Path, metavar="L2.json",
                     help="the place of every finding in a consolidated L2 file")
    how.add_argument("--records", type=Path, metavar="FILE",
                     help="the bug records' anchors in a saved recall_records answer, for the "
                          "code tenant's query (the code join)")
    how.add_argument("--at", nargs="+", metavar="FILE:LINE", help="these places")
    n.add_argument("--base", default="HEAD", help="--diff: the revision to compare against")
    n.add_argument("--root", type=Path, default=Path("."))
    n.add_argument("--repo-name", metavar="OWNER/NAME")
    m = sub.add_parser("match-fp", help="the PRIOR_FP matcher, between L2 and L2.5")
    m.add_argument("--findings", type=Path, required=True, metavar="L2.json")
    m.add_argument("--records", type=Path, required=True, metavar="FILE",
                   help="the saved recall_records answer for the findings' anchors")
    m.add_argument("--out", type=Path, metavar="FILE",
                   help=".claude/audits/runs/<run_id>/match_fp.json, which L2.5 reads")
    m.add_argument("--root", type=Path, default=Path("."))
    m.add_argument("--repo-name", metavar="OWNER/NAME")
    x = sub.add_parser("retract-fp", help="retract an accepted false positive record")
    x.add_argument("id", help="the false positive record id, <project>:fp:<run_id>:<finding>")
    x.add_argument("--reason", required=True)
    x.add_argument("--by", help="who retracts it (default: the current user)")
    w = sub.add_parser("rerender", help="rewrite every memo from its ledger row")
    w.add_argument("--all", action="store_true", required=True,
                   help="required: every memo is rewritten, hand edits included")
    w.add_argument("--modified", metavar="STAMP",
                   help="the memos' `modified` stamp (default: now, UTC)")
    w.add_argument("--json", action="store_true")
    c = sub.add_parser("backfill-caused-by",
                       help="derive caused_by for ledger rows of one checked-out repository")
    c.add_argument("--repo-root", type=Path, required=True, metavar="PATH",
                   help="a clone of the repository whose rows to fill")
    c.add_argument("--write", action="store_true",
                   help="append the annotations to caused_by.jsonl (bugs.jsonl is never "
                        "written; memos change through `rerender --all`)")
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

    if a.cmd == "anchors":
        return _anchors(a)

    if a.cmd == "match-fp":
        return _match_fp(a)

    if a.cmd == "retract-fp":
        return _retract_fp(a)

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
