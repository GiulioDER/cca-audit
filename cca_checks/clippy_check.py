"""Settle Rust claims from clippy diagnostics -- the Rust analogue of pyright_check.

The shape is deliberately the same: disjoint lint sets per claim type, a diagnostic
covering the cited line CONFIRMS, and only span-wide SILENCE may refute. What differs
is the nature of the tool's blindness, and getting that difference right is the whole
soundness argument.

PYRIGHT IS TYPE-BLIND. CLIPPY IS LINT-BLIND. pyright given `def charge(user):` knows
nothing about `user` and says nothing -- hence the strict-mode blindness probe, which
asks whether it could see at all before a refutation is issued. Clippy is never
type-blind: the crate compiled, so every type is known. It is blind in a different
way -- `unwrap_used`, `indexing_slicing` and `arithmetic_side_effects` are all
ALLOW-BY-DEFAULT, so clippy's silence about them means nothing unless they were
switched on. Two consequences, both load-bearing:

  1. The lints are FORCE-ENABLED here, with `--force-warn`. Not `-W`: `-W` loses to a
     crate's own `#![allow(clippy::unwrap_used)]`, `clippy.toml`, or `[lints]` table,
     so the audited repo could delete the evidence against itself and collect a
     refutation carrying an authoritative `source: clippy`. `--force-warn` overrides
     all three. This is the exact analogue of `enableTypeIgnoreComments: false` in
     pyright_check, and it rests on the same rule: the auditor must control the
     configuration its refutations rest on; the audited repo must not.

  2. CARGO FRESHNESS is the analogue of pyright's `summary.filesAnalyzed`. A warm
     target directory can report a build with nothing to say, which is byte-for-byte
     what a clean crate looks like. `_run_clippy` therefore uses its own target
     directory -- never the crate's -- so the first analysis in a process is always
     cold, and it verifies from the JSON stream that a real compilation happened
     before any silence is trusted.

CONFIRMED IS UNREACHABLE FOR panic_path AND unsafe_op, BY DESIGN. A `.unwrap()` is
not a defect; a `.unwrap()` REACHABLE with a value the caller controls is. Clippy
reports the syntax, not the reachability, so it can prove a panicking construct is
ABSENT from a scope but never that a present one is wrong. Those two claim types
mirror `taint`: they refute, they never confirm, and a confirmation has to come from
`cargo test` actually panicking. `overflow` and `error_swallow` are different -- there
the lint fires on the defect itself, not on a possibility -- so they can confirm.
"""

import json
import os
import subprocess
import tempfile

from .claim import Claim, Verdict, make_verdict
from .config import RUST_TIMEOUT_S
from .toolpath import resolve_tool

SOURCE = "clippy"

# --- Lint sets --------------------------------------------------------------
# A claim type maps to exactly one interpretation of a diagnostic, so these sets must
# stay pairwise disjoint (enforced by a test), exactly as pyright_check's do.

PANIC_LINTS = frozenset({
    "clippy::unwrap_used",
    "clippy::expect_used",
    "clippy::indexing_slicing",
    "clippy::panic",
    "clippy::unreachable",
    "clippy::todo",
    "clippy::unimplemented",
    "clippy::panic_in_result_fn",
})

OVERFLOW_LINTS = frozenset({
    "clippy::arithmetic_side_effects",
    "clippy::cast_possible_truncation",
    "clippy::cast_possible_wrap",
    "clippy::cast_precision_loss",
    "clippy::cast_sign_loss",
    "clippy::checked_conversions",
})

ERROR_SWALLOW_LINTS = frozenset({
    # rustc's own, not clippy's -- the JSON carries it under the same `code.code`
    # field, so no special handling is needed, but it must be listed to be matched.
    "unused_must_use",
    "clippy::let_underscore_must_use",
    "clippy::let_underscore_untyped",
    "clippy::ok_expect",
    "clippy::unused_io_amount",
})

UNSAFE_LINTS = frozenset({
    "clippy::undocumented_unsafe_blocks",
    "clippy::multiple_unsafe_ops_per_block",
    "clippy::not_unsafe_ptr_arg_deref",
    "clippy::macro_use_imports",
})

LINTS_BY_CLAIM = {
    "panic_path": PANIC_LINTS,
    "overflow": OVERFLOW_LINTS,
    "error_swallow": ERROR_SWALLOW_LINTS,
    "unsafe_op": UNSAFE_LINTS,
}

#: Claim types where a lint hit means the defect ITSELF is present, so CONFIRMED is
#: reachable. The others report a possibility (a `.unwrap()` that may or may not be
#: reachable), which is evidence to adjudicate, never proof -- see the module
#: docstring. Membership here is what separates "may confirm" from "may only refute".
CONFIRMABLE_CLAIMS = frozenset({"overflow", "error_swallow"})

