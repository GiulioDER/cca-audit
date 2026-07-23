"""No claim about a file we have no deterministic backend for may be settled.

THE DEFECT THIS GUARDS. The Python assumption was enforced in only two of the five
`check` claim types. `clock_check` and `semgrep_check` test `.endswith(".py")`
explicitly; `type` and `nullability` failed closed only *by accident*, because the
blindness probe escalates when `ast.parse` chokes on non-Python source; and
`definedness` -- which `pyright_check.TYPE_DEPENDENT_CLAIMS` deliberately exempts
from that probe -- had no guard at all. Measured, before the fix:

    $ python -m cca_checks check --claim-type definedness --file probe.rs --line 3
    {"verdict": "FALSE_POSITIVE",
     "evidence": "pyright: no undefined-symbol diagnostic @ probe.rs:3",
     "source": "pyright"}

pyright parses a `.rs` file as Python and reports syntax errors, none of which fall
under DEFINEDNESS_RULES; `enclosing_span` then raises and is swallowed; and the
function falls through to a refutation. Per the fp-check protocol a verdict carrying
a tool artifact may not be overturned downstream, so that refutation is binding and
a real defect is dropped with nobody left to look again.

WHY THIS TEST IS SHAPED THE WAY IT IS.

It asserts the INVARIANT over `CLAIM_TYPES`, not the one claim type that leaked --
the same reasoning as test_dev_extra_completeness.py. Pinning `definedness` would
pass forever while claim type six ships with the same hole.

It MOCKS the tools into their "ran, and was genuinely silent" answer rather than
letting them run. Silence is the state that licenses a FALSE_POSITIVE, so that is
the state the guard has to be tested against -- and a machine without pyright
installed would otherwise return UNCERTAIN for tool-unavailable reasons and turn
this file green while the hole is wide open. A guard whose own test passes for the
wrong reason is worth less than no test.
"""

import json

import pytest

from cca_checks import __main__ as cli
from cca_checks import languages, semgrep_check
from cca_checks.languages import python as pyb

DECISIVE = ("CONFIRMED", "FALSE_POSITIVE")

# Languages NO backend covers. Rust was the motivating case and is now covered, so it
# has moved to `test_a_covered_language_still_escalates_an_undeclared_claim_type`
# below -- which is the half of the guard that keeps the NEXT language safe.
UNCOVERED = [
    ("app.ts", "function main() {\n  const a = 1;\n\n\n  console.log(a);\n}\n"),
    ("main.go", "package main\n\nfunc main() {\n\ta := 1\n\t_ = a\n}\n"),
    ("lib.rb", "def main\n  a = 1\n\n\n  puts a\nend\n"),
]

# A file in a language a backend DOES cover. Its backend settles `clock_leak` and
# nothing else, so every other claim type must still escalate here.
COVERED_RUST = ("main.rs", 'fn main() {\n    let a = 1;\n\n\n    println!("{}", a);\n}\n')


@pytest.fixture
def silent_tools(monkeypatch):
    """Every external analyzer reports "I ran, and I matched nothing".

    Not "the tool is missing" -- that already escalates for unrelated reasons and
    would mask the defect. `[]` is the answer that licenses a refutation.
    """
    monkeypatch.setattr(pyb, "run_pyright", lambda path: [])
    monkeypatch.setattr(semgrep_check, "run_semgrep", lambda config, path: [])


@pytest.fixture
def in_tmp(tmp_path, monkeypatch):
    """The CLI refuses a claim file outside the audit root, so cwd must be the root."""
    monkeypatch.chdir(tmp_path)
    for name, body in [*UNCOVERED, COVERED_RUST]:
        (tmp_path / name).write_text(body, encoding="utf-8")
    return tmp_path


def _verdict(capsys, argv):
    assert cli.main(argv) == 0
    return json.loads(capsys.readouterr().out.strip())


@pytest.mark.parametrize("claim_type", cli.CLAIM_TYPES)
@pytest.mark.parametrize("filename", [name for name, _ in UNCOVERED])
def test_no_decisive_verdict_on_a_language_without_a_backend(
    claim_type, filename, capsys, in_tmp, silent_tools
):
    out = _verdict(capsys, [
        "check", "--claim-type", claim_type,
        # A sink class is supplied so `taint` reaches its checker rather than
        # escalating on an unsupported class -- the guard, not a missing argument,
        # has to be what stops it.
        "--sink-class", "sql",
        "--finding-id", "GUARD-1", "--file", filename, "--line", "3",
    ])
    assert out["verdict"] not in DECISIVE, (
        f"{claim_type} settled a claim about {filename} with a binding "
        f"{out['verdict']} (source={out['source']}): {out['evidence']}"
    )


def _undeclared_by(filename):
    """Claim types the CLI accepts that this file's backend does NOT settle.

    Derived from the registry rather than listed. A hardcoded exclusion here silently
    stops testing anything the backend later adopts -- which is exactly what happened
    when the Rust backend grew its clippy claim types: the list still said "everything
    but clock_leak", so five claim types the backend now settles were being asserted
    to escalate, and the test failed for a reason that was not a defect.
    """
    backend = languages.resolve(filename)
    return sorted(set(cli.CLAIM_TYPES) - set(backend.claim_types))


@pytest.mark.parametrize("claim_type", _undeclared_by(COVERED_RUST[0]))
def test_a_covered_language_still_escalates_an_undeclared_claim_type(
    claim_type, capsys, in_tmp, silent_tools
):
    """The second half of the guard, and the half that protects the NEXT language.

    The Rust backend covers `.rs`, but not every claim type. Nothing it does not
    declare may be routed to a Python checker just because the extension resolved --
    `definedness` on a `.rs` file is precisely the leak this file was opened for, and
    it must stay closed now that `.rs` HAS a backend.
    """
    out = _verdict(capsys, [
        "check", "--claim-type", claim_type, "--sink-class", "sql",
        "--finding-id", "GUARD-4", "--file", COVERED_RUST[0], "--line", "3",
    ])
    assert out["verdict"] == "UNCERTAIN", out["evidence"]
    assert "does not settle" in out["evidence"]


@pytest.mark.parametrize("filename", [name for name, _ in UNCOVERED])
def test_the_escalation_names_the_language_as_the_reason(
    filename, capsys, in_tmp, silent_tools
):
    """An UNCERTAIN that does not say why is indistinguishable from a tool outage.

    The pipeline routes UNCERTAIN to a human, and that human needs to know whether
    to install something or to stop expecting coverage here at all.
    """
    out = _verdict(capsys, [
        "check", "--claim-type", "definedness",
        "--finding-id", "GUARD-2", "--file", filename, "--line", "3",
    ])
    assert out["verdict"] == "UNCERTAIN"
    assert "language" in out["evidence"].lower()


def test_python_is_still_settled(capsys, tmp_path, monkeypatch):
    """The guard must not be a blanket off-switch.

    A guard that escalates everything satisfies every assertion above and silently
    deletes the whole deterministic layer, which is a worse outcome than the defect
    it replaces.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "svc.py").write_text("x = 1\n" * 50, encoding="utf-8")
    monkeypatch.setattr(pyb, "run_pyright", lambda path: [])
    out = _verdict(capsys, [
        "check", "--claim-type", "definedness",
        "--finding-id", "GUARD-3", "--file", "svc.py", "--line", "3",
    ])
    assert out["verdict"] == "FALSE_POSITIVE"
    assert out["source"] == "pyright"
