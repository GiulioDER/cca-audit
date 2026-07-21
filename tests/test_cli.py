import json

import pytest

from cca_checks import __main__ as cli
from cca_checks import pyright_check as pc
from cca_checks.claim import make_verdict


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
        cli.main(["check", "--claim-type", "invalid_type",
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
    monkeypatch.setattr(cli, "run_repro",
                        lambda fid, test, exp: make_verdict(fid, "UNCERTAIN", "stub", "pytest"))
    out = run(capsys, ["repro", "--finding-id", "R", "--test", "t.py"])
    assert out["finding_id"] == "R"
    assert out["source"] == "pytest"


def test_check_taint_refutes_when_no_sink(capsys, monkeypatch):
    monkeypatch.setattr(cli, "verdict_for_taint",
                        lambda claim: make_verdict(claim.finding_id, "FALSE_POSITIVE",
                                                   "semgrep: no sql sink in the enclosing scope",
                                                   "semgrep"))
    out = run(capsys, ["check", "--claim-type", "taint", "--sink-class", "sql",
                       "--finding-id", "T-1", "--file", "svc.py", "--line", "9"])
    assert out["verdict"] == "FALSE_POSITIVE"
    assert out["source"] == "semgrep"


def test_check_taint_passes_the_sink_class_through(capsys, monkeypatch):
    captured = {}

    def spy(claim):
        captured["claim"] = claim
        return make_verdict(claim.finding_id, "UNCERTAIN", "stub", "semgrep")

    monkeypatch.setattr(cli, "verdict_for_taint", spy)
    run(capsys, ["check", "--claim-type", "taint", "--sink-class", "command",
                 "--finding-id", "T-2", "--file", "svc.py", "--line", "3"])
    assert captured["claim"].claim_type == "taint"
    assert captured["claim"].sink_class == "command"
    assert captured["claim"].line == 3


def test_unknown_sink_class_is_not_an_argparse_error(capsys, monkeypatch):
    # An agent may name a class we do not cover. That must escalate, not crash.
    monkeypatch.setattr(cli, "verdict_for_taint",
                        lambda claim: make_verdict(claim.finding_id, "UNCERTAIN",
                                                   "sink class not covered; escalated", "llm"))
    out = run(capsys, ["check", "--claim-type", "taint", "--sink-class", "xxe",
                       "--finding-id", "T-3", "--file", "svc.py", "--line", "1"])
    assert out["verdict"] == "UNCERTAIN"
    assert out["source"] == "llm"


def test_taint_without_sink_class_still_returns_a_verdict(capsys, monkeypatch):
    monkeypatch.setattr(cli, "verdict_for_taint",
                        lambda claim: make_verdict(claim.finding_id, "UNCERTAIN",
                                                   "no sink class given; escalated", "llm"))
    out = run(capsys, ["check", "--claim-type", "taint",
                       "--finding-id", "T-4", "--file", "svc.py", "--line", "1"])
    assert out["verdict"] == "UNCERTAIN"


def test_taint_is_an_accepted_claim_type():
    assert "taint" in cli.CLAIM_TYPES


def test_sink_class_is_ignored_for_non_taint_claims(capsys, no_pyright):
    out = run(capsys, ["check", "--claim-type", "definedness", "--sink-class", "sql",
                       "--finding-id", "D", "--file", "s.py", "--line", "1"])
    assert out["verdict"] == "UNCERTAIN"
    assert out["source"] == "llm"


def test_numeric_subcommand_emits_a_verdict(capsys, monkeypatch):
    monkeypatch.setattr(cli, "run_properties",
                        lambda fid, test: make_verdict(fid, "CONFIRMED",
                                                       "property violated:\nFalsifying example: f(x=1.0)",
                                                       "hypothesis"))
    out = run(capsys, ["numeric", "--finding-id", "NUM-1", "--test", "t_NUM-1_props.py"])
    assert out["finding_id"] == "NUM-1"
    assert out["verdict"] == "CONFIRMED"
    assert out["source"] == "hypothesis"


def test_numeric_subcommand_passes_the_test_path_through(capsys, monkeypatch):
    captured = {}

    def spy(fid, test):
        captured["fid"] = fid
        captured["test"] = test
        return make_verdict(fid, "UNCERTAIN", "stub; escalated", "hypothesis")

    monkeypatch.setattr(cli, "run_properties", spy)
    run(capsys, ["numeric", "--finding-id", "NUM-2", "--test", "props.py"])
    assert captured == {"fid": "NUM-2", "test": "props.py"}


def test_numeric_requires_a_test_path():
    with pytest.raises(SystemExit):
        cli.main(["numeric", "--finding-id", "NUM-3"])


def test_numeric_is_not_a_check_claim_type():
    # `check` settles a static claim at a file:line; `numeric` executes a test
    # file. Conflating them would put an unusable --file/--line on the numeric path.
    assert "numeric" not in cli.CLAIM_TYPES
