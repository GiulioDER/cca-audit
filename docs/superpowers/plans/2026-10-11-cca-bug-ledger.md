# CCA bug ledger (backfill) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `python -m cca_checks bugs backfill`, which rebuilds the historical tier of the cross-project bug ledger from CCA fix commits and the per-project scorecard ledgers, read-only unless `--write`.

**Architecture:** A new subpackage `cca_checks/bugs/` with one module per job: `model` (the row, its tier and its memo), `parse` (commit message to bullets), `gitread` (clones, default branch, fix commits), `ledgers` (scorecard ledgers, proof files, run matching), `backfill` (evidence assembly, dedup, summary), `store` (append rows, write memos) and `__main__` (the CLI). Pure stdlib. The top-level CLI only forwards `bugs ...` to it.

**Tech Stack:** Python 3.10+, stdlib only (`argparse`, `dataclasses`, `json`, `re`, `subprocess`, `pathlib`), `git` on PATH, pytest.

**Spec:** `docs/superpowers/specs/2026-10-11-cca-bug-ledger-design.md`, including its §11 corrections, which win where they disagree with earlier sections.

**Not in this plan:** the forward path (spec §6, waits for GiulioDER/cca-audit#60 to merge, and gets its own plan then) and the `cca` tenant (spec §7, operational, planned in the operator's private configuration because it names a host).

## Global Constraints

- `requires-python = ">=3.10"`; ruff `line-length = 100`, `select = ["E", "F", "W", "I", "B", "UP"]`; no new dependency.
- Default ledger directory `~/.claude/cca-bugs/`, overridden by `CCA_BUGS_DIR`. Nothing the tool writes goes inside a repository.
- Evidence values are exactly `evidenced`, `claimed`, `absent`; conditions are exactly `confirmed_l25`, `fixed`, `l6_approved`, `red_proof`; tier is `proved` if and only if all four are `evidenced`, else `historical`.
- `summary` is the commit's bullet text, never generated.
- No private commit text in this repository: fixtures are written for the tests in the shape of real messages.
- Prose, comments, commit messages: no dash used as punctuation; first person singular where a person speaks.
- Every behaviour test is proved red before it is trusted green: apply the named mutation to the implementation, run the test, see it fail in its assertion (or, where stated, in the call under test, which is the defect itself), revert, run green, and write the receipt into the test's docstring as `Red proof: <mutation> -> <failure>`. An `ImportError` or a missing symbol is never a red proof.
- Run tests as `python -m pytest ...` from the worktree root, so the worktree's `cca_checks` is imported rather than an installed copy.
- Do not edit files GiulioDER/cca-audit#60 changes, except the forwarding lines in `cca_checks/__main__.py` (Task 7). Every file in Tasks 1 to 6 is new.

## Review Focus

1. A heading written in Markdown bold or as a Markdown heading (`**P1:**`, `## P2`): bullets under it must still be read. Pinned in Task 2.
2. A commit message holding bytes that are not UTF-8: the run must not crash; the text carries a replacement character. Pinned in Task 3.
3. A scorecard ledger with a malformed line: the line is skipped and counted, the rest is read. Pinned in Task 4.
4. `CCA_BUGS_DIR` naming a folder that does not exist yet: `--write` creates it. Pinned in Task 6.
5. A clone whose `origin/HEAD` is not set and whose default branch is `main`: it is still read. Pinned in Task 3.

---

### Task 1: The row, its tier, and its memo

**Files:**
- Create: `cca_checks/bugs/__init__.py`
- Create: `cca_checks/bugs/model.py`
- Test: `tests/test_bugs_model.py`

**Interfaces:**
- Produces: `EVIDENCED`, `CLAIMED`, `ABSENT`, `CONDITIONS`, `SHA_LEN = 12`; `BugRow` (dataclass, fields below) with properties `bug_id: str`, `tier: str` and method `to_json() -> dict`; `memo_filename(bug_id: str) -> str`; `render_memo(row: BugRow, modified: str) -> str`.

- [ ] **Step 1: Confirm the worktree's package is the one imported**

Run: `python -c "import cca_checks, os; print(os.path.abspath(cca_checks.__file__))"`
Expected: a path inside this worktree. If it points elsewhere, stop and report.

- [ ] **Step 2: Write the failing tests**

```python
"""Tests for cca_checks.bugs.model: the bug row, its tier, and its memo."""
from cca_checks.bugs.model import (
    ABSENT,
    CLAIMED,
    CONDITIONS,
    EVIDENCED,
    BugRow,
    memo_filename,
    render_memo,
)


def _row(**evidence):
    base = {c: EVIDENCED for c in CONDITIONS}
    base.update(evidence)
    return BugRow(
        project="widgets", repo="acme/widgets", finding_id="STAKES-001",
        fix_commit="0123456789abcdef0123456789abcdef01234567", date="2026-09-01",
        files=["src/store.py"], summary="delete and bump ran as two statements; +test",
        severity="P1", evidence=base, sources=["git:0123456789ab"],
    )


def test_tier_is_proved_only_when_all_four_are_evidenced():
    """Red proof: model.BugRow.tier `all(` -> `any(` -> the claimed row reads 'proved'."""
    assert _row().tier == "proved"
    assert _row(red_proof=CLAIMED).tier == "historical"
    assert _row(l6_approved=ABSENT).tier == "historical"


def test_bug_id_and_memo_filename():
    """Red proof: memo_filename returns bug_id + '.md' -> the ':' assertion fails."""
    row = _row()
    assert row.bug_id == "widgets:0123456789ab:STAKES-001"
    name = memo_filename(row.bug_id)
    assert ":" not in name
    assert name == "widgets__0123456789ab__STAKES-001.md"


def test_to_json_marks_a_missing_condition_absent():
    """Red proof: to_json uses evidence.get(c) without a default -> None, not 'absent'."""
    row = _row()
    del row.evidence["red_proof"]
    out = row.to_json()
    assert out["evidence"]["red_proof"] == ABSENT
    assert out["tier"] == "historical"
    assert out["bug_id"] == row.bug_id


def test_memo_quotes_the_summary_and_carries_front_matter():
    """Red proof: drop the summary line from render_memo -> the 'in text' assertion fails."""
    text = render_memo(_row(), "2026-10-11T00:00:00Z")
    assert text.startswith("---\nname: widgets-0123456789ab-stakes-001\n")
    assert "valid_from: 2026-09-01\n" in text
    assert "  type: project\n" in text
    assert "delete and bump ran as two statements; +test" in text
    assert "| red_proof | evidenced |" in text
    assert "`src/store.py`" in text
```

- [ ] **Step 3: Run them and see them fail**

Run: `python -m pytest tests/test_bugs_model.py -q`
Expected: collection fails with `ModuleNotFoundError: No module named 'cca_checks.bugs'` (not a red proof; Step 6 gives that).

- [ ] **Step 4: Write the implementation**

`cca_checks/bugs/__init__.py`:

```python
"""Cross-project ledger of the bugs CCA fixed.

Spec: docs/superpowers/specs/2026-10-11-cca-bug-ledger-design.md. A bug counts as
`proved` only when four conditions are on record (confirmed at L2.5, fix landed,
approved at L6, red at L5.6); everything else is `historical`, with each condition
marked for what can and cannot be shown.
"""
```

`cca_checks/bugs/model.py`:

```python
"""The ledger row, its tier, and the memo generated from it.

The tier is computed, never stored by hand, so a row cannot claim `proved` while
holding a condition that is only `claimed`. The memo is rendered from the row alone:
it adds no text the row does not hold, which is what keeps every memo traceable to
its sources.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

EVIDENCED = "evidenced"
CLAIMED = "claimed"
ABSENT = "absent"
CONDITIONS = ("confirmed_l25", "fixed", "l6_approved", "red_proof")
SHA_LEN = 12


@dataclass
class BugRow:
    project: str
    repo: str
    finding_id: str
    fix_commit: str
    date: str
    files: list[str]
    summary: str
    severity: str | None = None
    auditor: str | None = None
    category: str | None = None
    run_id: str | None = None
    evidence: dict[str, str] = field(default_factory=dict)
    sources: list[str] = field(default_factory=list)
    supersedes: str | None = None

    @property
    def bug_id(self) -> str:
        return f"{self.project}:{self.fix_commit[:SHA_LEN]}:{self.finding_id}"

    @property
    def tier(self) -> str:
        if all(self.evidence.get(c) == EVIDENCED for c in CONDITIONS):
            return "proved"
        return "historical"

    def to_json(self) -> dict:
        return {
            "bug_id": self.bug_id,
            "tier": self.tier,
            "date": self.date,
            "project": self.project,
            "repo": self.repo,
            "files": list(self.files),
            "finding_id": self.finding_id,
            "category": self.category,
            "severity": self.severity,
            "auditor": self.auditor,
            "fix_commit": self.fix_commit,
            "run_id": self.run_id,
            "evidence": {c: self.evidence.get(c, ABSENT) for c in CONDITIONS},
            "sources": list(self.sources),
            "summary": self.summary,
            "supersedes": self.supersedes,
        }


def memo_filename(bug_id: str) -> str:
    # ':' is not a legal filename character on Windows.
    return bug_id.replace(":", "__") + ".md"


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def render_memo(row: BugRow, modified: str) -> str:
    flat = " ".join(row.summary.split())
    description = f"{row.project} {row.finding_id} ({row.tier}): {flat[:120]}"
    lines = [
        "---",
        f"name: {_slug(row.bug_id)}",
        f"description: {json.dumps(description, ensure_ascii=False)}",
        f"valid_from: {row.date}",
        "metadata:",
        "  node_type: memory",
        "  type: project",
        "  originSessionId: cca-bugs",
        f"  modified: {modified}",
        "---",
        "",
        f"# {row.project} {row.finding_id}: a bug CCA fixed",
        "",
        "## The bug",
        "",
        row.summary,
        "",
        "## The fix",
        "",
        f"Commit `{row.fix_commit}` in `{row.repo}`, {row.date}. Files changed by that commit:",
        "",
        *[f"* `{f}`" for f in row.files],
        "",
        "## The evidence",
        "",
        f"Tier: **{row.tier}**.",
        "",
        "| Condition | Value |",
        "|---|---|",
        *[f"| {c} | {row.evidence.get(c, ABSENT)} |" for c in CONDITIONS],
        "",
        "Sources: " + ", ".join(f"`{s}`" for s in row.sources),
        "",
    ]
    return "\n".join(lines)
```

- [ ] **Step 5: Run them green**

Run: `python -m pytest tests/test_bugs_model.py -q`
Expected: `4 passed`.

- [ ] **Step 6: Prove each test red**

For each test, apply the mutation named in its docstring to `cca_checks/bugs/model.py`, run `python -m pytest tests/test_bugs_model.py::<test> -q`, confirm it fails in its assertion, and revert. Then run the file green again. If a test does not go red under its mutation, the test is wrong: fix the test, not the mutation.

- [ ] **Step 7: Lint and commit**

```bash
ruff check cca_checks tests
git add cca_checks/bugs/__init__.py cca_checks/bugs/model.py tests/test_bugs_model.py
git commit -m "feat(bugs): the ledger row, its computed tier, and its memo" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Parse a fix commit message into bullets

**Files:**
- Create: `cca_checks/bugs/parse.py`
- Test: `tests/test_bugs_parse.py`

**Interfaces:**
- Produces: `is_cca_fix_subject(subject: str) -> bool`; `Bullet` (frozen dataclass: `finding_id: str`, `severity: str`, `summary: str`); `expand_ids(ids: str) -> list[str]`; `parse_message(body: str) -> list[Bullet]`.

- [ ] **Step 1: Write the failing tests**

```python
"""Tests for cca_checks.bugs.parse: fix commit messages to one bullet per finding."""
from cca_checks.bugs.parse import expand_ids, is_cca_fix_subject, parse_message

DEEP = """fix(store): 3 audit fixes from CCA (DEEP tier)

P1:
- STAKES-001: delete ran the row delete and the version bump as two
  autocommit statements, so a failed bump left rows gone. Both now share
  one transaction; +test_delete_is_atomic, red at PR head.
P2:
- SEC-002: prefixes match with fullmatch (+test_prefix_fullmatch).
- CODE-001/002: tests pin the copied helpers.
- PERF-004/005, BUG-012: reads follow the database order.
Owner decision, not fixed here:
- DEPLOY-001: numpy is not a core dependency.
Deferred (P3): zero-norm cosines, file mode.

Audit: DEEP, 6 auditors, 3 fixed.
"""

FIX_IDS = """R2-1 forget: audit fixes for #805 (bounds) (#828)

* R2-1 forget: bound the detector (audit of #805)

Two fixes from a DEEP audit.

P1:
- FIX-1, regex backtracking: _POLITE was exponential. Whitespace is
  collapsed once. +test_detector_is_not_superlinear (9 cases).

P2:

- FIX-2: the ledger keeps 1,000 characters per request.

- DOC-009 deferred: scope wording.

P3:

- DOC-002: scope wording.
"""

SELF = """fix(cca_checks): 2 audit fixes from CCA self-audit (DEEP tier)

P1 Critical:
- SEC-001 pyright was launched by bare name. +test
- SEC-003/STAKES-002 run_pyright inherited the audited config. +test
"""


def _ids(bullets):
    return [(b.finding_id, b.severity) for b in bullets]


def test_subject_selection():
    """Red proof: SUBJECT_RE without the 'for' alternative -> the #805 subject is rejected."""
    assert is_cca_fix_subject("fix(store): 3 audit fixes from CCA (DEEP tier)")
    assert is_cca_fix_subject("R2-1 forget: audit fixes for #805 (bounds) (#828)")
    assert is_cca_fix_subject("fix(capabilities): apply CCA audit fixes")
    assert is_cca_fix_subject("fix(cca_checks): 21 audit fixes from CCA self-audit (DEEP tier)")
    assert not is_cca_fix_subject("feat(dashboard): a local memory dashboard")


def test_one_bullet_per_finding_with_its_continuation_joined():
    """Red proof: parse_message drops continuation lines (append -> pass) -> summary lacks 'one transaction'."""
    bullets = parse_message(DEEP)
    first = bullets[0]
    assert (first.finding_id, first.severity) == ("STAKES-001", "P1")
    assert first.summary.startswith("delete ran the row delete")
    assert "one transaction; +test_delete_is_atomic, red at PR head." in first.summary


def test_sections_that_were_not_fixed_are_skipped():
    """Red proof: the non-heading branch keeps the section (section = None -> pass) -> DEPLOY-001 parsed."""
    assert _ids(parse_message(DEEP)) == [
        ("STAKES-001", "P1"),
        ("SEC-002", "P2"),
        ("CODE-001", "P2"),
        ("CODE-002", "P2"),
        ("PERF-004", "P2"),
        ("PERF-005", "P2"),
        ("BUG-012", "P2"),
    ]


def test_a_bullet_marked_deferred_is_skipped():
    """Red proof: remove 'deferred' from _SKIP_TEXT -> DOC-009 is parsed."""
    assert _ids(parse_message(FIX_IDS)) == [("FIX-1", "P1"), ("FIX-2", "P2")]


def test_comma_after_the_id_is_a_separator():
    """Red proof: _BULLET_RE rest group without ',' -> FIX-1 is not parsed."""
    first = parse_message(FIX_IDS)[0]
    assert first.finding_id == "FIX-1"
    assert first.summary.startswith("regex backtracking: _POLITE was exponential.")


def test_multi_id_bullets():
    """Red proof: expand_ids splits on '/' only -> 'CODE-003, DOC-003' yields nothing."""
    assert expand_ids("CODE-001/002") == ["CODE-001", "CODE-002"]
    assert expand_ids("CODE-003, DOC-003") == ["CODE-003", "DOC-003"]
    assert expand_ids("PERF-004/005, BUG-012") == ["PERF-004", "PERF-005", "BUG-012"]
    assert expand_ids("SEC-003/STAKES-002") == ["SEC-003", "STAKES-002"]
    assert _ids(parse_message(SELF)) == [
        ("SEC-001", "P1"), ("SEC-003", "P1"), ("STAKES-002", "P1"),
    ]


def test_bold_and_markdown_headings_open_a_section():
    """Review Focus 1. Red proof: _heading_severity without .strip('*') -> the bold heading opens nothing."""
    body = "fix: 2 audit fixes from CCA\n\n**P1:**\n- BUG-001: a.\n\n## P2\n- BUG-002: b.\n"
    assert _ids(parse_message(body)) == [("BUG-001", "P1"), ("BUG-002", "P2")]
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_bugs_parse.py -q`
Expected: collection fails, `cannot import name ... from cca_checks.bugs.parse` or module not found.

- [ ] **Step 3: Write the implementation**

`cca_checks/bugs/parse.py`:

```python
"""Read a CCA fix commit message into one bullet per finding.

Deterministic on purpose: a bug description in the ledger is the commit's own text,
never a model's summary of it. A message this parser cannot read yields no bullets,
and the caller reports the commit as unparsed rather than guessing.

Shape read (Step 7 of audit-fix.md, and the variants found in real history):

    P1:                      also "P1 Critical:", "**P1:**", "## P1"
    - STAKES-001: text       also "- SEC-001 text", "- FIX-1, text"
      continuation lines     indented, joined into the summary
    - CODE-001/002: text     one bullet per ID
    Deferred (P3): ...       any other unindented line closes the section
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SUBJECT_RE = re.compile(
    r"audit fixes? (?:from|for) |from CCA|CCA (?:audit|self-audit)", re.IGNORECASE
)
_ID = r"[A-Z][A-Z0-9]*-\d+"
_BULLET_RE = re.compile(
    rf"^[-*]\s+(?P<ids>{_ID}(?:\s*[/,]\s*(?:[A-Z][A-Z0-9]*-)?\d+)*)(?P<rest>(?:[:,\s].*)?)$"
)
_ONE_ID = re.compile(r"([A-Z][A-Z0-9]*)-(\d+)")
_SEVERITY = re.compile(r"(P[123])\b")
_SKIP_TEXT = ("deferred", "(deferred", "not fixed", "owner decision")
_FIXED_SEVERITIES = ("P1", "P2")


@dataclass(frozen=True)
class Bullet:
    finding_id: str
    severity: str
    summary: str


def is_cca_fix_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject))


def expand_ids(ids: str) -> list[str]:
    out: list[str] = []
    prefix: str | None = None
    for part in re.split(r"\s*[/,]\s*", ids.strip()):
        m = _ONE_ID.fullmatch(part)
        if m:
            prefix = m.group(1)
            out.append(part)
        elif part.isdigit() and prefix:
            out.append(f"{prefix}-{part}")
    return out


def _heading_severity(line: str) -> str | None:
    is_markdown = line.lstrip().startswith("#")
    text = line.strip().lstrip("#").strip().strip("*").strip()
    m = _SEVERITY.match(text)
    if not m:
        return None
    if text.endswith(":") or is_markdown or text == m.group(1):
        return m.group(1)
    return None


def parse_message(body: str) -> list[Bullet]:
    bullets: list[Bullet] = []
    section: str | None = None
    current: list | None = None  # [ids, severity, parts]

    def flush() -> None:
        nonlocal current
        if current:
            ids, severity, parts = current
            text = " ".join(" ".join(parts).split())
            if (severity in _FIXED_SEVERITIES and text
                    and not text.lower().startswith(_SKIP_TEXT)):
                bullets.extend(Bullet(i, severity, text) for i in ids)
        current = None

    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if line[0] in " \t":
            if current:
                current[2].append(line.strip())
            continue
        m = _BULLET_RE.match(line)
        if m and section:
            flush()
            current = [expand_ids(m.group("ids")), section, [m.group("rest").lstrip(" :,\t")]]
            continue
        flush()
        severity = _heading_severity(line)
        if severity:
            section = severity
        elif line.startswith(("-", "*")):
            continue  # a bullet naming no finding ID leaves the section as it is
        else:
            section = None
    flush()
    return bullets
```

- [ ] **Step 4: Run them green**

Run: `python -m pytest tests/test_bugs_parse.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Prove each test red**

Apply each docstring's mutation to `cca_checks/bugs/parse.py`, run that test alone, confirm an assertion failure, revert. Run the file green again.

- [ ] **Step 6: Lint and commit**

```bash
ruff check cca_checks tests
git add cca_checks/bugs/parse.py tests/test_bugs_parse.py
git commit -m "feat(bugs): read CCA fix commit messages into one bullet per finding" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: Read fix commits from git

**Files:**
- Create: `cca_checks/bugs/gitread.py`
- Create: `tests/bugs_testkit.py` (helper module, not collected: no `test_` prefix; importable because `tests/` has no `__init__.py` and pytest prepends it to `sys.path`)
- Test: `tests/test_bugs_gitread.py`

**Interfaces:**
- Consumes: `is_cca_fix_subject` (Task 2).
- Produces: `GitError(RuntimeError)`; `git(clone: Path, *args: str) -> str`; `normalize_remote(url: str) -> str | None` (returns `"owner/name"`); `remote_repo(path: Path) -> str | None`; `group_clones(paths: list[Path]) -> tuple[dict[str, Path], list[tuple[str, str]]]` (repo to first clone; skipped `(path, reason)`); `default_ref(clone: Path) -> str | None`; `Commit` (dataclass: `sha`, `date` as `YYYY-MM-DD` committer date, `subject`, `body`, `files: list[str]`); `fix_commits(clone: Path, ref: str) -> list[Commit]`.
- Test helper produces: `run_git(repo, *args) -> str`, `make_repo(path, remote=...) -> Path`, `commit(repo, filename, content, message, date=...) -> str`, `publish(repo, ref="master", branch="master")`.

- [ ] **Step 1: Write the test helper**

`tests/bugs_testkit.py`:

```python
"""Tiny git repositories for the cca_checks.bugs tests.

Not collected (no test_ prefix). Nothing touches the network: `origin` is only a URL,
and the remote-tracking ref that marks a commit as landed is written with update-ref.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path


def run_git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True,
        text=True, encoding="utf-8",
    ).stdout


def make_repo(path: Path, remote: str = "https://github.com/acme/widgets.git") -> Path:
    path.mkdir(parents=True, exist_ok=True)
    run_git(path, "init", "-q", "-b", "master")
    run_git(path, "config", "user.email", "test@example.com")
    run_git(path, "config", "user.name", "Test")
    run_git(path, "config", "commit.gpgsign", "false")
    (path / ".nohooks").mkdir()
    run_git(path, "config", "core.hooksPath", str(path / ".nohooks"))
    run_git(path, "remote", "add", "origin", remote)
    commit(path, "README.md", "seed\n", "initial commit")
    return path


def commit(repo: Path, filename: str, content: str, message: str | bytes,
           date: str = "2026-09-01T12:00:00+00:00") -> str:
    target = repo / filename
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8", newline="\n")
    run_git(repo, "add", filename)
    env = {**os.environ, "GIT_AUTHOR_DATE": date, "GIT_COMMITTER_DATE": date}
    data = message if isinstance(message, bytes) else message.encode("utf-8")
    subprocess.run(["git", "-C", str(repo), "commit", "-q", "-F", "-"], input=data,
                   check=True, capture_output=True, env=env)
    return run_git(repo, "rev-parse", "HEAD").strip()


def publish(repo: Path, ref: str = "master", branch: str = "master") -> None:
    run_git(repo, "update-ref", f"refs/remotes/origin/{branch}", ref)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_bugs_gitread.py`:

```python
"""Tests for cca_checks.bugs.gitread: clones, default branch, landed fix commits."""
from bugs_testkit import commit, make_repo, publish, run_git

from cca_checks.bugs.gitread import (
    default_ref,
    fix_commits,
    group_clones,
    normalize_remote,
)

FIX = "fix(store): 1 audit fix from CCA (STANDARD tier)\n\nP1:\n- BUG-001: a. +test\n"


def test_normalize_remote():
    """Red proof: _GITHUB without the ':' alternative -> the ssh form returns None."""
    assert normalize_remote("git@github.com:acme/widgets.git") == "acme/widgets"
    assert normalize_remote("https://github.com/acme/widgets.git\n") == "acme/widgets"
    assert normalize_remote("https://github.com/acme/widgets") == "acme/widgets"
    assert normalize_remote(r"C:\bundles\widgets.bundle") is None


def test_clones_of_one_remote_count_once(tmp_path):
    """Red proof: group_clones keys on the path instead of the repo -> two entries."""
    a = make_repo(tmp_path / "a", "git@github.com:acme/widgets.git")
    b = make_repo(tmp_path / "b", "https://github.com/ACME/widgets.git")
    c = make_repo(tmp_path / "c", r"C:\bundles\widgets.bundle")
    repos, skipped = group_clones([a, b, c])
    assert repos == {"acme/widgets": a}
    assert skipped == [(str(c), "no GitHub origin remote")]


def test_default_ref_falls_back_to_main(tmp_path):
    """Review Focus 5. Red proof: drop origin/main from default_ref's candidates -> None."""
    repo = make_repo(tmp_path / "r")
    publish(repo, branch="main")
    assert default_ref(repo) == "refs/remotes/origin/main"


def test_only_landed_fix_commits_are_read(tmp_path):
    """Red proof: fix_commits logs '--all' instead of ref -> the unmerged fix is read."""
    repo = make_repo(tmp_path / "r")
    landed = commit(repo, "src/a.py", "a = 1\n", FIX, date="2026-09-02T10:00:00+00:00")
    commit(repo, "src/b.py", "b = 1\n", "feat: unrelated work\n")
    publish(repo)
    run_git(repo, "checkout", "-q", "-b", "feature")
    commit(repo, "src/c.py", "c = 1\n", FIX.replace("BUG-001", "BUG-002"))
    commits = fix_commits(repo, default_ref(repo))
    assert [c.sha for c in commits] == [landed]
    assert commits[0].date == "2026-09-02"
    assert commits[0].files == ["src/a.py"]
    assert "BUG-001" in commits[0].body


def test_a_message_that_is_not_utf8_does_not_crash(tmp_path):
    """Review Focus 2. Red proof: git() without errors='replace' -> UnicodeDecodeError in the call."""
    repo = make_repo(tmp_path / "r")
    message = FIX.encode("utf-8").replace(b"a. +test", b"caf\xe9 +test")
    commit(repo, "src/a.py", "a = 1\n", message)
    publish(repo)
    [only] = fix_commits(repo, default_ref(repo))
    assert "\ufffd" in only.body
```

- [ ] **Step 3: Run them and see them fail**

Run: `python -m pytest tests/test_bugs_gitread.py -q`
Expected: collection fails, module `cca_checks.bugs.gitread` not found.

- [ ] **Step 4: Write the implementation**

`cca_checks/bugs/gitread.py`:

```python
"""Find the landed CCA fix commits in a set of clones.

"Fixed" means landed: only commits reachable from the remote's default branch are
read. Clones are grouped by their GitHub origin, so thirty checkouts of one repository
count its bugs once; a clone without a GitHub origin (a local bundle, say) is skipped
and reported, because it would count the same bugs under a second name.
"""
from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .parse import is_cca_fix_subject

_GITHUB = re.compile(r"github\.com[:/](?P<owner>[^/\s]+)/(?P<name>[^/\s]+?)(?:\.git)?/?$")
_DEFAULT_REFS = (
    "refs/remotes/origin/HEAD",
    "refs/remotes/origin/master",
    "refs/remotes/origin/main",
)


class GitError(RuntimeError):
    pass


def git(clone: Path, *args: str) -> str:
    proc = subprocess.run(
        ["git", "-C", str(clone), *args], capture_output=True,
        text=True, encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed in {clone}: {proc.stderr.strip()}")
    return proc.stdout


def normalize_remote(url: str) -> str | None:
    m = _GITHUB.search(url.strip())
    return f"{m.group('owner')}/{m.group('name')}" if m else None


def remote_repo(path: Path) -> str | None:
    try:
        return normalize_remote(git(path, "remote", "get-url", "origin"))
    except GitError:
        return None


def group_clones(paths: list[Path]) -> tuple[dict[str, Path], list[tuple[str, str]]]:
    repos: dict[str, Path] = {}
    seen: set[str] = set()
    skipped: list[tuple[str, str]] = []
    for path in paths:
        repo = remote_repo(path)
        if repo is None:
            skipped.append((str(path), "no GitHub origin remote"))
            continue
        if repo.lower() in seen:
            continue
        seen.add(repo.lower())
        repos[repo] = path
    return repos, skipped


def default_ref(clone: Path) -> str | None:
    for ref in _DEFAULT_REFS:
        try:
            git(clone, "rev-parse", "--verify", "--quiet", ref)
            return ref
        except GitError:
            continue
    return None


@dataclass
class Commit:
    sha: str
    date: str
    subject: str
    body: str
    files: list[str]


def _changed_files(clone: Path, sha: str) -> list[str]:
    try:
        out = git(clone, "diff", "--name-only", f"{sha}~1", sha)
    except GitError:  # a root commit has no parent
        out = git(clone, "show", "--format=", "--name-only", sha)
    return sorted({f.strip() for f in out.splitlines() if f.strip()})


def fix_commits(clone: Path, ref: str) -> list[Commit]:
    commits: list[Commit] = []
    for line in git(clone, "log", ref, "--format=%H%x1f%cI%x1f%s").splitlines():
        sha, when, subject = line.split("\x1f", 2)
        if not is_cca_fix_subject(subject):
            continue
        body = git(clone, "show", "-s", "--format=%B", sha)
        commits.append(Commit(sha, when[:10], subject, body, _changed_files(clone, sha)))
    return commits
```

- [ ] **Step 5: Run them green**

Run: `python -m pytest tests/test_bugs_gitread.py -q`
Expected: `5 passed`.

- [ ] **Step 6: Prove each test red**

Apply each docstring's mutation to `cca_checks/bugs/gitread.py`, run that test alone, confirm it fails (in its assertion, or for Review Focus 2 with `UnicodeDecodeError` raised inside `fix_commits`, which is the defect), revert. Run the file green again.

- [ ] **Step 7: Lint and commit**

```bash
ruff check cca_checks tests
git add cca_checks/bugs/gitread.py tests/bugs_testkit.py tests/test_bugs_gitread.py
git commit -m "feat(bugs): read landed CCA fix commits, one pass per GitHub repository" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 4: Read scorecard ledgers and proof files, and match a run to a commit

**Files:**
- Create: `cca_checks/bugs/ledgers.py`
- Test: `tests/test_bugs_ledgers.py`

**Interfaces:**
- Consumes: `remote_repo` (Task 3).
- Produces: `fix_number(fix_id) -> int | None`; `match_key(finding_id: str) -> str` (`"FIX-01"` to `"FIX-1"`, other IDs unchanged); `LedgerRun` (dataclass: `key`, `repo`, `source`, `run_id`, `day: date | None`, `dispositions: dict[int, dict]`, `outcomes: dict[int, dict]`, `findings: dict[str, dict]`, `redproof: dict[int, str]`, `proof_source: str | None`; method `keys() -> set[str]`); `LoadReport` (dataclass: `malformed: int`, `ignored_proofs: list[str]`); `ledger_repo(path: Path) -> str | None`; `load_ledger(path: Path, repo: str, report: LoadReport) -> list[LedgerRun]`; `match_run(runs, repo: str, day: date, keys: set[str]) -> tuple[LedgerRun | None, str | None]`. Every row dict stored in a run carries `"_line": <1-based line number>`.

- [ ] **Step 1: Write the failing tests**

`tests/test_bugs_ledgers.py`:

```python
"""Tests for cca_checks.bugs.ledgers: scorecard ledgers, proof files, run matching."""
import json
from datetime import date

from bugs_testkit import make_repo

from cca_checks.bugs.ledgers import (
    LoadReport,
    fix_number,
    ledger_repo,
    load_ledger,
    match_key,
    match_run,
)


def _write(path, rows, extra_lines=()):
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(r) for r in rows] + list(extra_lines)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return path


DISP = {"ts": "2026-09-30T07:28:42+00:00", "run_id": "r1", "auditor": "bug-auditor",
        "category": "regex", "severity": "High", "priority": "P1",
        "verdict": "CONFIRMED", "fix_id": "FIX-1"}
OUT = {"run_id": "r1", "fix_id": "FIX-1", "result": "fixed", "architect_verdict": "APPROVED"}


def test_fix_ids_normalise():
    """Red proof: fix_number without int() (keep the digits string) -> 'FIX-01' != 'FIX-1'."""
    assert fix_number("FIX-01") == fix_number("FIX-1") == fix_number("fix-001") == 1
    assert fix_number("STAKES-001") is None
    assert fix_number(None) is None
    assert match_key("FIX-007") == "FIX-7"
    assert match_key("SEC-001") == "SEC-001"


def test_a_ledger_belongs_to_the_clone_that_holds_it(tmp_path):
    """Red proof: ledger_repo reads parents[1] instead of parents[2] -> None."""
    project = make_repo(tmp_path / "proj")
    path = _write(project / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl", [DISP])
    assert ledger_repo(path) == "acme/widgets"


def test_a_ledger_groups_rows_into_runs(tmp_path):
    """Red proof: load_ledger files outcome rows into dispositions -> outcomes is empty."""
    path = _write(tmp_path / "AUDITOR_SCORECARD.jsonl", [DISP, OUT])
    [run] = load_ledger(path, "acme/widgets", LoadReport())
    assert run.run_id == "r1"
    assert run.day == date(2026, 9, 30)
    assert run.dispositions[1]["verdict"] == "CONFIRMED"
    assert run.dispositions[1]["_line"] == 1
    assert run.outcomes[1]["architect_verdict"] == "APPROVED"
    assert run.keys() == {"FIX-1"}


def test_a_malformed_line_is_skipped_and_counted(tmp_path):
    """Review Focus 3. Red proof: drop `report.malformed += 1` -> the count stays 0."""
    path = _write(tmp_path / "AUDITOR_SCORECARD.jsonl", [DISP], extra_lines=["{not json"])
    report = LoadReport()
    [run] = load_ledger(path, "acme/widgets", report)
    assert report.malformed == 1
    assert 1 in run.dispositions


def test_a_finding_key_is_kept_beside_the_fix_id(tmp_path):
    """Red proof: load_ledger ignores the 'finding' key -> findings is empty."""
    row = {"ts": "2026-09-05T12:00:00Z", "verdict": "CONFIRMED",
           "finding": "SEC-001", "fix_id": "FIX-002"}
    [run] = load_ledger(_write(tmp_path / "L.jsonl", [row]), "acme/widgets", LoadReport())
    assert run.findings["SEC-001"]["fix_id"] == "FIX-002"
    assert run.keys() == {"FIX-2", "SEC-001"}


def test_a_proof_file_attaches_to_the_only_run(tmp_path):
    """Red proof: _attach_proofs lets a later RED overwrite a TAUTOLOGICAL -> FIX-2 reads RED."""
    path = _write(tmp_path / "AUDITOR_SCORECARD.jsonl", [DISP, OUT])
    proofs = [
        {"finding": "FIX-1", "nodeid": "t::a", "verdict": "RED"},
        {"finding": "FIX-1", "nodeid": "t::b", "verdict": "RED"},
        {"finding": "FIX-2", "nodeid": "t::c", "verdict": "TAUTOLOGICAL"},
        {"finding": "FIX-2", "nodeid": "t::d", "verdict": "RED"},
    ]
    (tmp_path / "redproof-fix1.json").write_text(json.dumps(proofs), encoding="utf-8")
    [run] = load_ledger(path, "acme/widgets", LoadReport())
    assert run.redproof == {1: "RED", 2: "TAUTOLOGICAL"}
    assert run.proof_source.endswith("redproof-fix1.json")


def test_a_proof_file_with_two_runs_and_no_run_id_is_ignored(tmp_path):
    """Red proof: attach to the first run when more than one exists -> nothing reported."""
    other = {**DISP, "run_id": "r2"}
    path = _write(tmp_path / "AUDITOR_SCORECARD.jsonl", [DISP, other])
    (tmp_path / "redproof-x.json").write_text(
        json.dumps([{"finding": "FIX-1", "verdict": "RED"}]), encoding="utf-8")
    report = LoadReport()
    runs = load_ledger(path, "acme/widgets", report)
    assert all(not r.redproof for r in runs)
    assert len(report.ignored_proofs) == 1


def _runs(tmp_path, *rows):
    return load_ledger(_write(tmp_path / "L.jsonl", list(rows)), "acme/widgets", LoadReport())


def test_match_needs_shared_keys_in_the_window(tmp_path):
    """Red proof: match_run without the key-overlap condition -> the STAKES commit matches r1."""
    runs = _runs(tmp_path, DISP)
    hit, problem = match_run(runs, "acme/widgets", date(2026, 10, 1), {"FIX-1"})
    assert hit is runs[0] and problem is None
    assert match_run(runs, "acme/widgets", date(2026, 10, 1), {"STAKES-001"}) == (None, None)
    assert match_run(runs, "acme/widgets", date(2026, 10, 9), {"FIX-1"}) == (None, None)
    assert match_run(runs, "acme/other", date(2026, 10, 1), {"FIX-1"}) == (None, None)


def test_an_ambiguous_match_upgrades_nothing(tmp_path):
    """Red proof: match_run returns hits[0] when several qualify -> a run comes back."""
    runs = _runs(tmp_path, DISP, {**DISP, "run_id": "r2"})
    hit, problem = match_run(runs, "acme/widgets", date(2026, 10, 1), {"FIX-1"})
    assert hit is None
    assert problem.startswith("ambiguous:")
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_bugs_ledgers.py -q`
Expected: collection fails, module `cca_checks.bugs.ledgers` not found.

- [ ] **Step 3: Write the implementation**

`cca_checks/bugs/ledgers.py`:

```python
"""Read the per-project scorecard ledgers and saved proof files, and match a run to a commit.

Ledger rows name findings by a run-local `fix_id` (FIX-1, FIX-01, FIX-001), which is
not the auditor-style ID a commit bullet carries (STAKES-001). So a run is matched to
a commit only on keys both sides actually share: FIX ids named in the commit's bullets,
and `finding` values where a ledger has them. Anything else stays unlinked, and the
bug's conditions stay `claimed`: nothing on record says which FIX it was.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from .gitread import remote_repo

WINDOW_BEFORE = timedelta(days=3)
WINDOW_AFTER = timedelta(days=1)  # timestamps mix time zones
_FIX = re.compile(r"FIX-0*(\d+)$", re.IGNORECASE)


def fix_number(fix_id: object) -> int | None:
    if not isinstance(fix_id, str):
        return None
    m = _FIX.match(fix_id.strip())
    return int(m.group(1)) if m else None


def match_key(finding_id: str) -> str:
    n = fix_number(finding_id)
    return f"FIX-{n}" if n is not None else finding_id


@dataclass
class LedgerRun:
    key: str
    repo: str
    source: str
    run_id: str | None
    day: date | None = None
    dispositions: dict[int, dict] = field(default_factory=dict)
    outcomes: dict[int, dict] = field(default_factory=dict)
    findings: dict[str, dict] = field(default_factory=dict)
    redproof: dict[int, str] = field(default_factory=dict)
    proof_source: str | None = None

    def keys(self) -> set[str]:
        fixes = set(self.dispositions) | set(self.outcomes)
        return {f"FIX-{n}" for n in fixes} | set(self.findings)


@dataclass
class LoadReport:
    malformed: int = 0
    ignored_proofs: list[str] = field(default_factory=list)


def ledger_repo(path: Path) -> str | None:
    # <project>/.claude/audits/AUDITOR_SCORECARD.jsonl
    return remote_repo(path.resolve().parents[2])


def _row_day(row: dict) -> date | None:
    ts = row.get("ts")
    if not isinstance(ts, str) or len(ts) < 10:
        return None
    try:
        return date.fromisoformat(ts[:10])
    except ValueError:
        return None


def load_ledger(path: Path, repo: str, report: LoadReport) -> list[LedgerRun]:
    runs: dict[str, LedgerRun] = {}
    text = path.read_text(encoding="utf-8", errors="replace")
    for number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            report.malformed += 1
            continue
        if not isinstance(row, dict):
            report.malformed += 1
            continue
        run_id = row.get("run_id") if isinstance(row.get("run_id"), str) else None
        key = f"{repo}:{run_id}" if run_id else f"{repo}:{path}#norun"
        run = runs.setdefault(key, LedgerRun(key=key, repo=repo, source=str(path), run_id=run_id))
        day = _row_day(row)
        if day and (run.day is None or day < run.day):
            run.day = day
        stored = {**row, "_line": number}
        num = fix_number(row.get("fix_id"))
        if "verdict" in row:
            if num is not None:
                run.dispositions[num] = stored
            if isinstance(row.get("finding"), str):
                run.findings[row["finding"]] = stored
        elif num is not None and any(k in row for k in ("result", "outcome", "architect_verdict")):
            run.outcomes[num] = stored
    _attach_proofs(path.parent, runs, report)
    return list(runs.values())


def _attach_proofs(folder: Path, runs: dict[str, LedgerRun], report: LoadReport) -> None:
    for proof in sorted(folder.glob("redproof-*.json")):
        stem = proof.stem[len("redproof-"):]
        target = next((r for r in runs.values() if r.run_id == stem), None)
        if target is None:
            if len(runs) != 1:
                report.ignored_proofs.append(f"{proof}: cannot tell which run it belongs to")
                continue
            target = next(iter(runs.values()))
        try:
            entries = json.loads(proof.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            report.ignored_proofs.append(f"{proof}: unreadable")
            continue
        target.proof_source = str(proof)
        for entry in entries if isinstance(entries, list) else []:
            num = fix_number(entry.get("finding")) if isinstance(entry, dict) else None
            if num is None:
                continue
            verdict = str(entry.get("verdict", "")).upper()
            if verdict == "RED":
                target.redproof.setdefault(num, "RED")
            else:  # one non-RED proof makes the finding unproved, whatever else ran
                target.redproof[num] = verdict or "UNKNOWN"


def match_run(runs: list[LedgerRun], repo: str, day: date,
              keys: set[str]) -> tuple[LedgerRun | None, str | None]:
    if not keys:
        return None, None
    need = math.ceil(len(keys) / 2)
    hits = [
        r for r in runs
        if r.repo.lower() == repo.lower() and r.day is not None
        and day - WINDOW_BEFORE <= r.day <= day + WINDOW_AFTER
        and len(keys & r.keys()) >= need
    ]
    if len(hits) == 1:
        return hits[0], None
    if len(hits) > 1:
        return None, "ambiguous: " + ", ".join(sorted(r.key for r in hits))
    return None, None
```

- [ ] **Step 4: Run them green**

Run: `python -m pytest tests/test_bugs_ledgers.py -q`
Expected: `9 passed`.

- [ ] **Step 5: Prove each test red**

Apply each docstring's mutation to `cca_checks/bugs/ledgers.py`, run that test alone, confirm an assertion failure, revert. Run the file green again.

- [ ] **Step 6: Lint and commit**

```bash
ruff check cca_checks tests
git add cca_checks/bugs/ledgers.py tests/test_bugs_ledgers.py
git commit -m "feat(bugs): read scorecard ledgers and proof files, match a run on shared keys" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 5: Assemble rows with their evidence, collapse duplicates, summarise

**Files:**
- Create: `cca_checks/bugs/backfill.py`
- Test: `tests/test_bugs_backfill.py`

**Interfaces:**
- Consumes: `BugRow`, `EVIDENCED`, `CLAIMED`, `ABSENT`, `CONDITIONS` (Task 1); `parse_message` (Task 2); `group_clones`, `default_ref`, `fix_commits` (Task 3); `LoadReport`, `LedgerRun`, `ledger_repo`, `load_ledger`, `match_key`, `match_run`, `fix_number` (Task 4).
- Produces: `BackfillResult` (dataclass: `rows: list[BugRow]`, `unparsed: list[str]`, `skipped: list[tuple[str, str]]`, `ambiguous: list[str]`, `malformed_ledger_lines: int`, `ignored_proofs: list[str]`); `backfill(clones: list[Path], ledgers: list[tuple[Path, str | None]]) -> BackfillResult`; `summarize(result: BackfillResult) -> dict`.

- [ ] **Step 1: Write the failing tests**

`tests/test_bugs_backfill.py`:

```python
"""Tests for cca_checks.bugs.backfill: rows, evidence, duplicates, and the summary."""
import json

from bugs_testkit import commit, make_repo, publish

from cca_checks.bugs.backfill import backfill, summarize
from cca_checks.bugs.model import ABSENT, CLAIMED, EVIDENCED

MSG = (
    "fix(store): 2 audit fixes for #12 (DEEP tier)\n\n"
    "P1:\n- FIX-1: delete and bump share one transaction. +test_atomic\n"
    "P2:\n- FIX-2: prefixes match in full.\n"
)


def _ledger(tmp_path, rows, proofs=None):
    folder = tmp_path / "audits"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "AUDITOR_SCORECARD.jsonl"
    path.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
    if proofs is not None:
        (folder / "redproof-r1.json").write_text(json.dumps(proofs), encoding="utf-8")
    return [(path, "acme/widgets")]


def _disp(n, verdict="CONFIRMED"):
    return {"ts": "2026-09-01T09:00:00Z", "run_id": "r1", "auditor": "bug-auditor",
            "category": "atomicity", "priority": "P1", "verdict": verdict, "fix_id": f"FIX-{n}"}


def _out(n, arch):
    return {"run_id": "r1", "fix_id": f"FIX-{n}", "result": "fixed", "architect_verdict": arch}


def _repo(tmp_path, message=MSG):
    repo = make_repo(tmp_path / "widgets")
    commit(repo, "src/store.py", "x = 1\n", message, date="2026-09-02T10:00:00+00:00")
    publish(repo)
    return repo


def _by_id(result):
    return {r.finding_id: r for r in result.rows}


def test_without_a_ledger_every_condition_but_fixed_is_claimed(tmp_path):
    """Red proof: TEST_HINT match sets EVIDENCED instead of CLAIMED -> red_proof reads evidenced."""
    rows = _by_id(backfill([_repo(tmp_path)], []))
    one, two = rows["FIX-1"], rows["FIX-2"]
    assert one.evidence == {"confirmed_l25": CLAIMED, "fixed": EVIDENCED,
                            "l6_approved": CLAIMED, "red_proof": CLAIMED}
    assert two.evidence["red_proof"] == ABSENT
    assert one.tier == two.tier == "historical"
    assert one.files == ["src/store.py"] and one.project == "widgets"


def test_all_four_on_record_is_proved(tmp_path):
    """Red proof: skip the redproof block in _apply_ledger -> FIX-1 stays historical."""
    ledgers = _ledger(tmp_path, [_disp(1), _out(1, "APPROVED")],
                      proofs=[{"finding": "FIX-1", "verdict": "RED"}])
    one = _by_id(backfill([_repo(tmp_path)], ledgers))["FIX-1"]
    assert one.tier == "proved"
    assert one.run_id == "r1"
    assert one.category == "atomicity" and one.severity == "P1"
    assert any(s.startswith("redproof:") for s in one.sources)


def test_blocked_architect_keeps_the_row_and_marks_l6_absent(tmp_path):
    """Red proof: _apply_ledger reads 'result' instead of 'architect_verdict' -> l6 stays claimed."""
    ledgers = _ledger(tmp_path, [_disp(1), _out(1, "BLOCKED")])
    one = _by_id(backfill([_repo(tmp_path)], ledgers))["FIX-1"]
    assert one.evidence["l6_approved"] == ABSENT
    assert one.evidence["confirmed_l25"] == EVIDENCED


def test_a_false_positive_on_record_marks_confirmation_absent(tmp_path):
    """Red proof: _apply_ledger treats any verdict as CONFIRMED -> confirmed_l25 reads evidenced."""
    ledgers = _ledger(tmp_path, [_disp(1, "FALSE_POSITIVE")])
    one = _by_id(backfill([_repo(tmp_path)], ledgers))["FIX-1"]
    assert one.evidence["confirmed_l25"] == ABSENT


def test_a_squash_duplicate_collapses_to_the_earlier_commit(tmp_path):
    """Red proof: _dedupe keys on (repo, finding_id, fix_commit) -> two FIX-1 rows."""
    repo = make_repo(tmp_path / "widgets")
    first = commit(repo, "src/a.py", "a\n", MSG, date="2026-09-02T10:00:00+00:00")
    commit(repo, "src/b.py", "b\n", MSG.replace("for #12", "for #12 (#13)"),
           date="2026-09-03T10:00:00+00:00")
    publish(repo)
    rows = backfill([repo], []).rows
    assert [r.finding_id for r in rows] == ["FIX-1", "FIX-2"]
    assert {r.fix_commit for r in rows} == {first}


def test_a_ledger_copied_into_a_worktree_counts_once(tmp_path):
    """Red proof: _load_runs without the digest check -> two identical runs, ambiguous, not proved."""
    [(path, repo)] = _ledger(tmp_path, [_disp(1), _out(1, "APPROVED")],
                             proofs=[{"finding": "FIX-1", "verdict": "RED"}])
    copy = tmp_path / "worktree" / "audits"
    copy.mkdir(parents=True)
    for f in path.parent.iterdir():
        (copy / f.name).write_bytes(f.read_bytes())
    ledgers = [(path, repo), (copy / path.name, repo)]
    result = backfill([_repo(tmp_path)], ledgers)
    assert result.ambiguous == []
    assert _by_id(result)["FIX-1"].tier == "proved"


def test_unparsed_commits_are_reported_and_summarised(tmp_path):
    """Red proof: backfill drops commits with no bullet without recording them -> unparsed is empty."""
    repo = _repo(tmp_path)
    commit(repo, "src/c.py", "c\n", "fix: apply CCA audit fixes\n\nSee the PR.\n",
           date="2026-09-04T10:00:00+00:00")
    publish(repo)
    result = backfill([repo], [])
    assert len(result.unparsed) == 1 and "apply CCA audit fixes" in result.unparsed[0]
    summary = summarize(result)
    assert summary["rows"] == 2
    assert summary["tiers"] == {"historical": 2}
    assert summary["unparsed"] == 1
    assert summary["by_repo"] == {"acme/widgets": 2}
    assert summary["evidence"]["red_proof"] == {"claimed": 1, "absent": 1}
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_bugs_backfill.py -q`
Expected: collection fails, module `cca_checks.bugs.backfill` not found.

- [ ] **Step 3: Write the implementation**

`cca_checks/bugs/backfill.py`:

```python
"""Rebuild the historical tier: landed fix commits, joined to whatever ledger evidence exists.

Every condition starts from what the commit alone can show: `fixed` is evidenced (the
commit is on the default branch), the rest are `claimed` because the commit text says
so, and `red_proof` is `absent` unless the bullet even mentions a test. A matched
ledger run can then upgrade a condition to `evidenced`, or mark it `absent` where the
record contradicts the claim (a FALSE_POSITIVE verdict, a BLOCKED architect, a
TAUTOLOGICAL proof).
"""
from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .gitread import default_ref, fix_commits, group_clones
from .ledgers import (
    LedgerRun,
    LoadReport,
    fix_number,
    ledger_repo,
    load_ledger,
    match_key,
    match_run,
)
from .model import ABSENT, CLAIMED, CONDITIONS, EVIDENCED, BugRow
from .parse import parse_message

TEST_HINT = re.compile(r"\+\s*test|\btest_\w+|\bred\b", re.IGNORECASE)


@dataclass
class BackfillResult:
    rows: list[BugRow] = field(default_factory=list)
    unparsed: list[str] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    ambiguous: list[str] = field(default_factory=list)
    malformed_ledger_lines: int = 0
    ignored_proofs: list[str] = field(default_factory=list)


def _apply_ledger(row: BugRow, run: LedgerRun) -> None:
    n = fix_number(row.finding_id)
    disp = run.dispositions.get(n) if n is not None else run.findings.get(row.finding_id)
    if disp is not None:
        confirmed = str(disp.get("verdict", "")).upper() == "CONFIRMED"
        row.evidence["confirmed_l25"] = EVIDENCED if confirmed else ABSENT
        row.sources.append(f"ledger:{run.source}#L{disp['_line']}")
        if disp.get("priority") in ("P1", "P2", "P3"):
            row.severity = disp["priority"]
        row.category = disp.get("category") or row.category
        row.auditor = disp.get("auditor") or row.auditor
        if n is None:
            n = fix_number(disp.get("fix_id"))
    if n is not None:
        out = run.outcomes.get(n)
        if out is not None:
            verdict = str(out.get("architect_verdict", "")).upper()
            if verdict.startswith("APPROVED"):
                row.evidence["l6_approved"] = EVIDENCED
            elif verdict in ("BLOCKED", "REVISE"):
                row.evidence["l6_approved"] = ABSENT
            row.sources.append(f"ledger:{run.source}#L{out['_line']}")
        proof = run.redproof.get(n)
        if proof is not None:
            row.evidence["red_proof"] = EVIDENCED if proof == "RED" else ABSENT
            row.sources.append(f"redproof:{run.proof_source}")
    if run.run_id:
        row.run_id = run.run_id


def _norm(text: str) -> str:
    return " ".join(text.lower().split())


def _dedupe(rows: list[BugRow]) -> list[BugRow]:
    best: dict[tuple[str, str, str], BugRow] = {}
    for row in rows:
        key = (row.repo.lower(), row.finding_id, _norm(row.summary))
        kept = best.get(key)
        if kept is None or (row.date, row.fix_commit) < (kept.date, kept.fix_commit):
            best[key] = row
    return sorted(best.values(), key=lambda r: (r.date, r.repo, r.fix_commit, r.finding_id))


def _load_runs(ledgers: list[tuple[Path, str | None]], result: BackfillResult) -> list[LedgerRun]:
    report = LoadReport()
    runs: list[LedgerRun] = []
    seen: set[str] = set()
    for path, repo in ledgers:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest in seen:  # the same ledger copied into a worktree
            continue
        seen.add(digest)
        repo = repo or ledger_repo(path)
        if repo is None:
            result.skipped.append((str(path), "ledger: cannot tell which repository it belongs to"))
            continue
        runs.extend(load_ledger(path, repo, report))
    result.malformed_ledger_lines = report.malformed
    result.ignored_proofs = report.ignored_proofs
    return runs


def backfill(clones: list[Path], ledgers: list[tuple[Path, str | None]]) -> BackfillResult:
    result = BackfillResult()
    runs = _load_runs(ledgers, result)
    repos, skipped = group_clones(clones)
    result.skipped.extend(skipped)
    rows: list[BugRow] = []
    for repo, clone in repos.items():
        ref = default_ref(clone)
        if ref is None:
            result.skipped.append((str(clone), "no origin default branch"))
            continue
        project = repo.split("/", 1)[1]
        for c in fix_commits(clone, ref):
            bullets = parse_message(c.body)
            if not bullets:
                result.unparsed.append(f"{repo} {c.sha[:12]} {c.subject}")
                continue
            keys = {match_key(b.finding_id) for b in bullets}
            run, problem = match_run(runs, repo, date.fromisoformat(c.date), keys)
            if problem:
                result.ambiguous.append(f"{repo} {c.sha[:12]}: {problem}")
            for b in bullets:
                is_fix_id = fix_number(b.finding_id) is not None
                row = BugRow(
                    project=project, repo=repo, finding_id=b.finding_id, fix_commit=c.sha,
                    date=c.date, files=list(c.files), summary=b.summary, severity=b.severity,
                    auditor=None if is_fix_id else b.finding_id.rsplit("-", 1)[0],
                    evidence={
                        "confirmed_l25": CLAIMED,
                        "fixed": EVIDENCED,
                        "l6_approved": CLAIMED,
                        "red_proof": CLAIMED if TEST_HINT.search(b.summary) else ABSENT,
                    },
                    sources=[f"git:{c.sha}"],
                )
                if run is not None:
                    _apply_ledger(row, run)
                rows.append(row)
    result.rows = _dedupe(rows)
    return result


def summarize(result: BackfillResult) -> dict:
    rows = result.rows
    return {
        "rows": len(rows),
        "by_repo": dict(Counter(r.repo for r in rows)),
        "tiers": dict(Counter(r.tier for r in rows)),
        "evidence": {c: dict(Counter(r.evidence[c] for r in rows)) for c in CONDITIONS},
        "unparsed": len(result.unparsed),
        "unparsed_commits": list(result.unparsed),
        "skipped": [f"{p}: {why}" for p, why in result.skipped],
        "ambiguous": list(result.ambiguous),
        "malformed_ledger_lines": result.malformed_ledger_lines,
        "ignored_proofs": list(result.ignored_proofs),
    }
```

- [ ] **Step 4: Run them green**

Run: `python -m pytest tests/test_bugs_backfill.py -q`
Expected: `7 passed`.

- [ ] **Step 5: Prove each test red**

Apply each docstring's mutation to `cca_checks/bugs/backfill.py`, run that test alone, confirm an assertion failure, revert. Run the file green again.

- [ ] **Step 6: Lint and commit**

```bash
ruff check cca_checks tests
git add cca_checks/bugs/backfill.py tests/test_bugs_backfill.py
git commit -m "feat(bugs): assemble bug rows with their evidence and collapse squash duplicates" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 6: Write the ledger and memos, and the CLI

**Files:**
- Create: `cca_checks/bugs/store.py`
- Create: `cca_checks/bugs/__main__.py`
- Test: `tests/test_bugs_store.py`

**Interfaces:**
- Consumes: `BugRow`, `memo_filename`, `render_memo` (Task 1); `backfill`, `summarize` (Task 5).
- Produces: `bugs_dir() -> Path`; `existing_ids(ledger: Path) -> set[str]`; `write(rows: list[BugRow], root: Path, modified: str) -> tuple[int, int]` (rows appended, memos written); `main(argv: list[str] | None = None) -> int` in `cca_checks/bugs/__main__.py`, runnable as `python -m cca_checks.bugs`.

- [ ] **Step 1: Write the failing tests**

`tests/test_bugs_store.py`:

```python
"""Tests for cca_checks.bugs.store and the bugs CLI."""
import json

from bugs_testkit import commit, make_repo, publish

from cca_checks.bugs.__main__ import main
from cca_checks.bugs.model import CONDITIONS, EVIDENCED, BugRow
from cca_checks.bugs.store import bugs_dir, existing_ids, write

MSG = "fix(store): 1 audit fix from CCA (FAST tier)\n\nP1:\n- BUG-001: a real defect. +test\n"


def _row(finding="BUG-001"):
    return BugRow(project="widgets", repo="acme/widgets", finding_id=finding,
                  fix_commit="ab" * 20, date="2026-09-01", files=["a.py"],
                  summary="a real defect. +test",
                  evidence={c: EVIDENCED for c in CONDITIONS}, sources=["git:ab"])


def test_bugs_dir_honours_the_environment(tmp_path, monkeypatch):
    """Red proof: bugs_dir ignores CCA_BUGS_DIR -> the home path comes back."""
    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path / "x"))
    assert bugs_dir() == tmp_path / "x"


def test_writing_twice_writes_nothing_the_second_time(tmp_path):
    """Red proof: write without the `bug_id in have` check -> the second call appends 2."""
    rows = [_row(), _row("BUG-002")]
    assert write(rows, tmp_path, "2026-10-11T00:00:00Z") == (2, 2)
    assert write(rows, tmp_path, "2026-10-11T00:00:00Z") == (0, 0)
    lines = (tmp_path / "bugs.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["tier"] == "proved"
    assert existing_ids(tmp_path / "bugs.jsonl") == {r.bug_id for r in rows}
    assert len(list((tmp_path / "memos").glob("*.md"))) == 2


def test_an_existing_memo_is_never_overwritten(tmp_path):
    """Red proof: write the memo without the exists() check -> the hand note is replaced."""
    row = _row()
    memo = tmp_path / "memos" / "widgets__abababababab__BUG-001.md"
    memo.parent.mkdir(parents=True)
    memo.write_text("hand note\n", encoding="utf-8")
    assert write([row], tmp_path, "2026-10-11T00:00:00Z") == (1, 0)
    assert memo.read_text(encoding="utf-8") == "hand note\n"


def _published(tmp_path):
    repo = make_repo(tmp_path / "widgets")
    commit(repo, "a.py", "a\n", MSG)
    publish(repo)
    return repo


def test_a_dry_run_writes_nothing(tmp_path, monkeypatch, capsys):
    """Red proof: main calls write() whatever --write says -> the directory exists."""
    target = tmp_path / "ledger"
    monkeypatch.setenv("CCA_BUGS_DIR", str(target))
    assert main(["backfill", "--repo", str(_published(tmp_path)), "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["rows"] == 1 and "written" not in out
    assert not target.exists()


def test_write_creates_a_missing_directory(tmp_path, monkeypatch, capsys):
    """Review Focus 4. Red proof: write() mkdir without parents=True -> FileNotFoundError in the call."""
    target = tmp_path / "does" / "not" / "exist"
    monkeypatch.setenv("CCA_BUGS_DIR", str(target))
    assert main(["backfill", "--repo", str(_published(tmp_path)), "--write", "--json"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["written"] == {"rows": 1, "memos": 1, "dir": str(target)}
    assert (target / "bugs.jsonl").exists()
    assert (target / "unparsed.txt").read_text(encoding="utf-8") == ""
```

- [ ] **Step 2: Run them and see them fail**

Run: `python -m pytest tests/test_bugs_store.py -q`
Expected: collection fails, module `cca_checks.bugs.store` not found.

- [ ] **Step 3: Write the implementation**

`cca_checks/bugs/store.py`:

```python
"""Append rows to the ledger and write one memo per new row.

Append-only: a row whose bug_id is already in the ledger is not written again, and an
existing memo is never overwritten, so a second run changes nothing and a note added
by hand survives. A correction is a new row naming the one it supersedes.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .model import BugRow, memo_filename, render_memo

LEDGER = "bugs.jsonl"
MEMOS = "memos"


def bugs_dir() -> Path:
    env = os.environ.get("CCA_BUGS_DIR")
    return Path(env) if env else Path.home() / ".claude" / "cca-bugs"


def existing_ids(ledger: Path) -> set[str]:
    if not ledger.exists():
        return set()
    ids: set[str] = set()
    for line in ledger.read_text(encoding="utf-8").splitlines():
        try:
            ids.add(json.loads(line)["bug_id"])
        except (json.JSONDecodeError, KeyError, TypeError):
            continue
    return ids


def write(rows: list[BugRow], root: Path, modified: str) -> tuple[int, int]:
    memos = root / MEMOS
    memos.mkdir(parents=True, exist_ok=True)
    ledger = root / LEDGER
    have = existing_ids(ledger)
    added = written = 0
    with ledger.open("a", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            if row.bug_id in have:
                continue
            fh.write(json.dumps(row.to_json(), ensure_ascii=False, sort_keys=True) + "\n")
            have.add(row.bug_id)
            added += 1
            memo = memos / memo_filename(row.bug_id)
            if not memo.exists():
                memo.write_text(render_memo(row, modified), encoding="utf-8", newline="\n")
                written += 1
    return added, written
```

`cca_checks/bugs/__main__.py`:

```python
"""python -m cca_checks.bugs backfill --repo PATH [--repo PATH ...] [--ledger PATH[=OWNER/NAME]]

Read-only unless --write. The dry run prints what would be written, so the numbers can
be reviewed before anything lands in the ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

from .backfill import backfill, summarize
from .gitread import GitError
from .store import bugs_dir, write


def _ledger_arg(spec: str) -> tuple[Path, str | None]:
    path, _, repo = spec.partition("=")
    return Path(path), (repo or None)


def _render(summary: dict) -> str:
    lines = [
        f"bugs: {summary['rows']}  tiers: {summary['tiers']}",
        f"lower bound: {summary['unparsed']} commit(s) matched but could not be parsed",
        f"by repository: {summary['by_repo']}",
    ]
    lines += [f"  {c}: {v}" for c, v in summary["evidence"].items()]
    for label in ("unparsed_commits", "skipped", "ambiguous", "ignored_proofs"):
        lines += [f"{label}: {item}" for item in summary[label]]
    if summary["malformed_ledger_lines"]:
        lines.append(f"malformed ledger lines skipped: {summary['malformed_ledger_lines']}")
    if "written" in summary:
        lines.append(f"written: {summary['written']}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="cca_checks bugs")
    sub = p.add_subparsers(dest="cmd", required=True)
    b = sub.add_parser("backfill", help="rebuild the historical tier (read-only unless --write)")
    b.add_argument("--repo", action="append", type=Path, required=True,
                   help="a clone to read; clones of one GitHub repository count once")
    b.add_argument("--ledger", action="append", default=[], metavar="PATH[=OWNER/NAME]",
                   help="a scorecard ledger; name its repository if it is not inside a clone")
    b.add_argument("--write", action="store_true", help="append to the ledger and write memos")
    b.add_argument("--json", action="store_true")
    a = p.parse_args(argv)

    try:
        result = backfill(a.repo, [_ledger_arg(s) for s in a.ledger])
    except GitError as exc:
        print(f"backfill stopped: {exc}", file=sys.stderr)
        return 1
    summary = summarize(result)
    if a.write:
        root = bugs_dir()
        modified = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        rows, memos = write(result.rows, root, modified)
        text = "".join(f"{line}\n" for line in result.unparsed)
        (root / "unparsed.txt").write_text(text, encoding="utf-8", newline="\n")
        summary["written"] = {"rows": rows, "memos": memos, "dir": str(root)}
    print(json.dumps(summary, indent=2) if a.json else _render(summary))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run them green**

Run: `python -m pytest tests/test_bugs_store.py -q`
Expected: `5 passed`.

- [ ] **Step 5: Prove each test red**

Apply each docstring's mutation (to `store.py` or `__main__.py` as named), run that test alone, confirm it fails (in its assertion, or for Review Focus 4 with `FileNotFoundError` raised inside `write`, which is the defect), revert. Run the file green again.

- [ ] **Step 6: Lint and commit**

```bash
ruff check cca_checks tests
git add cca_checks/bugs/store.py cca_checks/bugs/__main__.py tests/test_bugs_store.py
git commit -m "feat(bugs): append rows and memos, and the backfill command" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 7: Forward `python -m cca_checks bugs` to the subpackage

**Files:**
- Modify: `cca_checks/__main__.py` (the subparser block in `main()` and its dispatch)
- Test: `tests/test_bugs_store.py` (append one test)

**Interfaces:**
- Consumes: `cca_checks.bugs.__main__.main` (Task 6).

- [ ] **Step 1: Check whether #60 has merged**

Run: `git fetch -q origin && git log --oneline origin/master -5 && gh pr view 60 --json state -q .state`
If it says `MERGED`, rebase this branch on `origin/master` before editing (`git rebase origin/master`), so the edit lands on the file as #60 left it.

- [ ] **Step 2: Write the failing test** (append to `tests/test_bugs_store.py`)

```python
def test_the_top_level_cli_forwards_bugs(tmp_path, monkeypatch, capsys):
    """Red proof: forward a.bugs_args[1:] instead of a.bugs_args -> argparse exits 2."""
    from cca_checks.__main__ import main as top

    monkeypatch.setenv("CCA_BUGS_DIR", str(tmp_path / "ledger"))
    assert top(["bugs", "backfill", "--repo", str(_published(tmp_path)), "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["rows"] == 1
```

- [ ] **Step 3: Run it and see it fail**

Run: `python -m pytest tests/test_bugs_store.py::test_the_top_level_cli_forwards_bugs -q`
Expected: FAIL, argparse `invalid choice: 'bugs'` (exit 2).

- [ ] **Step 4: Add the forwarding**

In `cca_checks/__main__.py`, directly after the `capabilities` subparser block (before `pl = sub.add_parser("pipeline", ...)`), add:

```python
    bg = sub.add_parser("bugs", add_help=False,
                        help="cross-project ledger of the bugs CCA fixed (see: bugs backfill -h)")
    bg.add_argument("bugs_args", nargs=argparse.REMAINDER)
```

and directly after `a = p.parse_args(argv)`, before `if a.cmd == "pipeline":`, add:

```python
    if a.cmd == "bugs":
        from .bugs.__main__ import main as bugs_main

        return bugs_main(a.bugs_args)
```

- [ ] **Step 5: Run it green, then prove it red**

Run: `python -m pytest tests/test_bugs_store.py -q` (expect `6 passed`). Apply the docstring's mutation, see the test fail with `SystemExit: 2` raised in the call, revert, run green.

- [ ] **Step 6: Full suite, lint, commit**

Run: `python -m pytest -q` and `ruff check cca_checks tests`.
Expected: everything passes except the semgrep tests that also fail at `origin/master` on a workstation where semgrep cannot run; compare the failing set against `origin/master` and report both counts.

```bash
git add cca_checks/__main__.py tests/test_bugs_store.py
git commit -m "feat(bugs): python -m cca_checks bugs forwards to the ledger command" -m "Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 8: First real dry run (measurement, no code)

**Files:** none in this repository. Output goes to the terminal; nothing is written without `--write`.

- [ ] **Step 1: Pre-register**

Invoke `/preregister` before running anything. Record at least: the predicted number of `proved` rows (my expectation going in: between 0 and 10, since only one run is known to have saved its proofs), the predicted unparsed share of selected commits (expectation: under 25%), and what would falsify "the parser reads the real history" (unparsed share above 25%, or any hand-checked row whose summary is not the commit's own text). Commit the record before Step 2.

- [ ] **Step 2: Assemble the inputs**

One clone per remote is enough, since clones are grouped by remote. Collect every clone under the user's Documents folder and every scorecard ledger, without listing private names in this file:

```bash
find ~/Documents -maxdepth 2 -name .git -printf '%h\n' 2>/dev/null > /tmp/cca-clones.txt
find ~/Documents ~/.claude/archive -name AUDITOR_SCORECARD.jsonl 2>/dev/null > /tmp/cca-ledgers.txt
```

(Use the session scratchpad instead of `/tmp` when one exists.)

- [ ] **Step 3: Dry run**

```bash
python -m cca_checks bugs backfill $(sed 's/^/--repo /' /tmp/cca-clones.txt) $(sed 's/^/--ledger /' /tmp/cca-ledgers.txt)
```

(Paths with spaces need quoting; build the argument list with a short script if any are present.)

- [ ] **Step 4: Check by hand, then report**

Pick five rows at random and compare each `summary` to `git show -s <fix_commit>` in its clone. Read every line of `unparsed_commits`. Append the measured numbers under the pre-registration without editing the prediction. Report to the user and stop: `--write` runs only after the user approves the dry run.
