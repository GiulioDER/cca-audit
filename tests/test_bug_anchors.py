"""Tests for cca_checks.bugs.anchors: the (file, symbol, code_sha) key shared with RE-call.

The resolver is a port of RE-call's `recall/anchors.py`, not an import of it. Two
implementations of one key agree only if both reproduce the same vectors, so the vectors in
`tests/fixtures/audit_memory/anchor_vectors.json` (copied from RE-call, see
`test_bug_records_roundtrip.py::test_contract_copies_match_pinned_recall_commit`) are the test.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from cca_checks.bugs.anchors import (
    Anchor,
    anchor_for,
    anchors_for_findings,
    code_sha,
    enclosing_symbol,
    resolve_symbols,
    symbol_sha,
)

VECTORS = json.loads((Path(__file__).parent / "fixtures" / "audit_memory"
                      / "anchor_vectors.json").read_text(encoding="utf-8"))

POOL = (
    "import threading\n"
    "\n"
    "\n"
    "class Pool:\n"
    "    def lease(self):\n"
    "        return 1\n"
    "\n"
    "    def release(self, conn):\n"
    "        conn.close()\n"
)


@pytest.mark.parametrize("vector", VECTORS, ids=[v["name"] for v in VECTORS])
def test_vectors_reproduce(vector):
    """Every vector's spans, hashes, file hash and enclosing symbols, exactly.

    Red 2026-10-21, node `test_vectors_reproduce[crlf_line_endings]`: mutation of
    `anchors.normalize` to a normaliser that forgets line endings (no CR replacement, and
    `rstrip` limited to spaces and tabs); failed at
    `assert symbol_sha(source, want["symbol"]) == want["sha"]` (4915c276... != 69f6485a...).
    Red 2026-10-21, nodes `[property_with_setter]` and `[conditional_definitions]`: mutation
    `name = dotted` in `resolve_symbols` (no `#n` suffix); failed at `assert got == [...]`,
    "At index 2 diff: ('Box.value', 6, 8) != ('Box.value#2', 6, 8)".
    """
    source = vector["source"]
    got = [(s.symbol, s.start_line, s.end_line) for s in resolve_symbols(source)]
    assert got == [(e["symbol"], e["start_line"], e["end_line"]) for e in vector["expect"]]
    for want in vector["expect"]:
        assert symbol_sha(source, want["symbol"]) == want["sha"]
    assert symbol_sha(source, None) == vector["file_sha"]
    assert code_sha(source) == vector["file_sha"]
    for case in vector["enclosing"]:
        assert enclosing_symbol(source, case["line"]) == case["symbol"]


def test_crlf_checkout_same_sha(tmp_path):
    """A Windows checkout (CRLF) and a Linux checkout (LF) of one file give one anchor.

    The file is written as bytes, so no newline translation hides the CRs from the reader.

    Red 2026-10-21: the same `normalize` mutation as above (line endings forgotten); failed at
    `assert crlf == lf`, differing attribute `code_sha` (0b33dd23... != fe61b35a...).
    """
    (tmp_path / "lf").mkdir()
    (tmp_path / "crlf").mkdir()
    (tmp_path / "lf" / "pool.py").write_bytes(POOL.encode())
    (tmp_path / "crlf" / "pool.py").write_bytes(POOL.replace("\n", "\r\n").encode())
    lf = anchor_for(tmp_path / "lf", "pool.py", 6)
    crlf = anchor_for(tmp_path / "crlf", "pool.py", 6)
    assert lf == Anchor("pool.py", "Pool.lease", symbol_sha(POOL, "Pool.lease"))
    assert crlf == lf


def test_non_python_file_anchors_at_file_level(tmp_path):
    """A file the Python resolver does not cover anchors at file level: symbol None, whole-file sha.

    The same holds for a `.py` file that does not parse, and for line 0 (L2's "file-level").

    A text file that happens to parse as Python (`notes.txt` below) is the case that tells the
    suffix rule apart from the SyntaxError fallback: Rust never parses, so it alone cannot.

    Red 2026-10-21: mutation `if line > 0:` in `anchor_at` (suffix check removed); failed at
    `assert anchor_for(tmp_path, "notes.txt", 6) == Anchor("notes.txt", None, ...)`, symbol
    'Pool.lease' != None. Before `notes.txt` was added the same mutation PASSED, which is why
    it is there.
    """
    rust = "fn lease() -> u32 {\r\n    1\r\n}\r\n"
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "lib.rs").write_bytes(rust.encode())
    (tmp_path / "notes.txt").write_bytes(POOL.encode())
    assert anchor_for(tmp_path, "notes.txt", 6) == Anchor("notes.txt", None, code_sha(POOL))
    (tmp_path / "broken.py").write_bytes(b"def f(:\n    pass\n")
    (tmp_path / "pool.py").write_bytes(POOL.encode())
    rs = anchor_for(tmp_path, "src/lib.rs", 2)
    assert rs == Anchor("src/lib.rs", None, code_sha(rust))
    assert anchor_for(tmp_path, "broken.py", 1) == Anchor("broken.py", None,
                                                           code_sha("def f(:\n    pass\n"))
    assert anchor_for(tmp_path, "pool.py", 0) == Anchor("pool.py", None, code_sha(POOL))
    assert anchor_for(tmp_path, "pool.py", 1) == Anchor("pool.py", None, code_sha(POOL))


def test_paths_are_repo_relative_forward_slash(tmp_path):
    """`file` is repo-relative with forward slashes, however the finding spelled it.

    Red 2026-10-21 (Windows): mutation `return str(full.relative_to(root))` in `_relative`;
    failed at `assert a.file == "pkg/sub/pool.py"` (got backslash separators). On Linux this
    mutation is invisible, since `str` already gives forward slashes there.
    Red 2026-10-21: mutation in `_relative` returning the spelling with only separators swapped
    (no resolution against the root); failed at the same assertion ('./pkg/sub/pool.py').
    """
    sub = tmp_path / "pkg" / "sub"
    sub.mkdir(parents=True)
    (sub / "pool.py").write_bytes(POOL.encode())
    for spelling in ("pkg/sub/pool.py", "pkg\\sub\\pool.py", "./pkg/sub/pool.py",
                     str(sub / "pool.py")):
        a = anchor_for(tmp_path, spelling, 9)
        assert a.file == "pkg/sub/pool.py"
        assert a.symbol == "Pool.release"
    assert a.to_json() == {"file": "pkg/sub/pool.py", "symbol": "Pool.release",
                           "code_sha": symbol_sha(POOL, "Pool.release")}
    with pytest.raises(ValueError):
        anchor_for(tmp_path, "../outside.py", 1)


def test_anchors_for_findings_skips_unresolvable(tmp_path):
    """One anchor per resolvable finding, in finding order; a finding with no file is skipped.

    Red 2026-10-21: mutation `except ():` in `anchors_for_findings` (nothing caught); the call
    raised FileNotFoundError for `gone.py`, which is the defect itself: one unreadable finding
    would abort every anchor of the run.
    """
    (tmp_path / "pool.py").write_bytes(POOL.encode())
    findings = [
        {"id": "BUG-1", "file": "pool.py", "line": 6},
        {"id": "BUG-2", "file": "gone.py", "line": 3},
        {"id": "BUG-3"},
        {"id": "BUG-4", "file": "pool.py", "line": "9"},
        {"id": "BUG-5", "file": "../escape.py", "line": 1},
    ]
    got = anchors_for_findings(tmp_path, findings)
    assert [(a.file, a.symbol) for a in got] == [("pool.py", "Pool.lease"),
                                                 ("pool.py", "Pool.release")]


def test_later_branches_do_not_rename_prior_handlers():
    """Later branches cannot rename or rehash an unchanged exception-handler definition.

    Production target: anchors._statement_bodies. The pre-fix 7113cf8 ordering visits
    handlers after else/finally. This node must fail its handler-identity assertion there;
    test_vectors_reproduce[try_except_else_finally] must fail its ordered-span assertion.
    Exact red and restored-green receipts are recorded for the coordinated parity repair.
    """
    source = next(v["source"] for v in VECTORS if v["name"] == "try_except_else_finally")
    earlier = "\n".join(source.splitlines()[:6]) + "\n"

    assert enclosing_symbol(earlier, 5) == "f#2"
    assert enclosing_symbol(source, 5) == "f#2", "later branches renamed the prior handler"
    assert symbol_sha(earlier, "f#2") == symbol_sha(source, "f#2")