REFUTE_LABEL = {
    "panic_path": "panicking-construct",
    "overflow": "arithmetic-overflow",
    "error_swallow": "discarded-result",
    "unsafe_op": "unsafe-block",
}


def force_warn_flags(lints: frozenset[str]) -> list[str]:
    """`--force-warn` for each lint, sorted so the cargo fingerprint is stable.

    Unsorted flags change the command line between runs, which changes cargo's
    fingerprint and forces a full rebuild every single time -- turning a 2-second
    check into a 2-minute one for no behavioural gain.
    """
    return [flag for lint in sorted(lints) for flag in ("--force-warn", lint)]


# --- cargo clippy invocation -------------------------------------------------

def _manifest_for(path: str) -> str | None:
    """Nearest `Cargo.toml` at or above `path`'s directory, or None.

    None escalates. Guessing a workspace root would analyse a different crate than
    the one the claim is about, and a diagnostic from another crate must never settle
    a claim here.
    """
    current = os.path.dirname(os.path.abspath(path))
    while True:
        candidate = os.path.join(current, "Cargo.toml")
        if os.path.isfile(candidate):
            return candidate
        parent = os.path.dirname(current)
        if parent == current:
            return None
        current = parent


def run_clippy(path: str, lints: frozenset[str],
               target_dir: str | None = None) -> list[dict] | None:
    """Clippy diagnostics for the crate containing `path`, or None to escalate.

    None means "could not tell": cargo is missing or untrusted, no manifest was
    found, the run timed out, the crate did not build, the output was unparseable, or
    nothing was actually compiled. A `list` (possibly empty) means clippy genuinely
    ran and reported those diagnostics -- an empty list is "ran clean", which is what
    licenses a FALSE_POSITIVE, and the checks below are what make that reading safe.

    Mirrors `pyright_check._run_pyright_with_config`'s fail-safe cascade so the two
    cannot drift: every failure mode that would otherwise read as "ran clean" returns
    None instead.
    """
    exe = resolve_tool("cargo")
    if exe is None:
        # Missing from PATH, or resolved inside the audited tree (hijack attempt).
        return None
    manifest = _manifest_for(path)
    if manifest is None:
        return None

    with tempfile.TemporaryDirectory(prefix="cca-clippy-") as fallback:
        cmd = [
            exe, "clippy",
            "--manifest-path", manifest,
            "--all-targets",
            "--message-format=json",
            # A dedicated target directory, NEVER the crate's own. Two reasons, and
            # both matter: a warm cache can report a build with nothing to say (which
            # is indistinguishable from a clean crate), and writing into the audited
            # repo's `target/` dirties the very tree the pipeline is reviewing --
            # the same reason repro_runner passes `-p no:cacheprovider` to pytest.
            "--target-dir", target_dir or fallback,
            "--", *force_warn_flags(lints),
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=RUST_TIMEOUT_S,
            )
        except subprocess.TimeoutExpired:
            return None
        except OSError:
            return None
        return _parse_stream(proc.stdout or "", os.path.dirname(manifest))


def _absolutise(diagnostic: dict, crate_root: str) -> None:
    """Rewrite each span's `file_name` to an absolute path, in place.

    WHY THIS IS NOT OPTIONAL. cargo reports span paths RELATIVE TO THE MANIFEST
    DIRECTORY (`src\\lib.rs`), not to the process's working directory, and the CLI is
    normally run from the audit root rather than from inside the crate. Resolving
    them against cwd therefore matched nothing, every diagnostic became "location
    could not be determined", and every claim escalated -- measured, on the fixture
    crate, before this existed. It failed SAFE (an escalation, never a false
    refutation), which is precisely why it produced no visible symptom beyond the
    deterministic layer quietly settling nothing at all.
    """
    for span in _spans(diagnostic):
        name = span.get("file_name")
        if isinstance(name, str) and not os.path.isabs(name):
            span["file_name"] = os.path.join(crate_root, name)


def _parse_stream(stdout: str, crate_root: str) -> list[dict] | None:
    """Diagnostics from cargo's JSON-lines stream, or None if it proves nothing.

    Three distinct things have to be true before silence may be trusted, and each
    would otherwise be a way for "we could not check" to look like "we checked and
    found nothing":

      * the build FINISHED and SUCCEEDED -- a crate that does not compile produces no
        lint diagnostics at all, which reads exactly like a clean crate;
      * something was actually COMPILED -- with a per-process target directory a
        wholly-fresh build cannot legitimately happen, so it means the analysis did
        not cover this crate;
      * the stream PARSED -- cargo interleaves non-JSON lines, which are skipped, but
        a stream with no recognisable cargo records at all is not a result.
    """
    diagnostics: list[dict] = []
    build_succeeded: bool | None = None
    compiled_something = False
    saw_a_record = False

    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(record, dict):
            continue
        reason = record.get("reason")
        if reason is None:
            continue
        saw_a_record = True
        if reason == "compiler-message":
            message = record.get("message")
            if isinstance(message, dict):
                _absolutise(message, crate_root)
                diagnostics.append(message)
                compiled_something = True
        elif reason == "compiler-artifact":
            # `fresh: False` is a real compilation. A wholly-fresh build in a
            # per-process target dir means we are not looking at what we think.
            if record.get("fresh") is False:
                compiled_something = True
        elif reason == "build-finished":
            build_succeeded = bool(record.get("success"))

    if not saw_a_record or build_succeeded is not True or not compiled_something:
        return None
    return diagnostics


