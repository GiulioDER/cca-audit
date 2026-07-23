"""Regression tests for the defects found by CCA-Audit's DEEP self-audit.

Each test names the finding it pins. They share one theme: the deterministic layer
must never let the repo under audit, a missing dependency, or its own arithmetic
decide a verdict.
"""

import os
import subprocess

import pytest

from cca_checks import property_check as pcheck
from cca_checks import pyright_check as pyc
from cca_checks import repro_runner as rr
from cca_checks import semgrep_check as sc
from cca_checks import toolpath
from cca_checks.claim import Claim

# See the note in tests/test_cli.py: the CLI now dispatches through the language
# backend, so a checker is stood down there rather than on the CLI module.
from cca_checks.languages import python as pyb
from cca_checks.properties import (
    PropertyViolation,
    assert_bounded,
    assert_monotonic_in,
    assert_round_trips,
    assert_scale_invariant,
)
from cca_checks.scope import enclosing_span

# --- SEC-001: analyzer binaries must not be resolved from the audited tree -----

def test_resolve_tool_refuses_a_binary_inside_the_audited_tree(tmp_path, monkeypatch):
    """A repo shipping `pyright.exe` in its root must not get code execution.

    Windows CreateProcess (and shutil.which) search the current directory before
    PATH, and during an audit the current directory is the repo under audit.
    """
    planted = tmp_path / "pyright"
    planted.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(toolpath.shutil, "which", lambda name, path=None: str(planted))
    assert toolpath.resolve_tool("pyright") is None


def test_resolve_tool_returns_an_absolute_path(tmp_path, monkeypatch):
    outside = tmp_path / "bin" / "pyright"
    outside.parent.mkdir()
    outside.write_text("#!/bin/sh\n", encoding="utf-8")
    workdir = tmp_path / "repo"
    workdir.mkdir()
    monkeypatch.chdir(workdir)
    monkeypatch.setattr(toolpath.shutil, "which", lambda name, path=None: str(outside))
    resolved = toolpath.resolve_tool("pyright")
    assert resolved is not None and os.path.isabs(resolved)


def test_missing_tool_still_reports_unavailable(monkeypatch):
    monkeypatch.setattr(toolpath.shutil, "which", lambda name, path=None: None)
    assert sc.run_semgrep("cfg.yaml", "x.py") is None
    assert pyc.run_pyright("x.py") is None


# --- SEC-004: the audited repo must not be able to suppress the scan -----------

def test_semgrep_disables_repo_controlled_suppression(monkeypatch):
    """`# nosemgrep` / .gitignore must not shrink a scan that licenses a refutation."""
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

    monkeypatch.setattr(sc, "resolve_tool", lambda name: "/usr/bin/semgrep")
    monkeypatch.setattr(sc.subprocess, "run", fake_run)
    sc.run_semgrep("cfg.yaml", "x.py")
    assert "--disable-nosem" in seen["cmd"]
    assert "--no-git-ignore" in seen["cmd"]
    assert seen["cmd"][0] == "/usr/bin/semgrep"  # never a bare name


# --- SEC-003 / STAKES-002: the audited repo must not supply pyright's config ---

def test_pyright_pins_its_own_analysis_config(monkeypatch, tmp_path):
    """A repo's pyrightconfig.json / `# type: ignore` must not silence the check."""
    captured = {}

    def fake_run(cmd, **kw):
        # --project <dir>; read back the config we generated.
        cfg = os.path.join(cmd[cmd.index("--project") + 1], "pyrightconfig.json")
        with open(cfg, encoding="utf-8") as fh:
            captured["config"] = fh.read()
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(pyc, "resolve_tool", lambda name: "/usr/bin/pyright")
    monkeypatch.setattr(pyc.subprocess, "run", fake_run)
    pyc.run_pyright(str(tmp_path / "x.py"))
    assert "--project" in captured["cmd"]
    assert '"enableTypeIgnoreComments": false' in captured["config"].replace("False", "false")
    assert "typeCheckingMode" in captured["config"]


# --- BUG-003: an environment fact must not confirm an undefined-symbol claim ---

def test_missing_import_is_not_a_definedness_rule():
    """`reportMissingImports` says pyright could not resolve an import from its
    search paths -- not that the symbol is undefined. definedness is exempt from
    the blindness probe, so a rule here is decisive with no backstop."""
    assert "reportMissingImports" not in pyc.DEFINEDNESS_RULES


