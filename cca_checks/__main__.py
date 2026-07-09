import argparse
import json
import sys
from dataclasses import asdict
from .claim import Claim
from .pyright_check import run_pyright, verdict_for_definedness
from .repro_runner import run_repro

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="cca_checks")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("definedness")
    d.add_argument("--finding-id", required=True)
    d.add_argument("--file", required=True)
    d.add_argument("--line", type=int, required=True)
    d.add_argument("--symbol", default="")
    r = sub.add_parser("repro")
    r.add_argument("--finding-id", required=True)
    r.add_argument("--test", required=True)
    r.add_argument("--expect-error", default=None)
    a = p.parse_args(argv)
    if a.cmd == "definedness":
        claim = Claim(a.finding_id, a.file, a.line, "definedness", a.symbol)
        v = verdict_for_definedness(claim, run_pyright(a.file))
    else:
        v = run_repro(a.finding_id, a.test, a.expect_error)
    print(json.dumps(asdict(v)))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
