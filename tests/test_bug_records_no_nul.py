"""No U+0000 reaches bugs.jsonl, caused_by.jsonl or a memo.

PostgreSQL jsonb cannot store U+0000, so RE-call refuses such a record at index time and
`roundtrip-check` would list it as missing. Summaries and finding ids come from commit subjects
and model output CCA does not control, so every writer replaces U+0000 with U+FFFD first
(`records.no_nul`). Both the raw byte and its JSON escape are checked, because `json.dumps`
writes U+0000 as the six characters backslash-u-0-0-0-0.
"""
from __future__ import annotations

import contextlib
import json

from cca_checks.bugs.anchors import Anchor
from cca_checks.bugs.model import EVIDENCED, BugRow, memo_filename
from cca_checks.bugs.records import LessonRow, rerender_all
from cca_checks.bugs.store import append_annotations, write

NUL = "\x00"
ESCAPED = b"\\u0000"


def _clean(path) -> bool:
    data = path.read_bytes()
    return b"\x00" not in data and ESCAPED not in data


def _bug(finding_id: str = "BUG-1") -> BugRow:
    return BugRow(project="widgets", repo="acme/widgets", finding_id=finding_id,
                  fix_commit="a" * 40, date="2026-09-01", files=[f"pool{NUL}.py"],
                  summary=f"leaks a conn{NUL}ection", anchors=[Anchor("pool.py", f"le{NUL}ase",
                                                                       "1" * 64)],
                  evidence={"confirmed_l25": EVIDENCED, "fixed": EVIDENCED,
                            "l6_approved": EVIDENCED, "red_proof": EVIDENCED})


def _lesson() -> LessonRow:
    return LessonRow(project="widgets", repo="acme/widgets", date="2026-09-02",
                     rule=f"return pooled{NUL} connections on the owner thread",
                     pattern="conn.close()", anchors=[Anchor("pool.py", None, None)],
                     members=["widgets:aaaaaaaaaaaa:BUG-1", "widgets:bbbbbbbbbbbb:BUG-2",
                              "widgets:cccccccccccc:BUG-3"],
                     accepted_by="owner", accepted_at="2026-09-02")


def test_a_nul_in_a_row_never_reaches_the_ledger_or_its_memo(tmp_path):
    """`store.write` scrubs every string of every kind before the line and the memo.

    Red 2026-10-22: mutation in `store.write`, the `row = no_nul(row)` line removed; failed at
    `assert _clean(root / "bugs.jsonl")`.
    """
    root = tmp_path / "bugs"
    assert write([_bug(), _lesson()], root, "2026-10-22T00:00:00Z") == (2, 2)
    assert _clean(root / "bugs.jsonl")
    memos = sorted((root / "memos").iterdir())
    assert len(memos) == 2
    assert all(_clean(m) for m in memos)
    bug = json.loads((root / "bugs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert bug["summary"] == "leaks a conn\ufffdection"
    assert bug["files"] == ["pool\ufffd.py"]


def test_a_nul_in_a_finding_id_never_reaches_the_ledger(tmp_path):
    """The scrub runs before the id is taken, so a NUL in model output never names a row.

    Red 2026-10-22: mutation in `store.write`, the `row = no_nul(row)` line removed; failed at
    `assert _clean(root / "bugs.jsonl")`. Under that mutation the memo's file name also holds
    the NUL and `write` raises after the ledger line is out, which is why the call suppresses
    ValueError: the line already written is what this test guards.
    """
    root = tmp_path / "bugs"
    with contextlib.suppress(ValueError):
        write([_bug(f"BUG{NUL}1")], root, "2026-10-22T00:00:00Z")
    assert _clean(root / "bugs.jsonl")
    bug = json.loads((root / "bugs.jsonl").read_text(encoding="utf-8"))
    assert bug["bug_id"] == "widgets:aaaaaaaaaaaa:BUG\ufffd1"
    assert (root / "memos" / memo_filename(bug["bug_id"])).exists()


def test_a_nul_never_reaches_the_caused_by_log(tmp_path):
    """`append_annotations` scrubs the annotation it appends.

    Red 2026-10-22: mutation in `append_annotations`, `no_nul(annotation)` to `annotation`;
    failed at `assert _clean(tmp_path / "caused_by.jsonl")`.
    """
    append_annotations(tmp_path, [{"bug_id": f"widgets:bbbbbbbbbbbb:BUG{NUL}2",
                                   "caused_by": [f"widgets:aaaaaaaaaaaa:BUG{NUL}1"],
                                   "method": "git-blame", "base_commit": "c" * 40,
                                   "fix_commit": "b" * 40, "derived_at": "2026-10-22T00:00:00Z"}])
    assert _clean(tmp_path / "caused_by.jsonl")
    line = json.loads((tmp_path / "caused_by.jsonl").read_text(encoding="utf-8"))
    assert line["caused_by"] == ["widgets:aaaaaaaaaaaa:BUG\ufffd1"]


def test_rerender_never_writes_a_nul_from_an_old_ledger_line(tmp_path):
    """A ledger line written before the scrub existed still yields a clean memo.

    Red 2026-10-22: mutation in `rerender_all`, `render_memo(clean, ...)` to
    `render_memo(row, ...)` (the memo name stays scrubbed, its text does not); failed at
    `assert all(_clean(m) for m in memos)`. Removing the scrub outright fails earlier, with
    `ValueError: embedded null character` from the memo's file name, which is not red proof.
    """
    root = tmp_path / "bugs"
    root.mkdir()
    line = json.dumps(_bug(f"BUG{NUL}1").to_json(), sort_keys=True)
    assert ESCAPED.decode() in line  # the old line really holds U+0000
    (root / "bugs.jsonl").write_text(line + "\n", encoding="utf-8", newline="\n")
    report = rerender_all(root, "2026-10-22T00:00:00Z")
    assert report.rendered == 1 and report.skipped == []
    memos = list((root / "memos").iterdir())
    assert all(_clean(m) for m in memos)