def test_missing_import_escalates_instead_of_confirming():
    diags = [{"range": {"start": {"line": 0}}, "rule": "reportMissingImports",
              "message": 'Import "plug" could not be resolved'}]
    v = pyc.verdict_for_claim(
        Claim("D", "a.py", 1, "definedness"), diags, pyc.DEFINEDNESS_RULES)
    assert v.verdict == "UNCERTAIN"


# --- BUG-002: a diagnostic spanning several lines must match the cited line ----

def test_diagnostic_matches_anywhere_in_its_range():
    """pyright anchors at the start of the offending expression; an auditor cites
    the line carrying the access. For a multi-line expression they differ, and
    silence at the cited line is what licenses a FALSE_POSITIVE."""
    diags = [{"range": {"start": {"line": 13}, "end": {"line": 15}},
              "rule": "reportOptionalMemberAccess", "message": "m"}]
    for cited in (14, 15, 16):  # 1-based, inside [start+1, end+1]
        v = pyc.verdict_for_claim(
            Claim("N", "a.py", cited, "nullability"), diags, pyc.NULLABILITY_RULES)
        assert v.verdict == "CONFIRMED", cited


def test_diagnostic_outside_its_range_still_refutes():
    diags = [{"range": {"start": {"line": 13}, "end": {"line": 15}},
              "rule": "reportOptionalMemberAccess", "message": "m"}]
    v = pyc.verdict_for_claim(
        Claim("N", "a.py", 40, "nullability"), diags, pyc.NULLABILITY_RULES,
        blind_probe=lambda _file, _line: False)
    assert v.verdict == "FALSE_POSITIVE"


# --- BUG-005: a malformed diagnostic must not escape as a traceback ------------

@pytest.mark.parametrize("bad", [
    {"range": {"start": {"line": None}}, "rule": "reportUndefinedVariable"},
    {"range": {"start": {"line": "3"}}, "rule": "reportUndefinedVariable"},
    {"range": {"start": [1, 2]}, "rule": "reportUndefinedVariable"},
    {"range": ["start"], "rule": "reportUndefinedVariable"},
    {"range": {"start": {"line": True}}, "rule": "reportUndefinedVariable"},
])
def test_malformed_diagnostic_yields_a_verdict_not_an_exception(bad):
    v = pyc.verdict_for_claim(
        Claim("D", "a.py", 5, "definedness"), [bad], pyc.DEFINEDNESS_RULES)
    assert v.verdict == "UNCERTAIN"


# --- NUM-004: a declared relation that cannot hold must not confirm ------------

def test_bounded_rejects_swapped_bounds():
    """lo > hi makes `lo <= y <= hi` unsatisfiable, so the first example would
    CONFIRM against arbitrary correct code -- the operand-order class this tool
    exists to catch, turned into a binding verdict."""
    with pytest.raises(ValueError):
        assert_bounded(lambda: 0.5, (), lo=1.0, hi=0.0)


def test_bounded_still_accepts_a_correct_result():
    assert_bounded(lambda: 0.5, (), lo=0.0, hi=1.0)


# --- NUM-007: the anti-tautology guarantee must actually hold ------------------

@pytest.mark.parametrize("factor,indices", [
    (1.0, (0, 1)),   # scaled == args: fn(args) == fn(args), true on any code
    (10.0, ()),      # nothing scaled: same tautology
    (10.0, (0, 0, 1)),  # duplicate index compounds to factor**2
])
def test_scale_invariant_rejects_degenerate_configurations(factor, indices):
    with pytest.raises(ValueError):
        assert_scale_invariant(lambda a, b: a / b + a, (4.0, 2.0), factor, indices)


def test_scale_invariant_still_catches_a_real_violation():
    with pytest.raises(PropertyViolation):
        assert_scale_invariant(lambda a, b: a / b + a, (4.0, 2.0), 10.0, (0, 1))


# --- BUG-004: the harness must not blame the target for its own overflow -------

def test_scale_invariant_overflow_is_not_a_property_violation():
    """`a/b` is exactly scale-invariant; scaling 1e300 by 1e10 overflows to inf
    and inf/inf = nan. ValueError (-> UNCERTAIN) is the honest verdict."""
    with pytest.raises(ValueError):
        assert_scale_invariant(lambda a, b: a / b, (1e300, 1e300), 1e10, (0, 1))


def test_monotonic_overflow_is_not_a_property_violation():
    with pytest.raises(ValueError):
        assert_monotonic_in(lambda x: x, (1e308,), 0, "increasing", 1e308)


# --- NUM-002: the probe must stay inside the declared input domain -------------

