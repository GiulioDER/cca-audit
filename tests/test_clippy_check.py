"""The clippy backend: verdict table, lint-set hygiene, and fail-safe cascade.

Split into two halves on purpose. The verdict logic is tested against INJECTED
diagnostics -- fast, deterministic, and it exercises every branch including the ones
a real crate will not reproduce on demand. The end-to-end half actually shells out to
cargo and is where the two soundness mechanisms are proven: that `--force-warn` beats
the audited crate's own `#![allow(...)]`, and that the fail-safe cascade never turns
a failure into a refutation.
"""

import os
import pathlib
import shutil

import pytest

from cca_checks import clippy_check as cc
from cca_checks.claim import Claim

CRATE = pathlib.Path(__file__).parent / "fixtures" / "rust_clippy"
LIB = str(CRATE / "src" / "lib.rs")

# Coordinates pinned in tests/test_fixture_contract.py, which is what catches a
# formatter shifting them out from under these assertions.
UNWRAP_LINE = 20
CHECKED_MATCH_LINE = 27
RAW_MUL_LINE = 35
SATURATING_LINE = 48
DISCARD_LINE = 54
PROPAGATE_LINE = 60

needs_cargo = pytest.mark.skipif(
    shutil.which("cargo") is None,
    reason="cargo is not installed; the CI job installs it and "
           "test_ci_installs_the_rust_toolchain asserts that it does",
)


def diag(lint, line, file=LIB, end=None, message="m"):
    return {"code": {"code": lint}, "message": message,
            "spans": [{"file_name": file, "line_start": line,
                       "line_end": end if end is not None else line}]}


def claim(line=RAW_MUL_LINE, claim_type="overflow", file=LIB):
    return Claim("RS-1", file, line, claim_type)


# --- lint-set hygiene ---------------------------------------------------------

def test_lint_sets_are_pairwise_disjoint():
    """A claim type must map to exactly ONE interpretation of a diagnostic.

    An overlap makes the verdict depend on which set is consulted first, and the same
    diagnostic would confirm one claim type while merely informing another. The
    identical invariant is asserted for pyright's rule sets.
    """
    items = list(cc.LINTS_BY_CLAIM.items())
    for i, (name_a, set_a) in enumerate(items):
        for name_b, set_b in items[i + 1:]:
            assert not (set_a & set_b), f"{name_a} and {name_b} share {set_a & set_b}"


def test_every_claim_type_has_lints():
    for name, lints in cc.LINTS_BY_CLAIM.items():
        assert lints, f"{name} has no lints, so it can never do anything but refute"


def test_confirmable_claims_are_a_subset_of_the_claim_types():
    assert cc.CONFIRMABLE_CLAIMS <= set(cc.LINTS_BY_CLAIM)


def test_every_claim_type_has_a_refute_label():
    """The label lands in user-facing evidence; a missing one silently degrades to
    the raw claim type in the one sentence a human reads to decide whether to look."""
    assert set(cc.REFUTE_LABEL) == set(cc.LINTS_BY_CLAIM)


def test_force_warn_flags_are_sorted():
    """Unsorted flags change the command line between runs, which changes cargo's
    fingerprint and forces a full rebuild every time for no behavioural gain."""
    flags = cc.force_warn_flags(frozenset({"clippy::b", "clippy::a"}))
    assert flags == ["--force-warn", "clippy::a", "--force-warn", "clippy::b"]


# --- verdicts, against injected diagnostics -----------------------------------

def test_a_hit_at_the_line_confirms_a_confirmable_claim():
    v = cc.verdict_for_claim(
        claim(claim_type="overflow"),
        [diag("clippy::arithmetic_side_effects", RAW_MUL_LINE)],
        cc.OVERFLOW_LINTS, span=(34, 36))
    assert v.verdict == "CONFIRMED"
    assert v.source == "clippy"
    assert "arithmetic_side_effects" in v.evidence