# --- Diagnostic reading -------------------------------------------------------

def lint_code(diagnostic: dict) -> str | None:
    """The lint name, e.g. "clippy::unwrap_used", or None when there is not one.

    Every field is untrusted: `code` may be missing (a plain compiler error has none)
    or a non-dict, and `code.code` may be any type. None means "this is not a lint
    diagnostic", which the caller must not treat as a match.
    """
    code = diagnostic.get("code")
    if not isinstance(code, dict):
        return None
    name = code.get("code")
    return name if isinstance(name, str) else None


def _spans(diagnostic: dict) -> list[dict]:
    spans = diagnostic.get("spans")
    return [s for s in spans if isinstance(s, dict)] if isinstance(spans, list) else []


def line_bounds(diagnostic: dict, file: str) -> tuple[int, int] | None:
    """1-based (first, last) line the diagnostic covers IN `file`, or None.

    cargo's `line_start`/`line_end` are already 1-indexed -- unlike pyright's
    0-indexed `range.start.line`, and matching semgrep's `start.line`. Getting this
    wrong is an off-by-one in the span that decides whether a refutation is allowed.

    The file is checked because clippy analyses a whole CRATE, not one file. Without
    it, a diagnostic in `lib.rs` would happily confirm a claim about `main.rs` at the
    same line number -- the same reason `__main__._validate_coordinate` refuses a
    directory for pyright.
    """
    target = os.path.abspath(file)
    best: tuple[int, int] | None = None
    for span in _spans(diagnostic):
        name = span.get("file_name")
        if not isinstance(name, str):
            continue
        if os.path.abspath(name) != target:
            continue
        start, end = span.get("line_start"), span.get("line_end")
        if not isinstance(start, int) or isinstance(start, bool):
            continue
        if not isinstance(end, int) or isinstance(end, bool) or end < start:
            end = start
        if best is None or start < best[0]:
            best = (start, end)
    return best


def _in_lints(diagnostics: list[dict], lints: frozenset[str]) -> list[dict]:
    return [d for d in diagnostics
            if isinstance(d, dict) and lint_code(d) in lints]


def _at_line(diagnostics: list[dict], file: str, line: int,
             lints: frozenset[str] | None = None) -> list[dict]:
    """Diagnostics whose span in `file` COVERS `line`.

    Kept span-exact on purpose, mirroring `pyright_check._diags_at`: widening the
    CONFIRM side to the enclosing function would let a hallucinated line inside a long
    expression collect a binding CONFIRMED. Only span-wide SILENCE may refute.
    """
    out = []
    for d in diagnostics:
        if not isinstance(d, dict):
            continue
        if lints is not None and lint_code(d) not in lints:
            continue
        bounds = line_bounds(d, file)
        if bounds is not None and bounds[0] <= line <= bounds[1]:
            out.append(d)
    return out


def _in_span(diagnostics: list[dict], file: str, lo: int, hi: int,
             lints: frozenset[str]) -> list[dict]:
    out = []
    for d in _in_lints(diagnostics, lints):
        bounds = line_bounds(d, file)
        if bounds is not None and bounds[0] <= hi and bounds[1] >= lo:
            out.append(d)
    return out


def _mentions(diagnostic: dict, file: str) -> bool:
    """True if any span of the diagnostic names `file`, readable line or not."""
    target = os.path.abspath(file)
    return any(isinstance(s.get("file_name"), str)
               and os.path.abspath(s["file_name"]) == target
               for s in _spans(diagnostic))


def _unlocatable(diagnostics: list[dict], file: str,
                 lints: frozenset[str]) -> list[dict]:
    """In-lint diagnostics that name `file` but whose line we could not read.

    These must not fall through to a refutation. A diagnostic that positively exists
    under a lint we care about, in this file, but whose location we cannot read, is
    evidence that something may be wrong here -- the inverse of "clippy was silent".
    Mirrors `pyright_check._unlocatable_in_rules` and `semgrep_check.hits_in_span`.

    THE `_mentions` CHECK IS WHAT KEEPS THIS FROM SWALLOWING THE WHOLE FEATURE.
    Clippy analyses a CRATE, so its output is full of diagnostics about OTHER files,
    and `line_bounds` returns None for every one of them -- it looks for a span in
    THIS file and finds none. Without this check, "no span in this file" and "a span
    in this file I cannot read" collapse into the same answer, so a single
    `unwrap_used` anywhere else in the crate makes every claim in every file
    permanently unrefutable. Measured on the fixture crate: a diagnostic in a sibling
    module turned a correct FALSE_POSITIVE into an escalation. A diagnostic elsewhere
    is not unlocatable -- its location is perfectly well known, and it is not ours.
    """
    return [d for d in _in_lints(diagnostics, lints)
            if _mentions(d, file) and line_bounds(d, file) is None]