def test_monotonic_respects_the_declared_domain_bound():
    """A function correct on [0.01, 1.0] with different behaviour above it must
    not be falsified by a probe that steps past the declared bound."""
    def fee(vol):
        return 50.0 if vol > 1.0 else 10.0 - 5.0 * vol

    assert_monotonic_in(fee, (1.0,), 0, "decreasing", 0.1, domain_hi=1.0)
    with pytest.raises(PropertyViolation):
        assert_monotonic_in(fee, (1.0,), 0, "decreasing", 0.1)  # unbounded: escapes


def test_monotonic_violation_names_both_evaluation_points():
    with pytest.raises(PropertyViolation) as exc:
        assert_monotonic_in(lambda x: -x, (1.0,), 0, "increasing", 0.5)
    assert exc.value.inputs == ((1.0,), (1.5,))


# --- NUM-006: strict mode must catch a term that was dropped entirely ----------

def test_strict_monotonic_catches_a_dropped_term():
    """`mu - 0.5*vol**2` reduced to `mu` is trivially NON-strictly monotonic, so
    the default test passes a unit/scale bug that removed the term."""
    def dropped(mu, vol):
        return mu

    assert_monotonic_in(dropped, (0.1, 0.3), 1, "decreasing", 0.1)  # passes: no signal
    with pytest.raises(PropertyViolation):
        assert_monotonic_in(dropped, (0.1, 0.3), 1, "decreasing", 0.1, strict=True)


# --- NUM-001: a correct quantizing conversion must not be falsified ------------

def test_round_trip_accepts_a_correct_quantizing_converter():
    """Money <-> integer minor units is lossy by design. Without a declared
    quantum this helper CONFIRMED against a correct converter -- and the example
    that fails, x=1.625 -> 162 cents -> 1.62, is the one shipped in the agent
    prompt template."""
    def to_minor(amount):
        return round(amount * 100)

    def from_minor(cents):
        return cents / 100.0

    for x in (1.625, 1.005, 0.014, 123.456):
        assert_round_trips(to_minor, from_minor, x, quantum=0.01)


def test_round_trip_still_catches_a_genuinely_broken_converter():
    with pytest.raises(PropertyViolation):
        # drops a factor of 10 on the way back
        assert_round_trips(lambda a: round(a * 100), lambda c: c / 1000.0,
                           12.34, quantum=0.01)


def test_round_trip_defaults_to_exact():
    with pytest.raises(PropertyViolation):
        assert_round_trips(lambda a: round(a * 100), lambda c: c / 100.0, 1.625)


# --- NUM-003: evidence must not pair one bug's input with another's property ---

def test_multiple_falsifying_examples_escalate(monkeypatch):
    """Hypothesis reports multiple bugs per @given by default. Two independent
    first-match regexes would pair bug A's shrunk input with bug B's property
    line, yielding a counterexample that does not violate the stated property."""
    out = (
        "Falsifying example: test_bounded(x=6.0,)\n"
        "ZeroDivisionError\n"
        "\n"
        "Falsifying example: test_bounded(x=4.0,)\n"
        "PROPERTY bounded violated | inputs=(4.0,) | observed=9.0 | required=0 <= result <= 1\n"
    )
    monkeypatch.setattr(
        pcheck.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0] if a else [], 1, out, ""))
    v = pcheck.run_properties("NUM-1", "t_props.py")
    assert v.verdict == "UNCERTAIN"
    assert "multiple falsifying examples" in v.evidence


def test_multiple_falsifying_examples_without_blank_lines_escalate(monkeypatch):
    """pytest's grouped-exception output separates sub-reports with `+---- N ----`
    and no blank line -- the shape the old banner regex ran straight through,
    swallowing the summary footer and hiding the second banner."""
    out = (
        "+---------------- 1 ----------------\n"
        "Falsifying example: test_bounded(x=6.0,)\n"
        "ZeroDivisionError: division by zero\n"
        "+---------------- 2 ----------------\n"
        "Falsifying example: test_bounded(x=4.0,)\n"
        "PROPERTY bounded violated | inputs=(4.0,) | observed=9.0 | required=0 <= result <= 1\n"
        "=========== short test summary info ===========\n"
        "1 failed in 3.01s\n"
    )
    monkeypatch.setattr(
        pcheck.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0] if a else [], 1, out, ""))
    v = pcheck.run_properties("NUM-1", "t_props.py")
    assert v.verdict == "UNCERTAIN"
    assert "multiple falsifying examples" in v.evidence