@pytest.mark.parametrize("claim_type,lints,lint", [
    ("panic_path", cc.PANIC_LINTS, "clippy::unwrap_used"),
    ("unsafe_op", cc.UNSAFE_LINTS, "clippy::undocumented_unsafe_blocks"),
])
def test_a_hit_never_confirms_a_possibility_claim(claim_type, lints, lint):
    """`.unwrap()` is not a defect; a REACHABLE `.unwrap()` is, and a lint cannot
    tell them apart. So these mirror taint: they refute, they never confirm."""
    v = cc.verdict_for_claim(
        claim(line=UNWRAP_LINE, claim_type=claim_type), [diag(lint, UNWRAP_LINE)],
        lints, span=(19, 22))
    assert v.verdict == "UNCERTAIN"
    assert lint in v.evidence
    assert "reachable" in v.evidence


def test_a_multiline_span_matches_the_cited_line():
    """Clippy anchors at the start of the offending expression while an auditor cites
    the line carrying the operation; for a multi-line expression the two differ."""
    diags = [diag("clippy::arithmetic_side_effects", 30, end=36)]
    for cited in (30, 33, 36):
        v = cc.verdict_for_claim(claim(line=cited), diags, cc.OVERFLOW_LINTS,
                                 span=(29, 40))
        assert v.verdict == "CONFIRMED", cited


def test_span_wide_silence_refutes():
    v = cc.verdict_for_claim(claim(line=SATURATING_LINE), [], cc.OVERFLOW_LINTS,
                             span=(47, 49))
    assert v.verdict == "FALSE_POSITIVE"
    assert v.source == "clippy"
    assert "force-enabled" in v.evidence


def test_an_unrecognised_lint_at_the_line_escalates_rather_than_refuting():
    """A renamed or regrouped lint must not be read as "no bug"."""
    v = cc.verdict_for_claim(
        claim(), [diag("clippy::some_future_lint", RAW_MUL_LINE)],
        cc.OVERFLOW_LINTS, span=(34, 36))
    assert v.verdict == "UNCERTAIN"
    assert "some_future_lint" in v.evidence


def test_a_hit_elsewhere_in_the_scope_escalates_rather_than_refuting():
    """Off by a line is not the same as false, and only span-wide silence may refute."""
    v = cc.verdict_for_claim(
        claim(line=34), [diag("clippy::arithmetic_side_effects", 36)],
        cc.OVERFLOW_LINTS, span=(33, 40))
    assert v.verdict == "UNCERTAIN"
    assert "off by a line" in v.evidence


def test_an_unlocatable_in_lint_diagnostic_escalates():
    """A diagnostic that positively exists under a lint we care about, but which we
    cannot place, is evidence something may be wrong -- the inverse of silence."""
    bad = {"code": {"code": "clippy::arithmetic_side_effects"}, "message": "m",
           "spans": [{"file_name": LIB, "line_start": None}]}
    v = cc.verdict_for_claim(claim(), [bad], cc.OVERFLOW_LINTS, span=(34, 36))
    assert v.verdict == "UNCERTAIN"
    assert "could not be determined" in v.evidence


def test_a_diagnostic_in_another_file_cannot_settle_this_claim():
    """Clippy analyses a CRATE. Without a file check a diagnostic in lib.rs would
    confirm a claim about main.rs at the same line number.

    It must also not merely ESCALATE. A sibling module's diagnostic is not
    "unlocatable" -- its location is perfectly well known and it is not ours -- so
    treating it as such made one `unwrap_used` anywhere in the crate block every
    refutation in every file. See `_unlocatable`.
    """
    other = str(CRATE / "src" / "other.rs")
    v = cc.verdict_for_claim(
        claim(), [diag("clippy::arithmetic_side_effects", RAW_MUL_LINE, file=other)],
        cc.OVERFLOW_LINTS, span=(34, 36))
    assert v.verdict == "FALSE_POSITIVE"


def test_an_unreadable_line_in_THIS_file_still_escalates():
    """The other half of the pair above: the `_mentions` narrowing must not also
    discard a diagnostic that IS about this file and merely cannot be placed."""
    bad = {"code": {"code": "clippy::arithmetic_side_effects"}, "message": "m",
           "spans": [{"file_name": LIB, "line_start": "thirty-five"}]}
    v = cc.verdict_for_claim(claim(), [bad], cc.OVERFLOW_LINTS, span=(34, 36))
    assert v.verdict == "UNCERTAIN"
    assert "could not be determined" in v.evidence


