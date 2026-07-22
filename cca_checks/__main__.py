import argparse
import json
import os
import sys
from dataclasses import asdict

from .claim import Claim, Verdict, make_verdict
from .clock_check import verdict_for_clock_leak
from .property_check import run_properties
from .pyright_check import RULES_BY_CLAIM, run_pyright, verdict_for_claim
from .repro_runner import run_repro
from .semgrep_check import verdict_for_taint
from .toolpath import _is_inside

CLAIM_TYPES = sorted(set(RULES_BY_CLAIM) | {"taint", "clock_leak"})


def _add_claim_args(parser):
    parser.add_argument("--finding-id", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--line", type=int, required=True)
    # Informational only. Matching is line + rule granular; --symbol never
    # participates in it. Kept so callers can record what they were asking about.
    parser.add_argument("--symbol", default="")
    # Deliberately not an argparse `choices` list: an agent may name a sink class we
    # do not cover, and that must escalate to UNCERTAIN, not exit non-zero with a
    # usage error.
    parser.add_argument("--sink-class", default="")


def _validate_coordinate(file: str, line: int, finding_id: str) -> Verdict | None:
    """Reject a claim coordinate that cannot be settled. None means "usable".

    A coordinate no diagnostic can ever match must NOT reach the checkers, because
    every checker reads "no diagnostic here" as evidence and issues a confident
    FALSE_POSITIVE carrying an authoritative `source`. A hallucinated line number --
    the exact failure this gate exists to catch -- would therefore be rewarded with
    a refutation on a file that provably contains the defect. Escalating instead
    keeps "we could not check" distinct from "we checked and found nothing".

    Directories are refused for a second reason: pyright analyzes a directory
    happily and returns diagnostics for every file in it, while diagnostic matching
    is line-based, so a directory argument lets one file's diagnostic confirm a
    claim about another.
    """
    def bad(why: str) -> Verdict:
        return make_verdict(finding_id, "UNCERTAIN", f"{why}; escalated", "llm")

    if not os.path.exists(file):
        return bad(f"claim file {file!r} does not exist")
    if os.path.isdir(file):
        return bad(f"claim file {file!r} is a directory, not a file")
    resolved = os.path.realpath(file)
    if not _is_inside(resolved, os.path.realpath(os.getcwd())):
        # Analyzing outside the audit root is never something a finding about this
        # repo needs, and the file path originates from LLM output derived from
        # untrusted repo content.
        return bad(f"claim file {file!r} resolves outside the audit root")
    if line < 1:
        return bad(f"claim line {line} is not a valid 1-based line number")
    try:
        with open(file, "rb") as fh:
            total = sum(1 for _ in fh)
    except OSError as exc:
        return bad(f"claim file {file!r} could not be read ({exc.__class__.__name__})")
    if line > total:
        return bad(f"claim line {line} is past the end of {file!r} ({total} lines)")
    return None


def _check(claim_type: str, args) -> Verdict:
    invalid = _validate_coordinate(args.file, args.line, args.finding_id)
    if invalid is not None:
        return invalid
    claim = Claim(args.finding_id, args.file, args.line, claim_type,
                  proposition=args.symbol, sink_class=args.sink_class)
    if claim_type == "taint":
        return verdict_for_taint(claim)
    if claim_type == "clock_leak":
        return verdict_for_clock_leak(claim)
    return verdict_for_claim(claim, run_pyright(args.file), RULES_BY_CLAIM[claim_type])


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="cca_checks")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="settle a claim with a deterministic checker")
    c.add_argument("--claim-type", required=True, choices=CLAIM_TYPES)
    _add_claim_args(c)

    # Back-compat alias. cca-fp-check.md is *copied* into .claude/agents/ while
    # cca_checks is *pip-installed*; the two can drift. This makes that harmless.
    d = sub.add_parser("definedness", help="alias for: check --claim-type definedness")
    _add_claim_args(d)

    r = sub.add_parser("repro")
    r.add_argument("--finding-id", required=True)
    r.add_argument("--test", required=True)
    r.add_argument("--expect-error", default=None)

    n = sub.add_parser("numeric", help="settle a numeric claim by running declared properties")
    n.add_argument("--finding-id", required=True)
    n.add_argument("--test", required=True)

    a = p.parse_args(argv)
    if a.cmd == "check":
        v = _check(a.claim_type, a)
    elif a.cmd == "definedness":
        v = _check("definedness", a)
    elif a.cmd == "numeric":
        v = run_properties(a.finding_id, a.test)
    else:
        v = run_repro(a.finding_id, a.test, a.expect_error)
    print(json.dumps(asdict(v)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