def test_confirmed_evidence_excludes_the_pytest_footer(monkeypatch):
    out = (
        "Falsifying example: test_bounded(x=4.0,)\n"
        "PROPERTY bounded violated | inputs=(4.0,) | observed=9.0 | required=0 <= result <= 1\n"
        "=========== short test summary info ===========\n"
        "1 failed in 3.01s\n"
    )
    monkeypatch.setattr(
        pcheck.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0] if a else [], 1, out, ""))
    v = pcheck.run_properties("NUM-1", "t_props.py")
    assert v.verdict == "CONFIRMED"
    assert "short test summary" not in v.evidence


def test_the_same_banner_echoed_twice_is_one_bug(monkeypatch):
    """Regression: the ambiguity check counts DISTINCT banners, not occurrences.

    pytest and Hypothesis echo the same "Falsifying example" in more than one place
    depending on version -- the failure body, the -r summary, the explain phase. A
    raw occurrence count therefore measured the toolchain rather than the code under
    test, so an ordinary single-bug run escalated to UNCERTAIN on some installs and
    confirmed on others. CI caught this on all four Python versions after a local
    run of the same suite passed.
    """
    banner = "Falsifying example: test_bounded(x=4.0,)\n"
    out = (
        banner
        + "PROPERTY bounded violated | inputs=(4.0,) | observed=9.0 | required=0 <= result <= 1\n"
        + "\n=========== short test summary info ===========\n"
        + "E   " + banner  # the SAME bug, echoed with pytest's gutter prefix
        + "1 failed in 4.20s\n"
    )
    monkeypatch.setattr(
        pcheck.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0] if a else [], 1, out, ""))
    v = pcheck.run_properties("NUM-1", "t_props.py")
    assert v.verdict == "CONFIRMED", v.evidence


def test_confirmed_evidence_is_byte_identical_across_runs(monkeypatch):
    """A CONFIRMED artifact must reproduce exactly.

    The escalation path embeds pytest's output tail, which carries the run
    duration, so an artifact that wrongly fell into it stopped being reproducible
    between two identical runs.
    """
    def settle(duration):
        out = (
            "Falsifying example: test_bounded(x=4.0,)\n"
            "PROPERTY bounded violated | inputs=(4.0,) | observed=9.0 | required=0 <= result <= 1\n"
            f"1 failed in {duration}s\n"
        )
        monkeypatch.setattr(
            pcheck.subprocess, "run",
            lambda *a, **kw: subprocess.CompletedProcess(a[0] if a else [], 1, out, ""))
        return pcheck.run_properties("NUM-1", "t_props.py")

    a, b = settle("4.20"), settle("4.23")
    assert a.verdict == b.verdict == "CONFIRMED"
    assert a.evidence == b.evidence


def test_single_falsifying_example_still_confirms(monkeypatch):
    out = (
        "Falsifying example: test_bounded(x=4.0,)\n"
        "PROPERTY bounded violated | inputs=(4.0,) | observed=9.0 | required=0 <= result <= 1\n"
    )
    monkeypatch.setattr(
        pcheck.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(a[0] if a else [], 1, out, ""))
    v = pcheck.run_properties("NUM-1", "t_props.py")
    assert v.verdict == "CONFIRMED"


# --- ENV-001: a missing dependency must not manufacture evidence ---------------

