"""Tests for cca_checks.bugs.caused_by: regression edges from `git blame`, never from a model.

A fix that modifies or removes lines an earlier CCA fix introduced is caused by that fix. The
repositories are built in the test; nothing touches the network.
"""
from __future__ import annotations

import json

from bugs_testkit import commit, make_repo, run_git

from cca_checks.bugs import pending, run_facts
from cca_checks.bugs.anchors import symbol_sha
from cca_checks.bugs.caused_by import (
    backfill_caused_by,
    caused_by_for_fix,
    changed_old_lines,
    introducing_commits,
)
from cca_checks.bugs.model import EVIDENCED, BugRow

BASE_TEXT = "def lease():\n    conn = take()\n    ok = conn.closed\n    return conn\n"
FIX_A_TEXT = "def lease():\n    conn = take()\n    ok = check(conn)\n    return conn\n"
FIX_B_TEXT = "def lease():\n    conn = take()\n    ok = check(conn, inside=True)\n    return conn\n"


def _row(repo: str, sha: str, finding: str = "BUG-1") -> BugRow:
    return BugRow(project=repo.split("/")[1], repo=repo, finding_id=finding, fix_commit=sha,
                  date="2026-09-01", files=["pool.py"], summary="s",
                  evidence={"confirmed_l25": EVIDENCED, "fixed": EVIDENCED,
                            "l6_approved": EVIDENCED, "red_proof": EVIDENCED})


def _history(tmp_path):
    """seed (non-CCA) writes pool.py; fix A changes line 3; fix B changes line 3 again."""
    repo = make_repo(tmp_path / "w")
    commit(repo, "pool.py", BASE_TEXT, "add the pool")
    fix_a = commit(repo, "pool.py", FIX_A_TEXT, "fix(audit): BUG-1 check liveness")
    base = run_git(repo, "rev-parse", "HEAD").strip()
    fix_b = commit(repo, "pool.py", FIX_B_TEXT, "fix(audit): BUG-2 check inside the lease")
    diff = run_git(repo, "diff", base, fix_b)
    return repo, fix_a, base, fix_b, diff


def test_fix_of_a_line_an_earlier_fix_introduced_is_caused_by_it(tmp_path):
    """B modifies the line A introduced, so B is caused by A.

    Red 2026-10-21 (the plan's mutation): `caused_by_for_fix` blaming `"HEAD"` (the fix
    commit) instead of `base_commit`; failed at `assert caused_by_for_fix(...) == [a.bug_id]`
    with `[]`, since every changed line then blames to B itself.
    """
    repo, fix_a, base, _fix_b, diff = _history(tmp_path)
    a = _row("acme/widgets", fix_a)
    assert caused_by_for_fix(repo, base, diff, [a]) == [a.bug_id]


def test_a_line_a_non_cca_commit_introduced_yields_nothing(tmp_path):
    """Changing a line that came from an ordinary commit names no fix.

    Red 2026-10-21: mutation in `bugs_for_commits`, the `fix_commit in commits` filter
    replaced by `True`; failed at `assert ... == []` (A's id returned).
    """
    repo = make_repo(tmp_path / "w")
    commit(repo, "pool.py", BASE_TEXT, "add the pool")
    fix_a = commit(repo, "other.py", "x = 1\n", "fix(audit): BUG-1 unrelated")
    base = run_git(repo, "rev-parse", "HEAD").strip()
    fix_b = commit(repo, "pool.py", FIX_A_TEXT, "fix(audit): BUG-2")
    diff = run_git(repo, "diff", base, fix_b)
    assert caused_by_for_fix(repo, base, diff, [_row("acme/widgets", fix_a)]) == []


def test_another_repos_row_with_the_same_sha_does_not_match(tmp_path):
    """Only rows of the repository at `repo_root` (its origin) can be the cause.

    Red 2026-10-21: mutation in `bugs_for_commits`, the repo comparison removed; failed at
    `assert ... == [mine.bug_id]` with the other repository's row included.
    """
    repo, fix_a, base, _fix_b, diff = _history(tmp_path)
    mine, theirs = _row("acme/widgets", fix_a), _row("other/thing", fix_a, "BUG-9")
    assert caused_by_for_fix(repo, base, diff, [theirs, mine]) == [mine.bug_id]
    assert caused_by_for_fix(repo, base, diff, [theirs]) == []


