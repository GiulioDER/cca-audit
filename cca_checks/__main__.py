import argparse
import json
import os
import sys
from dataclasses import asdict

from . import languages
from .cargo_repro import run_repro as run_cargo_repro
from .claim import Claim, Verdict, make_verdict
from .property_check import run_properties
from .repro_runner import run_repro
from .toolpath import _is_inside

# Derived from the registry rather than restated, so a backend that adds a claim type
# reaches the CLI without a second edit here. A hand-maintained list would let
# `--claim-type panic_path` exit with an argparse usage error on a repo where the
# backend settles it perfectly well -- and a usage error is not a verdict, so the
# finding leaves the pipeline through a path that renders no evidence at all.
CLAIM_TYPES = sorted({ct for b in languages.BACKENDS for ct in b.claim_types})

# WHY THE LANGUAGE IS RESOLVED HERE AND NOT PER-CHECKER. It used to be per-checker,
# and it was enforced in two places out of five: `clock_check` and `semgrep_check`
# test `.endswith(".py")`, `type`/`nullability` fail closed only because the
# blindness probe escalates when `ast.parse` chokes on non-Python, and `definedness`
# -- exempt from that probe by TYPE_DEPENDENT_CLAIMS -- had nothing. pyright parses a
# `.rs` file as Python, emits syntax errors that fall under no rule we match, and
# `verdict_for_claim` falls through to a confident FALSE_POSITIVE carrying
# `source: pyright`. That artifact may not be overturned downstream, so a real defect
# is dropped and the file is closed on it.
#
# A guarantee each checker has to remember to implement is one the next checker ships
# without. Resolving the language once, here, is what makes it structural: see
# `cca_checks/languages/__init__.py`, which owns the extension table.


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


def _resolve_backend(file: str, claim_type: str, finding_id: str):
    """(backend, None) when this claim can be settled, (None, Verdict) when it cannot.

    Two distinct refusals, because they mean different things to whoever reads the
    escalation: no backend covers the language at all, versus a backend that covers
    the language but does not claim to settle THIS claim type. The second is the one
    that keeps a future claim type safe -- it is unsupported by every backend until
    its author opts each one in, rather than defaulting into a checker built for a
    different language.
    """
    def bad(why: str):
        return None, make_verdict(finding_id, "UNCERTAIN", f"{why}; escalated", "llm")

    backend = languages.resolve(file)
    if backend is None:
        ext = languages.extension_of(file) or "extension-less"
        return bad(f"no deterministic backend covers the {ext} language "
                   f"({os.path.basename(file)}); a tool that cannot read this file "
                   f"cannot be meaningfully silent about it either")
    if claim_type not in backend.claim_types:
        return bad(f"the {backend.name} backend does not settle {claim_type!r} claims "
                   f"(it settles: {', '.join(sorted(backend.claim_types))})")
    return backend, None


def _check(claim_type: str, args) -> Verdict:
    # Coordinate first, language second, and the order is load-bearing for the
    # MESSAGE rather than the verdict -- both gates escalate, so either order is
    # equally safe, but a directory or a missing file has no meaningful extension
    # and would otherwise be reported as an unsupported language, which sends the
    # reader looking for a backend to install instead of at the typo in the path.
    # Neither gate runs an analyzer, so nothing is executed against an unsupported
    # language by checking the coordinate first.
    invalid = _validate_coordinate(args.file, args.line, args.finding_id)
    if invalid is not None:
        return invalid
    backend, unsupported = _resolve_backend(args.file, claim_type, args.finding_id)
    if unsupported is not None:
        return unsupported
    claim = Claim(args.finding_id, args.file, args.line, claim_type,
                  proposition=args.symbol, sink_class=args.sink_class)
    return backend.settle(claim)


#: Which repro runner drives a generated test, by the test file's own extension.
#: Keyed on the TEST rather than looked up through the language registry, because a
#: repro is a file the caller wrote -- there is no claim coordinate to resolve, and a
#: `.rs` test is run by cargo whatever the finding was about.
_REPRO_RUNNERS = {".py": run_repro, ".rs": run_cargo_repro}


def _repro(args) -> Verdict:
    """Dispatch a repro to the runner for the test file's language.

    An unrecognised extension escalates rather than defaulting to pytest. Handing a
    `.rs` file to `python -m pytest` yields a collection error, and a collection
    error is a non-zero exit -- which the pytest runner would then have to tell apart
    from a genuine failure. Refusing up front is both clearer and safer.
    """
    ext = os.path.splitext(args.test)[1].lower()
    runner = _REPRO_RUNNERS.get(ext)
    if runner is None:
        return make_verdict(
            args.finding_id, "UNCERTAIN",
            f"no repro runner for a {ext or 'extension-less'} test file "
            f"({os.path.basename(args.test)}); supported: "
            f"{', '.join(sorted(_REPRO_RUNNERS))}; escalated", "llm")
    return runner(args.finding_id, args.test, args.expect_error)


def _capabilities(file: str) -> dict:
    """What this installation can settle about `file`, and what is missing.

    WHY THIS EXISTS. `cca-fp-check.md` is a PROMPT, and it used to enumerate the
    claim types in prose. That is a copy of the routing table living beside the real
    one, and the two drift -- the back-compat `definedness` alias below exists
    because they already did once. With a second language the copy also has to encode
    which claim types apply to which extension, and a prompt that says "Rust settles
    panic_path" on a machine with no cargo produces an agent confidently running a
    check that cannot run.

    So the prompt asks instead. `claim_types` is what the backend declares;
    `unavailable` names the ones whose tool is missing RIGHT HERE, with the reason.
    Both are reported, never silently subtracted: an agent that sees `overflow` listed
    as unavailable knows to escalate it, whereas an agent that never sees it at all
    cannot tell that from a claim type nobody supports.
    """
    backend = languages.resolve(file)
    if backend is None:
        return {"file": file, "language": None, "claim_types": [], "unavailable": {},
                "reason": f"no deterministic backend covers "
                          f"{languages.extension_of(file) or 'this extension'}"}
    unavailable = {}
    check = getattr(backend, "unavailable_claim_types", None)
    if check is not None:
        unavailable = check()
    return {"file": file, "language": backend.name,
            "claim_types": sorted(backend.claim_types),
            "unavailable": unavailable}


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

    k = sub.add_parser("capabilities",
                       help="which claim types can be settled about a file, here")
    k.add_argument("--file", required=True)

    a = p.parse_args(argv)
    if a.cmd == "capabilities":
        # Not a Verdict: this reports what the installation can do, not what is true
        # of the code, and giving it a verdict shape would let it be mistaken for one.
        print(json.dumps(_capabilities(a.file)))
        return 0
    if a.cmd == "check":
        v = _check(a.claim_type, a)
    elif a.cmd == "definedness":
        v = _check("definedness", a)
    elif a.cmd == "numeric":
        v = run_properties(a.finding_id, a.test)
    else:
        v = _repro(a)
    print(json.dumps(asdict(v)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
