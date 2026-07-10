import json
import subprocess

import pytest

from cca_checks.claim import Claim
from cca_checks import semgrep_check as sc
from cca_checks.semgrep_check import (
    SUPPORTED_SINK_CLASSES,
    hits_in_span,
    rule_name,
    rules_path,
    run_semgrep,
    verdict_for_taint,
)


def hit(rule_id, line, namespaced=True):
    """A semgrep result. start.line is 1-indexed, and check_id is namespaced by the
    config file's path -- both verified against semgrep 1.168.0."""
    check_id = f"cca_checks.rules.{rule_id}" if namespaced else rule_id
    return {"check_id": check_id, "start": {"line": line}, "end": {"line": line},
            "extra": {"message": f"{rule_id} matched", "severity": "INFO"}}


def proc(stdout, returncode=0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def ok_payload(results):
    return json.dumps({"results": results, "errors": [], "paths": {"scanned": ["f.py"]}})


def claim(sink_class="sql", line=5, file="svc.py"):
    return Claim("F-1", file, line, "taint", sink_class=sink_class)


# --- rule_name / rules_path -------------------------------------------------

def test_rule_name_strips_the_config_path_namespace():
    assert rule_name("cca_checks.rules.sink-strict-sql") == "sink-strict-sql"
    assert rule_name("sink-strict-sql") == "sink-strict-sql"
    assert rule_name("") == ""


def test_rules_path_resolves_bundled_files():
    for name in ("python_sinks.yaml", "python_taint.yaml"):
        p = rules_path(name)
        assert p.endswith(name)
        with open(p, encoding="utf-8") as fh:
            assert "rules:" in fh.read()


# --- run_semgrep fail-safe cascade ------------------------------------------

def test_run_semgrep_returns_results_on_a_genuine_run(monkeypatch):
    monkeypatch.setattr(sc.subprocess, "run",
                        lambda *a, **k: proc(ok_payload([hit("sink-strict-sql", 5)])))
    out = run_semgrep("cfg.yaml", "f.py")
    assert isinstance(out, list) and len(out) == 1


def test_run_semgrep_returns_empty_list_when_genuinely_clean(monkeypatch):
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: proc(ok_payload([])))
    out = run_semgrep("cfg.yaml", "f.py")
    assert out == []
    assert out is not None  # [] means "ran clean", never "could not tell"


def test_run_semgrep_returns_none_when_binary_missing(monkeypatch):
    def boom(*a, **k):
        raise FileNotFoundError()
    monkeypatch.setattr(sc.subprocess, "run", boom)
    assert run_semgrep("cfg.yaml", "f.py") is None


def test_run_semgrep_returns_none_on_timeout(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(cmd="semgrep", timeout=120)
    monkeypatch.setattr(sc.subprocess, "run", boom)
    assert run_semgrep("cfg.yaml", "f.py") is None


def test_run_semgrep_returns_none_on_oserror(monkeypatch):
    def boom(*a, **k):
        raise OSError("nope")
    monkeypatch.setattr(sc.subprocess, "run", boom)
    assert run_semgrep("cfg.yaml", "f.py") is None


def test_run_semgrep_returns_none_on_empty_stdout(monkeypatch):
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: proc(""))
    assert run_semgrep("cfg.yaml", "f.py") is None


def test_run_semgrep_returns_none_on_unparseable_stdout(monkeypatch):
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: proc("not json"))
    assert run_semgrep("cfg.yaml", "f.py") is None


def test_run_semgrep_returns_none_when_semgrep_reports_errors(monkeypatch):
    payload = json.dumps({"results": [], "errors": [{"message": "parse error"}],
                          "paths": {"scanned": ["f.py"]}})
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: proc(payload))
    assert run_semgrep("cfg.yaml", "f.py") is None


def test_run_semgrep_returns_none_when_nothing_was_scanned(monkeypatch):
    payload = json.dumps({"results": [], "errors": [], "paths": {"scanned": []}})
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: proc(payload))
    assert run_semgrep("cfg.yaml", "f.py") is None


def test_run_semgrep_returns_none_when_paths_is_malformed(monkeypatch):
    payload = json.dumps({"results": [], "errors": [], "paths": "nonsense"})
    monkeypatch.setattr(sc.subprocess, "run", lambda *a, **k: proc(payload))
    assert run_semgrep("cfg.yaml", "f.py") is None


def test_run_semgrep_passes_offline_flags_and_a_timeout(monkeypatch):
    seen = {}

    def spy(cmd, **k):
        seen["cmd"] = cmd
        seen["timeout"] = k.get("timeout")
        return proc(ok_payload([]))

    monkeypatch.setattr(sc.subprocess, "run", spy)
    run_semgrep("cfg.yaml", "f.py")
    assert "--metrics=off" in seen["cmd"]
    assert "--disable-version-check" in seen["cmd"]
    assert "--json" in seen["cmd"]
    assert seen["timeout"] == 120


# --- hits_in_span -----------------------------------------------------------

def test_hits_in_span_matches_namespaced_check_ids():
    results = [hit("sink-strict-sql", 5)]
    assert len(hits_in_span(results, 4, 7, "sink-strict-sql")) == 1


def test_hits_in_span_also_matches_a_bare_check_id():
    results = [hit("sink-strict-sql", 5, namespaced=False)]
    assert len(hits_in_span(results, 4, 7, "sink-strict-sql")) == 1