def _describe(diagnostic: dict, file: str) -> str:
    bounds = line_bounds(diagnostic, file)
    where = f"{os.path.basename(file)}:{bounds[0]}" if bounds else os.path.basename(file)
    return f"{lint_code(diagnostic)} @ {where}: {diagnostic.get('message', '')}"


# --- Verdict ------------------------------------------------------------------

def verdict_for_claim(claim: Claim, diagnostics: list[dict] | None,
                      lints: frozenset[str], span=None) -> Verdict:
    """Settle a Rust claim against clippy's diagnostics. Three-way, never false-refutes.

    `span` is the claim's enclosing (lo, hi), or None when it could not be determined
    -- in which case only the line-exact result is available and a refutation is not
    issued from a span we do not have.
    """
    fid, file, line = claim.finding_id, claim.file, claim.line
    claim_type = claim.claim_type

    if diagnostics is None:
        # Tool unavailable, crate did not build, or nothing was compiled. NEVER
        # conflated with "clippy ran and was silent", which is what refutes.
        return make_verdict(
            fid, "UNCERTAIN",
            "clippy could not run (cargo missing, no Cargo.toml, build failed, or "
            "nothing was compiled); falling back to LLM", "llm")

    hit = _at_line(diagnostics, file, line, lints)
    if hit:
        evidence = "; ".join(_describe(d, file) for d in hit)
        if claim_type in CONFIRMABLE_CLAIMS:
            return make_verdict(fid, "CONFIRMED", f"clippy {evidence}", SOURCE)
        # The lint reports a possibility, not the defect. Present it as evidence to
        # adjudicate, and let `cargo test` be what confirms.
        return make_verdict(
            fid, "UNCERTAIN",
            f"clippy {evidence} -- the construct is present, but clippy reports the "
            f"syntax, not whether it is reachable with a value the caller controls. "
            f"Adjudicate, or confirm with a repro that actually panics.", SOURCE)

    at_line = _at_line(diagnostics, file, line)
    if at_line:
        # Clippy sees something here, just not under a lint we recognise. A renamed
        # or regrouped lint must not be read as "no bug" -- escalate.
        seen = ", ".join(sorted({str(lint_code(d)) for d in at_line}))
        return make_verdict(
            fid, "UNCERTAIN",
            f"clippy reported {len(at_line)} diagnostic(s) @ {os.path.basename(file)}:"
            f"{line} but none in the expected lint set (saw: {seen}); escalated", SOURCE)

    unlocatable = _unlocatable(diagnostics, file, lints)
    if unlocatable:
        seen = ", ".join(sorted({str(lint_code(d)) for d in unlocatable}))
        return make_verdict(
            fid, "UNCERTAIN",
            f"clippy reported {len(unlocatable)} diagnostic(s) in the expected lint "
            f"set (saw: {seen}) whose location in this file could not be determined; "
            f"escalated", SOURCE)

    if span is None:
        # No enclosing scope means no span-wide silence to rest a refutation on.
        return make_verdict(
            fid, "UNCERTAIN",
            f"clippy reported nothing at {os.path.basename(file)}:{line}, but the "
            f"enclosing scope could not be determined, so its silence cannot be "
            f"scoped to the claim; escalated", SOURCE)

    lo, hi = span
    nearby = _in_span(diagnostics, file, lo, hi, lints)
    if nearby:
        where = ", ".join(sorted({str(line_bounds(d, file)[0]) for d in nearby}))
        return make_verdict(
            fid, "UNCERTAIN",
            f"clippy reported no {REFUTE_LABEL.get(claim_type, claim_type)} "
            f"diagnostic exactly at {os.path.basename(file)}:{line}, but did report "
            f"one in the enclosing scope (lines {lo}-{hi}, at {where}) -- the claim "
            f"may be off by a line rather than false; escalated", SOURCE)

    return make_verdict(
        fid, "FALSE_POSITIVE",
        f"clippy: no {REFUTE_LABEL.get(claim_type, claim_type)} diagnostic anywhere "
        f"in the enclosing scope @ {os.path.basename(file)}:{line} (lines {lo}-{hi}), "
        f"with the lint force-enabled over the crate's own configuration", SOURCE)
