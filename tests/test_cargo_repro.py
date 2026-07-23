"""The Rust repro runner: the only way a `panic_path` claim can be CONFIRMED.

clippy deliberately cannot confirm one -- a lint sees `.unwrap()`, not whether it is
reachable with a value the caller controls. Executing the code is what settles that,
so this runner is the other half of that claim type rather than a convenience.

Its central guarantee is the one `repro_runner` states for pytest: an environment gap
must never manufacture evidence. `cargo test` exits non-zero for a failing test AND
for a crate that does not compile, and since confirmation only requires the predicted
string to appear in the output, a claim predicting a word that occurs in a compiler
error would otherwise be CONFIRMED by the build failing.
"""

import pathlib
import shutil
import subprocess

import pytest

from cca_checks import cargo_repro as cr

CRATE = pathlib.Path(__file__).parent / "fixtures" / "rust_clippy"
PANICS = str(CRATE / "tests" / "t_overflow_repro.rs")
PASSES = str(CRATE / "tests" / "t_passes.rs")
OVERFLOW_MSG = "attempt to multiply with overflow"

needs_cargo = pytest.mark.skipif(
    shutil.which("cargo") is None,
    reason="cargo is not installed; ci.yml installs it and "
           "tests/test_ci_contract.py asserts that it does",
)


# --- the fail-safe cascade, without shelling out ------------------------------

def test_a_missing_cargo_escalates(monkeypatch):
    monkeypatch.setattr(cr, "resolve_tool", lambda name: None)
    v = cr.run_repro("R", PANICS, OVERFLOW_MSG)
    assert v.verdict == "UNCERTAIN"
    assert "cargo unavailable" in v.evidence


def test_no_manifest_escalates(tmp_path, monkeypatch):
    """Guessing a workspace root would run a different crate's tests than the claim
    is about."""
    monkeypatch.setattr(cr, "resolve_tool", lambda name: "/usr/bin/cargo")
    loose = tmp_path / "t_loose.rs"
    loose.write_text("#[test] fn t() {}\n", encoding="utf-8")
    v = cr.run_repro("R", str(loose), OVERFLOW_MSG)
    assert v.verdict == "UNCERTAIN"
    assert "no Cargo.toml" in v.evidence


def test_a_build_failure_is_not_a_reproduction(monkeypatch):
    """THE defect this runner is shaped around.

    A crate that does not compile exits non-zero exactly as a failing test does, and
    a compiler error is not evidence about runtime behaviour. Worse: confirmation only
    needs the predicted string to appear anywhere in the output, so a claim predicting
    a word that occurs in a build log would be CONFIRMED by the build breaking.
    """
    monkeypatch.setattr(cr, "resolve_tool", lambda name: "/usr/bin/cargo")

    def fake_run(cmd, **kw):
        # The build (`--no-run`) fails, and its output happens to contain the
        # predicted string -- the trap.
        return subprocess.CompletedProcess(
            cmd, 101, stdout="", stderr=f"error: could not compile; {OVERFLOW_MSG}")

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    v = cr.run_repro("R", PANICS, OVERFLOW_MSG)
    assert v.verdict == "UNCERTAIN"
    assert "could not be built" in v.evidence
    assert "nothing was executed" in v.evidence


def test_the_build_is_checked_before_anything_runs(monkeypatch):
    """`--no-run` must be the FIRST invocation. Reversing the order would execute the
    target's code before establishing that the fixture even compiles."""
    seen = []
    monkeypatch.setattr(cr, "resolve_tool", lambda name: "/usr/bin/cargo")

    def fake_run(cmd, **kw):
        seen.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout="ok", stderr="")

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    cr.run_repro("R", PANICS, OVERFLOW_MSG)
    assert "--no-run" in seen[0]
    assert seen[0][0] == "/usr/bin/cargo"      # never a bare name
    assert "--test" in seen[0]


def test_a_timeout_escalates(monkeypatch):
    monkeypatch.setattr(cr, "resolve_tool", lambda name: "/usr/bin/cargo")

    def boom(*a, **kw):
        raise subprocess.TimeoutExpired("cargo", 1)

    monkeypatch.setattr(cr.subprocess, "run", boom)
    v = cr.run_repro("R", PANICS, OVERFLOW_MSG)
    assert v.verdict == "UNCERTAIN"
    assert "timed out" in v.evidence


def test_a_failure_without_a_predicted_error_does_not_confirm(monkeypatch):
    """"Something went wrong" is not "the predicted thing went wrong"."""
    monkeypatch.setattr(cr, "resolve_tool", lambda name: "/usr/bin/cargo")
    calls = []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        rc = 0 if "--no-run" in cmd else 101
        return subprocess.CompletedProcess(cmd, rc, stdout="panicked", stderr="")

    monkeypatch.setattr(cr.subprocess, "run", fake_run)
    v = cr.run_repro("R", PANICS, None)
    assert v.verdict == "UNCERTAIN"
    assert "no predicted error" in v.evidence


# --- end to end, against real cargo -------------------------------------------

@needs_cargo
def test_a_real_panic_confirms():
    v = cr.run_repro("R", PANICS, OVERFLOW_MSG)
    assert v.verdict == "CONFIRMED", v.evidence
    assert v.source == "cargo"
    assert OVERFLOW_MSG in v.evidence


@needs_cargo
def test_a_passing_test_does_not_confirm():
    """The negative control. Without it a runner that confirmed unconditionally would
    satisfy every other assertion in this file."""
    v = cr.run_repro("R", PASSES, OVERFLOW_MSG)
    assert v.verdict == "UNCERTAIN"
    assert "did not trigger the impact" in v.evidence


@needs_cargo
def test_the_wrong_predicted_message_does_not_confirm():
    v = cr.run_repro("R", PANICS, "attempt to divide by zero")
    assert v.verdict == "UNCERTAIN"
    assert "not with" in v.evidence


# --- CLI dispatch --------------------------------------------------------------

def test_the_cli_routes_a_rs_test_to_cargo(monkeypatch, capsys):
    import json

    from cca_checks import __main__ as cli
    monkeypatch.setitem(cli._REPRO_RUNNERS, ".rs",
                        lambda fid, test, exp: cr.make_verdict(
                            fid, "UNCERTAIN", "routed to cargo", "cargo"))
    assert cli.main(["repro", "--finding-id", "R", "--test", "t.rs"]) == 0
    assert json.loads(capsys.readouterr().out)["evidence"] == "routed to cargo"


def test_the_cli_still_routes_a_py_test_to_pytest(monkeypatch, capsys):
    import json

    from cca_checks import __main__ as cli
    monkeypatch.setitem(cli._REPRO_RUNNERS, ".py",
                        lambda fid, test, exp: cr.make_verdict(
                            fid, "UNCERTAIN", "routed to pytest", "pytest"))
    assert cli.main(["repro", "--finding-id", "R", "--test", "t.py"]) == 0
    assert json.loads(capsys.readouterr().out)["evidence"] == "routed to pytest"


def test_an_unknown_test_extension_escalates(capsys):
    """Defaulting to pytest would hand a `.go` file to `python -m pytest`, whose
    collection error is a non-zero exit the runner would then have to tell apart from
    a genuine failure."""
    import json

    from cca_checks import __main__ as cli
    assert cli.main(["repro", "--finding-id", "R", "--test", "t.go"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["verdict"] == "UNCERTAIN"
    assert "no repro runner" in out["evidence"]
