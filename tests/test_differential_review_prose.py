"""The differential-review agent must use difftastic as evidence, and say when it could not.

Invariant: L5.5 checks a hunk claimed to be formatting only with `difft --check-only --exit-code`
on that hunk alone, takes the pre-fix side from the Step 4 snapshot, reads exit 1 on a comment or
docstring as expected rather than as a regression, and reports in its status block whether difft
ran, so a missing binary is visible instead of silently changing how verdicts were reached.

Failure mode caught: the procedure is removed or weakened (whole-file comparison, `git show HEAD`
as the before side, no fallback report, or difft promoted to a merge gate).

Red proof (run 2026-11-03): cca_checks/plugin/agents/cca-differential-review.md was replaced by its
origin/master copy (62192b4) and `python -m pytest tests/test_differential_review_prose.py -q
-p no:cacheprovider` gave 4 failed, each on its `assert` (AssertionError, no collection error).
Restored, the same command gave 4 passed. The production target is the "Syntactic check with
difftastic" section and the `difftastic:` line of the status block in that agent file.
"""

from __future__ import annotations

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
AGENT = (ROOT / "cca_checks/plugin/agents/cca-differential-review.md").read_text(encoding="utf-8")


def test_formatting_claims_are_checked_per_hunk_against_the_step_4_snapshot():
    assert "difft --check-only --exit-code" in AGENT
    assert ".claude/audits/.prefix_snapshot/prefix/" in AGENT
    # One region applied alone: a whole-file comparison exits 1 when any other change is real,
    # and default context merges nearby edits into one hunk (measured on an 8-line file).
    assert "git diff --no-index -U0" in AGENT
    assert "git apply --unidiff-zero -p2" in AGENT


def test_the_status_block_reports_whether_difft_ran():
    assert "difftastic: used | unavailable" in AGENT
    assert "`difft` is not on PATH" in AGENT


def test_exit_1_on_comments_and_docstrings_is_not_read_as_a_regression():
    assert "comment or docstring" in AGENT
    assert "`Text`" in AGENT


def test_difft_is_evidence_for_the_verdict_never_a_gate():
    assert "never a merge gate" in AGENT
