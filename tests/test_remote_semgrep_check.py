"""Tests for the remote path of cca_checks.semgrep_check."""
from cca_checks import remote
from cca_checks import semgrep_check as sc
from cca_checks.claim import Claim

PAYLOAD = {"version": "1.178.0", "errors": [], "paths": {"scanned": ["target.py"]}, "results": []}


def _remote_only(monkeypatch, answer):
    monkeypatch.setenv(remote.ENV, "cca-analyzer")
    monkeypatch.setattr(sc, "tool_unavailable_reason", lambda name: "blocked here")
    calls = []

    def fake(dest, rules, target, ext):
        calls.append((dest, ext, len(rules) > 0, target))
        return answer
    monkeypatch.setattr(remote, "remote_semgrep", fake)
    return calls


def test_local_first_nothing_leaves_when_local_semgrep_runs(monkeypatch, tmp_path):
    """Review Focus 2. Red proof: run_semgrep skips the tool_unavailable_reason test -> the remote is called."""
    monkeypatch.setenv(remote.ENV, "cca-analyzer")
    monkeypatch.setattr(sc, "tool_unavailable_reason", lambda name: None)
    called = []
    monkeypatch.setattr(remote, "remote_semgrep", lambda *a: called.append(a) or (PAYLOAD, None))
    monkeypatch.setattr(sc, "resolve_tool", lambda name: None)  # local path then returns None
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target))
    assert called == []


def test_remote_results_pass_the_same_validation(monkeypatch, tmp_path):
    """Red proof: _run_remote returns data["results"] without _results_of -> the errors payload is accepted."""
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _remote_only(monkeypatch, (PAYLOAD, None))
    assert sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target)) == []
    _remote_only(monkeypatch, ({**PAYLOAD, "errors": [{"message": "parse"}]}, None))
    assert sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target)) is None


def test_an_upper_case_extension_is_lowered(monkeypatch, tmp_path):
    """Review Focus 3. Red proof: _run_remote keeps the extension's case -> ext 'PY' sent."""
    target = tmp_path / "A.PY"
    target.write_bytes(b"x = 1\n")  # bytes: write_text would add \r on Windows
    calls = _remote_only(monkeypatch, (PAYLOAD, None))
    sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target))
    assert calls[0][1] == "py" and calls[0][2] is True and calls[0][3] == b"x = 1\n"


def _claim(tmp_path):
    f = tmp_path / "svc.py"
    f.write_text("def handler(q):\n    cur.execute(q)\n    return 1\n", encoding="utf-8")
    return Claim(finding_id="SEC-001", file=str(f), line=2, claim_type="taint",
                 sink_class="sql")


def test_a_remote_verdict_names_its_host(monkeypatch, tmp_path):
    """Red proof: verdict_for_taint drops the remote note -> evidence lacks the host."""
    hit = {"check_id": "rules.sink-strict-sql", "start": {"line": 2}, "extra": {"message": "m"}}
    _remote_only(monkeypatch, ({**PAYLOAD, "results": [hit]}, None))
    monkeypatch.setattr(sc, "enclosing_span", lambda p, line: (1, 3))
    v = sc.verdict_for_taint(_claim(tmp_path))
    assert v.verdict == "UNCERTAIN" and v.source == "semgrep"
    assert v.evidence.endswith("(semgrep 1.178.0 on cca-analyzer)")


def test_a_remote_failure_is_uncertain_with_its_reason(monkeypatch, tmp_path):
    """Red proof: the sinks-is-None branch ignores _last_remote -> the generic message, no reason."""
    _remote_only(monkeypatch, (None, "host key verification failed"))
    v = sc.verdict_for_taint(_claim(tmp_path))
    assert v.verdict == "UNCERTAIN" and v.source == "llm"
    assert "cca-analyzer" in v.evidence and "host key verification failed" in v.evidence


def test_a_remote_result_must_have_scanned_the_file_sent(monkeypatch, tmp_path):
    """Review I5. Red proof: _run_remote skips the scanned-path check -> the foreign result is accepted.

    A refutation rests on the remote's silence, so the answer must at least be about the
    one file sent, under its fixed name.
    """
    target = tmp_path / "a.py"
    target.write_text("x = 1\n", encoding="utf-8")
    _remote_only(monkeypatch, ({**PAYLOAD, "paths": {"scanned": ["other.py"]}}, None))
    assert sc.run_semgrep(sc.rules_path("python_sinks.yaml"), str(target)) is None
    assert "target.py" in sc._last_remote["error"]
