"""`capabilities` is what stops the agent prompts from carrying a routing table.

`cca-fp-check.md` is COPIED into `.claude/agents/` while `cca_checks` is
pip-INSTALLED, so a claim-type list written in the prompt drifts from the one in the
package -- the back-compat `definedness` alias in `__main__` exists because they
already did once. With two languages the prompt would also have to encode which claim
types apply to which extension, and it still could not know whether `cargo` exists on
the machine it is running on.

So the prompt asks. These tests pin the contract it asks against.
"""

import json
import shutil

import pytest

from cca_checks import __main__ as cli
from cca_checks import languages


def run(capsys, argv):
    assert cli.main(argv) == 0
    return json.loads(capsys.readouterr().out.strip())


@pytest.fixture
def files(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "svc.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "main.rs").write_text("fn main() {}\n", encoding="utf-8")
    (tmp_path / "app.ts").write_text("const a = 1;\n", encoding="utf-8")
    return tmp_path


def test_python_reports_its_claim_types(capsys, files):
    out = run(capsys, ["capabilities", "--file", "svc.py"])
    assert out["language"] == "python"
    assert "definedness" in out["claim_types"]
    assert "taint" in out["claim_types"]


def test_rust_reports_its_own_vocabulary(capsys, files):
    """Not Python's. `definedness`/`type`/`nullability` are absent by design -- the
    code compiled, so they would refute by construction."""
    out = run(capsys, ["capabilities", "--file", "main.rs"])
    assert out["language"] == "rust"
    assert "panic_path" in out["claim_types"]
    assert "clock_leak" in out["claim_types"]
    for absent in ("definedness", "type", "nullability"):
        assert absent not in out["claim_types"]


def test_an_uncovered_language_reports_nothing_and_says_why(capsys, files):
    out = run(capsys, ["capabilities", "--file", "app.ts"])
    assert out["language"] is None
    assert out["claim_types"] == []
    assert "no deterministic backend" in out["reason"]


def test_capabilities_matches_the_registry(capsys, files):
    """The output IS the registry, not a description of it. A hand-written answer
    here would be the very copy this command exists to delete."""
    out = run(capsys, ["capabilities", "--file", "main.rs"])
    backend = languages.resolve("main.rs")
    assert out["claim_types"] == sorted(backend.claim_types)


def test_a_missing_file_still_answers(capsys, files):
    """Capabilities is about the INSTALLATION and the extension, not about the file's
    contents -- an agent asks it while triaging a finding, before it has necessarily
    confirmed the path. Refusing here would send it to Phase 2 for the wrong reason."""
    out = run(capsys, ["capabilities", "--file", "does_not_exist.rs"])
    assert out["language"] == "rust"


def test_an_unavailable_tool_is_reported_not_subtracted(capsys, files, monkeypatch):
    """An agent that sees `overflow` listed as unavailable knows to escalate it. One
    that never sees it at all cannot tell that from a claim type nobody supports."""
    from cca_checks.languages import rust
    monkeypatch.setattr(rust, "resolve_tool", lambda name: None)
    out = run(capsys, ["capabilities", "--file", "main.rs"])
    assert "overflow" in out["claim_types"], "must not vanish from the list"
    assert "cargo" in out["unavailable"]["overflow"]


def test_a_missing_grammar_is_reported_against_clock_leak(capsys, files, monkeypatch):
    from cca_checks import treesitter as ts
    ts._parser.cache_clear()
    monkeypatch.setitem(ts._GRAMMAR_MODULES, "rust", "tree_sitter_no_such_grammar")
    out = run(capsys, ["capabilities", "--file", "main.rs"])
    ts._parser.cache_clear()
    assert "not installed" in out["unavailable"]["clock_leak"]


@pytest.mark.skipif(shutil.which("cargo") is None, reason="cargo not installed")
def test_nothing_is_unavailable_when_the_toolchain_is_present(capsys, files):
    """The negative case. If `unavailable` were populated unconditionally, every
    assertion above would pass while the command reported the whole layer as broken."""
    out = run(capsys, ["capabilities", "--file", "main.rs"])
    assert "overflow" not in out["unavailable"]


def test_capabilities_is_not_shaped_like_a_verdict(capsys, files):
    """It reports what the installation can do, not what is true of the code. Giving
    it a `verdict` field would let it be pasted into an evidence table as one."""
    out = run(capsys, ["capabilities", "--file", "svc.py"])
    assert "verdict" not in out
    assert "evidence" not in out
