import argparse
import json
import sys
from dataclasses import asdict

from .claim import Claim, Verdict
from .pyright_check import RULES_BY_CLAIM, run_pyright, verdict_for_claim
from .repro_runner import run_repro

CLAIM_TYPES = sorted(RULES_BY_CLAIM)


def _add_claim_args(parser):
    parser.add_argument("--finding-id", required=True)
    parser.add_argument("--file", required=True)
    parser.add_argument("--line", type=int, required=True)
    # Informational only. Matching is line + rule granular; --symbol never
    # participates in it. Kept so callers can record what they were asking about.
    parser.add_argument("--symbol", default="")


def _check(claim_type: str, args) -> Verdict:
    claim = Claim(args.finding_id, args.file, args.line, claim_type,
                  proposition=args.symbol)
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

    a = p.parse_args(argv)
    if a.cmd == "check":
        v = _check(a.claim_type, a)
    elif a.cmd == "definedness":
        v = _check("definedness", a)
    else:
        v = run_repro(a.finding_id, a.test, a.expect_error)
    print(json.dumps(asdict(v)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
