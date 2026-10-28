"""The L1 auditor files ask for one output, a scope tied to the run's mode, and evidence with a trigger.

Invariants, each one a defect the 2026-10-28 instructions had (pre-registration
docs/preregistrations/2026-10-28-l1-auditor-instructions.md):

- Every L1 auditor file opened its reply with a YAML status block while the same file and the
  command asked for the JSON array FIRST, and each wrote a trail file and EXECUTION_LOG.md from
  parallel agents.
- The agent checklists were repository-wide (file length, TODO counts, `.env` completeness,
  outdated packages) while the command said changed code only; 26 of 57 L1 false positives
  (0.456) were `out_of_scope`.
- `evidence` was free text: no trigger, no search for a mitigation.
- bug-auditor listed grep patterns only, none of the logic and contract classes the ledger holds;
  perf-auditor listed speculative items and asked no reason a path is hot.

Red proof (run 2026-10-28): this file was run against a `git archive dabc2a6` copy, the master
before the edit, with `python -m pytest tests/test_l1_auditor_prose.py -q -p no:cacheprovider`.
38 failed, 38 of 38, each on an `assert` (AssertionError, no collection or fixture error). The
production targets are the nine L1 files under cca_checks/plugin/agents/ and the Step 1 prompt
template of cca_checks/plugin/commands/audit-fix.md.

On that copy 26 tests failed inside `section()`, because the section did not exist yet. That is
the defect for the scope and evidence tests, but not for three tests that failed only because the
old heading read "Return value (authoritative)". Those, and three more, were also proved red by
mutating the new text (run 2026-10-28, one sentence removed, one test run, file restored; each
failed on its assertion, and all 38 passed after the restore):

- test_quoting_the_line_never_quotes_a_secret_or_config_value[cca-security-auditor] and
  [cca-env-validator]: the `<redacted>` sentence removed from each file's Return value.
- test_numeric_properties_still_travel_in_the_reply: " or in the prose right after the array"
  removed from cca-numeric-auditor.md.
- test_repository_wide_checks_are_hunt_only[cca-code-auditor]: " are HUNT-only" removed.
- test_bug_auditor_starts_from_logic_and_contracts: "- **Reachability.**" removed.
- test_perf_auditor_names_why_a_path_is_hot: ", missing `__slots__`" put back.
"""

from __future__ import annotations

import pathlib
import re

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENTS = ROOT / "cca_checks" / "plugin" / "agents"
L1 = ("cca-bug-auditor", "cca-code-auditor", "cca-security-auditor", "cca-perf-auditor",
      "cca-doc-auditor", "cca-env-validator", "cca-numeric-auditor", "cca-dep-auditor",
      "cca-deploy-auditor")
TEMPLATE = (ROOT / "cca_checks" / "plugin" / "commands" / "audit-fix.md").read_text(encoding="utf-8")


def text(name: str) -> str:
    return (AGENTS / f"{name}.md").read_text(encoding="utf-8")


def section(body: str, title: str) -> str:
    """The `## title` section of an agent file, up to the next `## ` heading."""
    match = re.search(rf"(?ms)^## {re.escape(title)}\n(.*?)(?=^## |\Z)", body)
    assert match, f"no `## {title}` section"
    return match.group(1)


@pytest.mark.parametrize("name", L1)
def test_reply_opens_with_the_json_and_nothing_is_written(name):
    """A single output contract: the JSON array first, no status block, no files."""
    body = text(name)
    assert "MUST start with" not in body and "## Status Block" not in body
    assert "EXECUTION_LOG" not in body and not re.search(r"AUDIT_[A-Z]+\.md", body)
    contract = section(body, "Return value")
    assert "Open the reply with the JSON array" in contract
    assert "Nothing comes before it" in contract and "Write no files" in contract
    assert "every check you skipped and why" in contract


@pytest.mark.parametrize("name", L1)
def test_scope_follows_the_mode(name):
    """DIFF reports what the change introduces or alters; HUNT reports every line."""
    scope = section(text(name), "In scope")
    assert "- **DIFF mode.** Report a defect only when the change introduces it" in scope
    assert "out of scope, even when it is real" in scope
    assert "- **HUNT mode.** Every line of the named files is in scope" in scope


@pytest.mark.parametrize("name", ("cca-code-auditor", "cca-doc-auditor", "cca-env-validator",
                                  "cca-dep-auditor", "cca-security-auditor"))
def test_repository_wide_checks_are_hunt_only(name):
    """The checklists that measure a whole file or repository do not run on a diff."""
    assert "HUNT-only" in section(text(name), "In scope")


@pytest.mark.parametrize("name", L1)
def test_every_finding_names_a_trigger_and_a_mitigation_search(name):
    """Evidence quotes the line, names the trigger, and says where a mitigation was sought."""
    evidence = section(text(name), "Evidence (every finding)")
    assert "**Quote the line.**" in evidence
    assert "**Name the trigger.**" in evidence
    assert "**Look for the mitigation.**" in evidence
    assert "`confidence` below 0.5 and `Trigger: unknown, because <reason>`" in evidence
    assert "Never invent one" in evidence


def test_bug_auditor_starts_from_logic_and_contracts():
    """The logic and contract checks come before the language grep patterns."""
    checks = section(text("cca-bug-auditor"), "Checks")
    logic = checks.find("**Logic and contracts.")
    assert 0 <= logic < checks.find("Construct grep patterns")
    for item in ("**Callers.**", "**Fail open or fail closed.**", "**Shape of outside input.**",
                 "**Identity and keys.**", "**Reachability.**", "**Cleanup on every exit.**"):
        assert item in checks


def test_perf_auditor_names_why_a_path_is_hot():
    """A perf finding names its frequency, and the speculative items are gone."""
    body = text("cca-perf-auditor")
    assert "**Every finding names why the path is hot**" in section(body, "Checks")
    for gone in ("__slots__", "React.memo", "HTTP caching headers", "Missing memoization",
                 "Missing compression", "DNS resolution on every request"):
        assert gone not in body


def test_numeric_properties_still_travel_in_the_reply():
    """Removing the trail must not lose the properties L2.5 executes."""
    contract = section(text("cca-numeric-auditor"), "Return value")
    assert "`properties` key on the finding object or in the prose right after the array" in contract
    assert "## Properties (every finding)" in text("cca-numeric-auditor")


@pytest.mark.parametrize("name", ("cca-security-auditor", "cca-env-validator"))
def test_quoting_the_line_never_quotes_a_secret_or_config_value(name):
    """The evidence bar asks for the line verbatim; these two auditors redact its value."""
    contract = section(text(name), "Return value")
    assert "replaced by `<redacted>`" in contract


def test_template_states_the_mode_and_the_same_scope():
    """The command's prompt template agrees with the agent files on mode and scope."""
    assert "Mode: DIFF. Check the CHANGED code" in TEMPLATE
    assert "Mode: HUNT. Read each file IN FULL." in TEMPLATE
    assert "Focus ONLY on new/changed code" not in TEMPLATE
    assert "including unchanged code\n  whose behaviour the change alters" in TEMPLATE