def test_only_modified_or_removed_lines_are_blamed():
    """Old-side numbers of removed lines; a removed line that looks like a header is still one.

    Red 2026-10-21: mutation in `changed_old_lines`, `--- ` checked before the hunk counters;
    failed at `assert got == {"q.sql": [2, 3], "pool.py": [7]}` with
    `{'q.sql': [2], 'pool.py': [4]}`: the SQL comment was read as a file header, and every
    later number went wrong with it.
    """
    diff = (
        "diff --git a/q.sql b/q.sql\n"
        "--- a/q.sql\n"
        "+++ b/q.sql\n"
        "@@ -1,4 +1,3 @@\n"
        " select 1;\n"
        "-select 2;\n"
        "--- a comment\n"
        "+select 3;\n"
        " select 4;\n"
        "diff --git a/pool.py b/pool.py\n"
        "--- a/pool.py\n"
        "+++ b/pool.py\n"
        "@@ -7 +7,2 @@\n"
        "-old\n"
        "+new\n"
        "+added\n"
        "@@ -20,0 +22 @@\n"
        "+pure insertion\n"
        "diff --git a/new.py b/new.py\n"
        "--- /dev/null\n"
        "+++ b/new.py\n"
        "@@ -0,0 +1 @@\n"
        "+x\n"
    )
    got = changed_old_lines(diff)
    assert got == {"q.sql": [2, 3], "pool.py": [7]}


def test_introducing_commits_is_deterministic_and_sorted(tmp_path):
    """The same inputs give the same sorted, unique list of full shas."""
    repo, fix_a, base, _fix_b, diff = _history(tmp_path)
    assert introducing_commits(repo, base, diff) == [fix_a]
    assert introducing_commits(repo, base, diff) == introducing_commits(repo, base, diff)


SHIFTED_TEXT = "import os\n\n\n" + FIX_B_TEXT  # fix B, three lines pushed down


def _shifted_history(tmp_path):
    repo = make_repo(tmp_path / "w")
    commit(repo, "pool.py", BASE_TEXT, "add the pool")
    fix_a = commit(repo, "pool.py", FIX_A_TEXT, "fix(audit): BUG-1 check liveness")
    base = run_git(repo, "rev-parse", "HEAD").strip()
    head = commit(repo, "pool.py", SHIFTED_TEXT, "fix(audit): BUG-2 check inside the lease")
    return repo, fix_a, base, head


def test_run_facts_anchor_the_fixed_code_and_name_the_blamed_commits(tmp_path):
    """The symbol is resolved at the base line L2 cited and hashed over the fixed code.

    The fix pushes `lease` down three lines, so base line 3 is module level in the fixed file:
    resolving the symbol there would lose it.

    Red 2026-10-21: mutation in `fixed_anchor`, the span hashed in `base` instead of `head`;
    failed at `assert facts == {...}` (code_sha of the old `lease`).
    Red 2026-10-21: mutation in `fixed_anchor`, the symbol resolved in the fixed text; failed
    at `assert facts == {...}` (symbol None, a file-level anchor).
    """
    repo, fix_a, base, head = _shifted_history(tmp_path)
    l2 = [{"id": "BUG-2", "file": "pool.py", "line": 3}, {"id": "BUG-9", "file": "gone.py"}]
    l4 = [{"fix_id": "FIX-1", "finding": "BUG-2", "files": ["pool.py"]},
          {"fix_id": "FIX-2", "finding": "BUG-9", "files": []}]
    out = run_facts.compute(repo, base, head, l2, l4)
    facts = out["findings"]["BUG-2"]
    assert facts == {"anchors": [{"file": "pool.py", "symbol": "lease",
                                  "code_sha": symbol_sha(SHIFTED_TEXT, "lease")}],
                     "errors": [], "introduced_by": [fix_a]}
    assert out["findings"]["BUG-9"] == {"anchors": [], "errors": ["no anchor for gone.py"],
                                        "introduced_by": []}