def test_hits_in_span_filters_by_rule_and_span():
    results = [hit("sink-strict-sql", 5), hit("sink-strict-sql", 99), hit("sink-loose-sql", 6)]
    assert len(hits_in_span(results, 4, 7, "sink-strict-sql")) == 1
    assert len(hits_in_span(results, 4, 7, "sink-loose-sql")) == 1
    assert hits_in_span(results, 4, 7, "sink-strict-command") == []


def test_hits_in_span_is_inclusive_at_both_ends():
    results = [hit("sink-strict-sql", 4), hit("sink-strict-sql", 7)]
    assert len(hits_in_span(results, 4, 7, "sink-strict-sql")) == 2


def test_hits_in_span_survives_a_malformed_result():
    results = [{"check_id": "cca_checks.rules.sink-strict-sql"},
               {"check_id": "cca_checks.rules.sink-strict-sql", "start": None},
               "not a dict"]
    assert hits_in_span(results, 1, 10, "sink-strict-sql") == []


# --- verdict_for_taint ------------------------------------------------------

def span_1_to_10(monkeypatch):
    monkeypatch.setattr(sc, "enclosing_span", lambda p, l: (1, 10))


def test_no_sink_of_either_tier_refutes(monkeypatch):
    span_1_to_10(monkeypatch)
    v = verdict_for_taint(claim(), sinks=[], taint=[])
    assert v.verdict == "FALSE_POSITIVE"
    assert v.source == "semgrep"
    assert "no sql sink in the enclosing scope" in v.evidence
    assert "premise does not hold" in v.evidence


def test_strict_sink_escalates_and_never_confirms(monkeypatch):
    span_1_to_10(monkeypatch)
    v = verdict_for_taint(claim(), sinks=[hit("sink-strict-sql", 6)], taint=[])
    assert v.verdict == "UNCERTAIN"
    assert v.source == "semgrep"
    assert "sink-strict-sql" in v.evidence


def test_strict_sink_with_taint_match_reports_the_rule_and_warns_it_is_not_proof(monkeypatch):
    span_1_to_10(monkeypatch)
    v = verdict_for_taint(claim(), sinks=[hit("sink-strict-sql", 6)], taint=[hit("taint-sql", 6)])
    assert v.verdict == "UNCERTAIN"
    assert "taint-sql" in v.evidence
    assert "not proof" in v.evidence.lower()


def test_loose_only_match_escalates_never_refutes(monkeypatch):
    span_1_to_10(monkeypatch)
    v = verdict_for_taint(claim(), sinks=[hit("sink-loose-sql", 6)], taint=[])
    assert v.verdict == "UNCERTAIN"
    assert v.source == "semgrep"
    assert "unrecognized sink" in v.evidence


def test_sink_outside_the_span_does_not_prevent_a_refutation(monkeypatch):
    span_1_to_10(monkeypatch)
    v = verdict_for_taint(claim(), sinks=[hit("sink-strict-sql", 50)], taint=[])
    assert v.verdict == "FALSE_POSITIVE"


def test_loose_hit_outside_the_span_does_not_force_escalation(monkeypatch):
    span_1_to_10(monkeypatch)
    v = verdict_for_taint(claim(), sinks=[hit("sink-loose-sql", 50)], taint=[])
    assert v.verdict == "FALSE_POSITIVE"


def test_a_sink_of_another_class_does_not_prevent_a_refutation(monkeypatch):
    span_1_to_10(monkeypatch)
    v = verdict_for_taint(claim(sink_class="sql"), sinks=[hit("sink-strict-command", 6)], taint=[])
    assert v.verdict == "FALSE_POSITIVE"


def test_semgrep_unavailable_escalates_to_llm(monkeypatch):
    span_1_to_10(monkeypatch)
    monkeypatch.setattr(sc, "run_semgrep", lambda cfg, path: None)
    v = verdict_for_taint(claim())
    assert v.verdict == "UNCERTAIN"
    assert v.source == "llm"
    assert "unavailable" in v.evidence


def test_unknown_sink_class_escalates_to_llm():
    v = verdict_for_taint(claim(sink_class="xxe"), sinks=[], taint=[])
    assert v.verdict == "UNCERTAIN"
    assert v.source == "llm"
    assert "not covered" in v.evidence


def test_missing_sink_class_escalates_to_llm():
    v = verdict_for_taint(claim(sink_class=""), sinks=[], taint=[])
    assert v.verdict == "UNCERTAIN"
    assert v.source == "llm"


def test_non_python_file_escalates_to_llm():
    v = verdict_for_taint(claim(file="handler.ts"), sinks=[], taint=[])
    assert v.verdict == "UNCERTAIN"
    assert v.source == "llm"
    assert "not covered" in v.evidence


def test_unparseable_source_file_escalates(monkeypatch):
    def boom(p, l):
        raise SyntaxError("bad")
    monkeypatch.setattr(sc, "enclosing_span", boom)
    v = verdict_for_taint(claim(), sinks=[], taint=[])
    assert v.verdict == "UNCERTAIN"
    assert v.verdict != "FALSE_POSITIVE"


@pytest.mark.parametrize("sinks,taint", [
    ([], []),
    ([hit("sink-strict-sql", 6)], []),
    ([hit("sink-strict-sql", 6)], [hit("taint-sql", 6)]),
    ([hit("sink-loose-sql", 6)], []),
])
def test_verdict_for_taint_never_confirms(monkeypatch, sinks, taint):
    span_1_to_10(monkeypatch)
    assert verdict_for_taint(claim(), sinks=sinks, taint=taint).verdict != "CONFIRMED"


def test_supported_sink_classes_matches_the_catalog():
    assert SUPPORTED_SINK_CLASSES == frozenset({"sql", "command", "code_exec", "path"})