def test_no_span_means_no_refutation():
    """A refutation is scoped to the enclosing span. Without one there is nothing to
    rest it on, and widening to the whole crate would refute on silence across code
    the claim never referred to."""
    v = cc.verdict_for_claim(claim(), [], cc.OVERFLOW_LINTS, span=None)
    assert v.verdict == "UNCERTAIN"
    assert "enclosing scope could not be determined" in v.evidence


def test_none_diagnostics_never_refute():
    """"Could not tell" must never be conflated with "ran and was silent"."""
    v = cc.verdict_for_claim(claim(), None, cc.OVERFLOW_LINTS, span=(34, 36))
    assert v.verdict == "UNCERTAIN"
    assert v.source == "llm"


@pytest.mark.parametrize("malformed", [
    {"code": None, "spans": [{"file_name": LIB, "line_start": 35}]},
    {"code": {"code": 7}, "spans": [{"file_name": LIB, "line_start": 35}]},
    {"code": {"code": "clippy::arithmetic_side_effects"}, "spans": "nope"},
    {"code": {"code": "clippy::arithmetic_side_effects"},
     "spans": [{"file_name": None, "line_start": 35}]},
    {"code": {"code": "clippy::arithmetic_side_effects"},
     "spans": [{"file_name": LIB, "line_start": True}]},
])
def test_a_malformed_diagnostic_yields_a_verdict_not_an_exception(malformed):
    v = cc.verdict_for_claim(claim(), [malformed], cc.OVERFLOW_LINTS, span=(34, 36))
    assert v.verdict in ("UNCERTAIN", "FALSE_POSITIVE")


# --- the fail-safe cascade in _parse_stream ------------------------------------

def _stream(*records):
    import json
    return "\n".join(json.dumps(r) for r in records)


BUILT = {"reason": "compiler-artifact", "fresh": False,
         "target": {"src_path": LIB}}
FINISHED_OK = {"reason": "build-finished", "success": True}
A_MESSAGE = {"reason": "compiler-message",
             "message": diag("clippy::unwrap_used", UNWRAP_LINE)}


def test_a_successful_build_with_messages_parses():
    out = cc._parse_stream(_stream(BUILT, A_MESSAGE, FINISHED_OK), str(CRATE))
    assert out is not None and len(out) == 1


def test_a_successful_build_with_no_messages_is_genuinely_silent():
    """This is the case that licenses a FALSE_POSITIVE, so it must be reachable --
    a cascade that escalated on everything would be safe and useless."""
    assert cc._parse_stream(_stream(BUILT, FINISHED_OK), str(CRATE)) == []


@pytest.mark.parametrize("stream,why", [
    ("", "empty output"),
    ("not json at all\n", "unparseable"),
    (_stream(BUILT), "no build-finished: the run was cut short"),
    (_stream(BUILT, {"reason": "build-finished", "success": False}),
     "the crate did not compile, so there are no lints to be silent about"),
    (_stream({"reason": "compiler-artifact", "fresh": True}, FINISHED_OK),
     "nothing was compiled: a wholly-fresh build in a per-process target dir"),
])
def test_a_stream_that_proves_nothing_returns_none(stream, why):
    assert cc._parse_stream(stream, str(CRATE)) is None, why


def test_relative_span_paths_are_resolved_against_the_crate_root():
    """cargo reports `src/lib.rs`, relative to the MANIFEST -- not to the process's
    working directory, which during an audit is the audit root. Resolving against cwd
    matched nothing and every claim escalated: safe, and therefore silent."""
    relative = {"reason": "compiler-message",
                "message": {"code": {"code": "clippy::unwrap_used"}, "message": "m",
                            "spans": [{"file_name": os.path.join("src", "lib.rs"),
                                       "line_start": UNWRAP_LINE,
                                       "line_end": UNWRAP_LINE}]}}
    out = cc._parse_stream(_stream(BUILT, relative, FINISHED_OK), str(CRATE))
    assert out is not None
    assert cc.line_bounds(out[0], LIB) == (UNWRAP_LINE, UNWRAP_LINE)