def test_capture_writes_run_facts_and_never_fails_on_them(tmp_path, _private_bugs_dir):
    """`pipeline close` (pending.capture) carries the facts; a broken L2 costs only the facts.

    Red 2026-10-21: mutation deleting the `_write_facts(...)` call in `capture`; failed at
    `facts = json.loads((dest / "run_facts.json").read_text(...))` (FileNotFoundError: the
    record carries no facts), which is the defect: resolve would build rows without them.
    Red 2026-10-21: mutation replacing `except Exception` in `_write_facts` with
    `except GitError`; capture raised JSONDecodeError for the broken L2 instead of returning.
    """
    repo, fix_a, base, head = _shifted_history(tmp_path)
    run_dir = repo / ".claude" / "audits" / "runs" / "run-9"
    run_dir.mkdir(parents=True)
    (run_dir / "summary.json").write_text(json.dumps({"commit": head, "base_commit": base}))
    (run_dir / "L2.json").write_text(json.dumps([{"id": "BUG-2", "file": "pool.py", "line": 3}]))
    (run_dir / "L4.json").write_text(json.dumps([{"fix_id": "FIX-1", "finding": "BUG-2",
                                                  "files": ["pool.py"]}]))
    dest = pending.capture(repo, "run-9")
    facts = json.loads((dest / "run_facts.json").read_text(encoding="utf-8"))
    assert facts["base"] == base and facts["head"] == head
    assert facts["findings"]["BUG-2"]["introduced_by"] == [fix_a]

    (run_dir / "L2.json").write_text("{not json")
    dest = pending.capture(repo, "run-9")
    facts = json.loads((dest / "run_facts.json").read_text(encoding="utf-8"))
    assert facts["error"].startswith("JSONDecodeError")
    assert (dest / "L4.json").exists()


def test_forward_rows_carry_anchors_and_caused_by_from_run_facts(tmp_path):
    """resolve's rows get the record's anchors, and caused_by from the ledger's same-repo rows.

    Malformed anchors and shas from the (untrusted) record are dropped, never passed on.

    Red 2026-10-21: mutation `anchors=[]` in `rows_and_skips`'s BugRow; failed at
    `assert bug.anchors == [Anchor("a.py", "f", "1" * 64)]`.
    Red 2026-10-21: mutation `caused_by=[]` in `rows_and_skips`'s BugRow; failed at
    `assert bug.caused_by == [earlier.bug_id]`.
    """
    from test_bugs_forward import MERGE, make_record

    from cca_checks.bugs.anchors import Anchor
    from cca_checks.bugs.forward import rows_for

    rec = make_record(tmp_path)
    earlier = _row("acme/widgets", "e" * 40, "BUG-0")
    elsewhere = _row("other/thing", "e" * 40, "BUG-5")
    (rec / "run_facts.json").write_text(json.dumps({"schema": 1, "findings": {"BUG-001": {
        "anchors": [{"file": "a.py", "symbol": "f", "code_sha": "1" * 64},
                    {"file": "a.py", "symbol": "g", "code_sha": "not-a-sha"},
                    {"file": "a\\b.py", "symbol": None, "code_sha": "2" * 64}],
        "introduced_by": ["e" * 40, "zz"], "errors": []}}}))
    bug, doc = rows_for(rec, MERGE, [elsewhere, earlier])
    assert bug.anchors == [Anchor("a.py", "f", "1" * 64)]
    assert bug.caused_by == [earlier.bug_id]
    assert doc.anchors == [] and doc.caused_by == []
    assert rows_for(rec, MERGE)[0].caused_by == []


def test_backfill_fills_only_empty_caused_by_and_keeps_other_lines(tmp_path):
    """The back-fill sets caused_by on rows that have none, and leaves every other line's bytes.

    Dry run by default: without `write` the ledger is not touched.

    Red 2026-10-21: mutation in `backfill_caused_by`, every line re-serialised rather than
    only the changed one; failed at `assert lines[0] == original[0]` (the legacy line gained
    keys).
    """
    repo, fix_a, base, fix_b, _diff = _history(tmp_path)
    a, b = _row("acme/widgets", fix_a), _row("acme/widgets", fix_b, "BUG-2")
    legacy = a.to_json()
    for key in ("anchors", "caused_by", "kind"):
        legacy.pop(key)
    root = tmp_path / "bugs"
    root.mkdir()
    ledger = root / "bugs.jsonl"
    original = [json.dumps(legacy, sort_keys=True), json.dumps(b.to_json(), sort_keys=True)]
    ledger.write_text("".join(f"{x}\n" for x in original), encoding="utf-8", newline="\n")

    dry = backfill_caused_by(root, repo, write=False)
    assert dry.changed == {b.bug_id: [a.bug_id]}
    assert ledger.read_text(encoding="utf-8").splitlines() == original

    done = backfill_caused_by(root, repo, write=True)
    assert done.changed == {b.bug_id: [a.bug_id]}
    lines = ledger.read_text(encoding="utf-8").splitlines()
    assert lines[0] == original[0]
    assert json.loads(lines[1]) == {**b.to_json(), "caused_by": [a.bug_id]}
    assert backfill_caused_by(root, repo, write=True).changed == {}
