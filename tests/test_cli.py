import json

import pytest

from cca_checks import __main__ as cli
from cca_checks import pyright_check as pc


@pytest.fixture
def no_pyright(monkeypatch):
    """Make run_pyright report the tool as unavailable, so the CLI never shells out."""
    monkeypatch.setattr(cli, "run_pyright", lambda path: None)


def run(capsys, argv):
    assert cli.main(argv) == 0
    return json.loads(capsys.readouterr().out.strip())


def test_check_nullability_emits_a_verdict(capsys, no_pyright):
    out = run(capsys, ["check", "--claim-type", "nullability",
                       "--finding-id", "BUG-1", "--file", "svc.py", "--line", "9"])
    assert out == {"finding_id": "BUG-1", "verdict": "UNCERTAIN",
                   "evidence": "pyright unavailable; falling back to LLM", "source": "llm"}


def test_check_type_emits_a_verdict(capsys, no_pyright):
    out = run(capsys, ["check", "--claim-type", "type",
                       "--finding-id", "BUG-2", "--file", "svc.py", "--line", "4"])
    assert out == {"finding_id": "BUG-2", "verdict": "UNCERTAIN",
                   "evidence": "pyright unavailable; falling back to LLM", "source": "llm"}


def test_definedness_alias_matches_the_generic_subcommand(capsys, no_pyright):
    alias = run(capsys, ["definedness", "--finding-id", "D", "--file", "s.py", "--line", "1"])
    generic = run(capsys, ["check", "--claim-type", "definedness",
                           "--finding-id", "D", "--file", "s.py", "--line", "1"])
    assert alias == generic


def test_check_refutes_a_clean_definedness_claim(capsys, monkeypatch):
    monkeypatch.setattr(cli, "run_pyright", lambda path: [])
    out = run(capsys, ["check", "--claim-type", "definedness",
                       "--finding-id", "D", "--file", "s.py", "--line", "3"])
    assert out["verdict"] == "FALSE_POSITIVE"
    assert out["evidence"] == "pyright: no undefined-symbol diagnostic @ s.py:3"


def test_check_confirms_a_nullability_claim(capsys, monkeypatch):
    monkeypatch.setattr(cli, "run_pyright", lambda path: [
        {"range": {"start": {"line": 8}}, "rule": "reportOptionalMemberAccess",
         "message": "\"token\" is not a known attribute of \"None\""}
    ])
    out = run(capsys, ["check", "--claim-type", "nullability",
                       "--finding-id", "N", "--file", "svc.py", "--line", "9"])
    assert out["verdict"] == "CONFIRMED"
    assert out["source"] == "pyright"
    assert "reportOptionalMemberAccess" in out["evidence"]


def test_check_confirms_a_type_claim(capsys, monkeypatch):
    monkeypatch.setattr(cli, "run_pyright", lambda path: [
        {"range": {"start": {"line": 8}}, "rule": "reportArgumentType",
         "message": "Argument of type \"str\" cannot be assigned to parameter \"x\" of type \"int\" in function \"foo\""}
    ])
    out = run(capsys, ["check", "--claim-type", "type",
                       "--finding-id", "TYPE-1", "--file", "svc.py", "--line", "9"])
    assert out["verdict"] == "CONFIRMED"
    assert out["source"] == "pyright"
    assert "reportArgumentType" in out["evidence"]


def test_unknown_claim_type_is_rejected(no_pyright):
    with pytest.raises(SystemExit):
        cli.main(["check", "--claim-type", "taint",
                  "--finding-id", "T", "--file", "s.py", "--line", "1"])


def test_symbol_lands_in_proposition_not_a_positional_slot(monkeypatch, capsys, no_pyright):
    captured = {}
    real = pc.verdict_for_claim

    def spy(claim, diags, rules, blind_probe=None):
        captured["claim"] = claim
        return real(claim, diags, rules, blind_probe)

    monkeypatch.setattr(cli, "verdict_for_claim", spy)
    run(capsys, ["check", "--claim-type", "definedness", "--finding-id", "D",
                 "--file", "s.py", "--line", "1", "--symbol", "RISK_CAP"])
    claim = captured["claim"]
    assert claim.claim_type == "definedness"
    assert claim.proposition == "RISK_CAP"


def test_repro_subcommand_still_works(capsys, monkeypatch):
    from cca_checks.claim import make_verdict
    monkeypatch.setattr(cli, "run_repro",
                        lambda fid, test, exp: make_verdict(fid, "UNCERTAIN", "stub", "pytest"))
    out = run(capsys, ["repro", "--finding-id", "R", "--test", "t.py"])
    assert out["finding_id"] == "R"
    assert out["source"] == "pytest"