def test_a_manifest_is_found_by_walking_up():
    assert cc._manifest_for(LIB) == str(CRATE / "Cargo.toml")


def test_no_manifest_means_escalate(tmp_path):
    """Guessing a workspace root would analyse a different crate than the claim is
    about, and another crate's diagnostic must never settle a claim here."""
    orphan = tmp_path / "loose.rs"
    orphan.write_text("fn f() {}\n", encoding="utf-8")
    assert cc._manifest_for(str(orphan)) is None
    assert cc.run_clippy(str(orphan), cc.PANIC_LINTS) is None


def test_a_missing_cargo_escalates(monkeypatch):
    monkeypatch.setattr(cc, "resolve_tool", lambda name: None)
    assert cc.run_clippy(LIB, cc.PANIC_LINTS) is None


def test_a_timeout_escalates(monkeypatch):
    import subprocess

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired("cargo", 1)

    monkeypatch.setattr(cc, "resolve_tool", lambda name: "/usr/bin/cargo")
    monkeypatch.setattr(cc.subprocess, "run", boom)
    assert cc.run_clippy(LIB, cc.PANIC_LINTS) is None


def test_the_audited_crates_target_dir_is_never_used(monkeypatch):
    """Two reasons, and both matter: a warm cache can report a build with nothing to
    say (indistinguishable from a clean crate), and writing into the audited repo's
    `target/` dirties the very tree the pipeline is reviewing."""
    seen = {}

    def fake_run(cmd, **kw):
        import subprocess
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(cc, "resolve_tool", lambda name: "/usr/bin/cargo")
    monkeypatch.setattr(cc.subprocess, "run", fake_run)
    cc.run_clippy(LIB, cc.PANIC_LINTS)
    cmd = seen["cmd"]
    assert "--target-dir" in cmd
    target = cmd[cmd.index("--target-dir") + 1]
    assert not os.path.abspath(target).startswith(os.path.abspath(str(CRATE)))
    assert cmd[0] == "/usr/bin/cargo"       # never a bare name
    assert "--force-warn" in cmd


# --- end to end, against real cargo -------------------------------------------

@needs_cargo
def test_force_warn_beats_the_crates_own_allow():
    """THE soundness property of this backend.

    The fixture crate carries `#![allow(clippy::unwrap_used)]`. Under plain `-W` that
    allow wins, clippy says nothing, and the audited repo collects a refutation
    carrying an authoritative `source: clippy` against itself. `--force-warn`
    overrides it -- the analogue of `enableTypeIgnoreComments: false` for pyright.
    """
    diags = cc.run_clippy(LIB, cc.PANIC_LINTS)
    assert diags is not None, "clippy could not run; the assertion below proves nothing"
    hits = [d for d in diags
            if cc.lint_code(d) == "clippy::unwrap_used"
            and cc.line_bounds(d, LIB) == (UNWRAP_LINE, UNWRAP_LINE)]
    assert hits, ("clippy reported no unwrap_used at the fixture's unwrap, despite "
                  "--force-warn; the crate's own #![allow] is winning")


@needs_cargo
@pytest.mark.parametrize("line,claim_type,expected", [
    (RAW_MUL_LINE, "overflow", "CONFIRMED"),
    (SATURATING_LINE, "overflow", "FALSE_POSITIVE"),
    (DISCARD_LINE, "error_swallow", "CONFIRMED"),
    (PROPAGATE_LINE, "error_swallow", "FALSE_POSITIVE"),
    (UNWRAP_LINE, "panic_path", "UNCERTAIN"),
    (CHECKED_MATCH_LINE, "panic_path", "FALSE_POSITIVE"),
])
def test_end_to_end_verdicts(line, claim_type, expected):
    from cca_checks.languages.rust import verdict_for_clippy_claim
    v = verdict_for_clippy_claim(Claim("E2E", LIB, line, claim_type))
    assert v.verdict == expected, f"{claim_type}@{line}: {v.evidence}"