def test_missing_pytest_does_not_confirm_a_repro(monkeypatch):
    """`python -m pytest` with pytest absent exits rc=1 -- the same code as a real
    test failure -- so a claim predicting 'No module named' would be CONFIRMED by
    the absence of pytest."""
    monkeypatch.setattr(
        rr.subprocess, "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            a[0] if a else [], 1, "", "C:\\python.exe: No module named pytest\n"))
    v = rr.run_repro("BUG-1", "t.py", expected_error="No module named")
    assert v.verdict == "UNCERTAIN"
    assert "pytest not installed" in v.evidence


def test_repro_is_side_effect_free(monkeypatch):
    seen = {}
    monkeypatch.setattr(
        rr.subprocess, "run",
        lambda cmd, **kw: seen.setdefault("cmd", cmd) and None
        or subprocess.CompletedProcess(cmd, 0, "", ""))
    rr.run_repro("BUG-1", "t.py", expected_error=None)
    assert "no:cacheprovider" in seen["cmd"]


# --- STAKES-006 / SEC-006: an unsettleable coordinate must escalate ------------

def _cli(argv, capsys):
    import json as _json

    from cca_checks import __main__ as cli
    assert cli.main(argv) == 0
    return _json.loads(capsys.readouterr().out.strip())


@pytest.fixture
def audit_root(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "target.py").write_text("x = 1\n" * 5, encoding="utf-8")
    (tmp_path / "subdir").mkdir()
    return tmp_path


@pytest.mark.parametrize("argv_tail,expected", [
    (["--file", "target.py", "--line", "99999"], "past the end"),
    (["--file", "target.py", "--line", "0"], "not a valid 1-based line"),
    (["--file", "target.py", "--line", "-5"], "not a valid 1-based line"),
    (["--file", "nope.py", "--line", "1"], "does not exist"),
    (["--file", "subdir", "--line", "1"], "is a directory"),
])
def test_unsettleable_coordinate_escalates_instead_of_refuting(
        argv_tail, expected, audit_root, capsys):
    """No diagnostic can ever match such a coordinate, so every checker would read
    the resulting silence as evidence and emit a confident FALSE_POSITIVE carrying
    an authoritative source -- rewarding a hallucinated line number with a
    refutation on a file that may well contain the defect."""
    out = _cli(["check", "--claim-type", "definedness", "--finding-id", "X"] + argv_tail,
               capsys)
    assert out["verdict"] == "UNCERTAIN"
    # source must NOT be a tool name: the agent contract forbids overturning a
    # tool-sourced verdict, and nothing was actually checked here.
    assert out["source"] == "llm"
    assert expected in out["evidence"]


def test_file_outside_the_audit_root_escalates(audit_root, tmp_path_factory, capsys):
    outside = tmp_path_factory.mktemp("elsewhere") / "other.py"
    outside.write_text("y = 2\n", encoding="utf-8")
    out = _cli(["check", "--claim-type", "definedness", "--finding-id", "X",
                "--file", str(outside), "--line", "1"], capsys)
    assert out["verdict"] == "UNCERTAIN"
    assert "outside the audit root" in out["evidence"]


def test_a_valid_coordinate_still_reaches_the_checker(audit_root, capsys, monkeypatch):
    """The gate must not swallow legitimate claims."""
    monkeypatch.setattr(pyb, "run_pyright", lambda path: [])
    out = _cli(["check", "--claim-type", "definedness", "--finding-id", "X",
                "--file", "target.py", "--line", "3"], capsys)
    assert out["verdict"] == "FALSE_POSITIVE"


# --- BUG-001: catalog absence must not be proof of sink absence ----------------

def _write(tmp_path, name, body):
    p = tmp_path / name
    p.write_text(body, encoding="utf-8")
    return str(p)


needs_semgrep = pytest.mark.skipif(
    __import__("shutil").which("semgrep") is None, reason="semgrep not installed")


@needs_semgrep
@pytest.mark.parametrize("sink_class,body", [
    # Real sinks that the hand-written strict list did not name. Each must at
    # worst escalate -- never be refuted as "the premise does not hold".
    ("command", "import subprocess\n\n\ndef f(host):\n"
                "    return subprocess.getoutput('ping -c1 ' + host)\n"),
    ("path", "import pathlib\n\n\ndef f(name):\n"
             "    return pathlib.Path('/data').joinpath(name).open().read()\n"),
    ("sql", "import pandas as pd\n\n\ndef f(user, conn):\n"
            "    return pd.read_sql(\"select * from u where n='%s'\" % user, conn)\n"),
])
def test_unlisted_real_sink_is_not_refuted(sink_class, body, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, "s.py", body)
    v = sc.verdict_for_taint(Claim("T", path, 5, "taint", sink_class=sink_class))
    assert v.verdict != "FALSE_POSITIVE", v.evidence


@needs_semgrep
@pytest.mark.parametrize("sink_class", ["command", "path", "code_exec", "sql"])
def test_benign_code_is_still_refuted(sink_class, tmp_path, monkeypatch):
    """The control for the test above. Widening the loose tier until ordinary code
    matches would make the refutation path unreachable -- trading a false
    refutation for a checker that can never refute anything."""
    monkeypatch.chdir(tmp_path)
    path = _write(tmp_path, "b.py", (
        "def f(items, text, mapping):\n"
        "    cleaned = text.replace('-', '_')\n"
        "    copied = mapping.copy()\n"
        "    items.remove(cleaned)\n"
        "    return copied, items\n"
    ))
    v = sc.verdict_for_taint(Claim("T", path, 3, "taint", sink_class=sink_class))
    assert v.verdict == "FALSE_POSITIVE", v.evidence


# --- BUG-007: a BOM'd source file is valid Python and must be readable ---------

def test_enclosing_span_handles_a_utf8_bom(tmp_path):
    p = tmp_path / "bom.py"
    p.write_bytes(b"\xef\xbb\xbfdef f(x):\n    return x\n")
    assert enclosing_span(str(p), 2) == (1, 2)
