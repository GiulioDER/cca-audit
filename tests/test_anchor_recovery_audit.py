"""CCA regression cases against the recorded pre-fix source snapshot.

Each behavior case was red at its assertion on the original recovery implementation.
ENV-001 uses the documented no-op fixture mutation under injected Git overrides.
Proof commands and outcomes are retained with the CCA run.
"""
from __future__ import annotations

import json
import os
import stat
from collections import Counter
from dataclasses import replace

from bugs_testkit import commit, make_repo, run_git
from bugs_testkit import isolated_git_environment as isolated_git_environment
from test_anchor_recovery import history, note, row

from cca_checks.bugs import anchor_log, prior, recover_anchors, store
from cca_checks.bugs.__main__ import main as bugs_main
from cca_checks.bugs.recover_anchors import Unavailable, plan, recover


def test_archive_parent_links_are_refused(tmp_path):
    """SEC-001 / FIX-001: public plan must refuse a linked ancestor before reading metadata."""
    r, repo, archive, _ = history(tmp_path)
    ledger = tmp_path / "ledger"
    store.write([r], ledger, "stamp")
    linked = tmp_path / "linked-archives"
    if os.name == "nt":
        import _winapi
        _winapi.CreateJunction(str(archive.parent), str(linked))
    else:
        linked.symlink_to(archive.parent, target_is_directory=True)
    try:
        blocked = False
        try:
            plan(ledger, [repo], [linked])
        except Unavailable:
            blocked = True
        assert blocked, "a linked archive root was followed"
    finally:
        if os.name == "nt":
            os.rmdir(linked)  # remove this test's junction, never traverse its target
        else:
            linked.unlink()


def test_duplicate_symbol_repair_cannot_pin_other_occurrence(tmp_path):
    """STAKES-001 / FIX-002: source-order suffixes are not a stable cross-tree identity."""
    repo = make_repo(tmp_path / "repo")
    before = "def f():\n    return 0\n\ndef f():\n    return -1\n"
    base = commit(repo, "pool.py", before, "input")
    tree = run_git(repo, "rev-parse", f"{base}^{{tree}}").strip()
    fixed = "def f():\n    return 99\n\n" + before.replace("return -1", "return 2")
    head = commit(repo, "pool.py", fixed, "repair")
    r = replace(row(), fix_commit=head)
    archive = tmp_path / "archives" / "run-1"
    archive.mkdir(parents=True)
    data = {"meta.json": {"repo": r.repo, "run_id": r.run_id, "commit": head},
            "summary.json": {"run_id": r.run_id, "commit": head,
                             "base_tree": tree, "base_commit": base},
            "L2.json": [{"id": r.finding_id, "file": "pool.py", "line": 5}],
            "L4.json": [{"finding": r.finding_id, "files": ["pool.py"]}]}
    for name, value in data.items():
        (archive / name).write_text(json.dumps(value), encoding="utf-8")
    try:
        n = recover(r, archive, repo)
    except Unavailable:
        return
    assert n["anchors"][0]["symbol"] is None, "occurrence labels pinned the wrong function"


def test_archive_read_is_bounded_even_with_stale_size(tmp_path, monkeypatch):
    """SEC-002 / FIX-003: a reported small size cannot authorize an unbounded read."""
    r, repo, archive, _ = history(tmp_path)
    target = archive / "meta.json"
    target.write_bytes(target.read_bytes() + b" " * 4096)
    monkeypatch.setattr(recover_anchors, "_MAX_BLOB", 2048)
    real_stat, real_fstat = type(target).stat, os.fstat

    def stale_path(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        values = list(result)
        if path == target:
            values[6] = 0
        return os.stat_result(values)

    def stale_handle(fd):
        result = real_fstat(fd)
        values = list(result)
        if stat.S_ISREG(result.st_mode):
            values[6] = 0
        return os.stat_result(values)

    monkeypatch.setattr(type(target), "stat", stale_path)
    monkeypatch.setattr(os, "fstat", stale_handle)
    blocked = False
    try:
        recover(r, archive, repo)
    except Unavailable:
        blocked = True
    assert blocked, "actual metadata bytes exceeded the recovery limit"


def test_nonregular_metadata_is_rejected_before_read(tmp_path, monkeypatch):
    """SEC-002 / FIX-003: special-file mode is rejected without performing a blocking read."""
    r, repo, archive, _ = history(tmp_path)
    target = archive / "meta.json"
    real_stat, real_fstat = type(target).stat, os.fstat
    real_read = type(target).read_bytes
    reads = []

    def special_path(path, *args, **kwargs):
        result = real_stat(path, *args, **kwargs)
        values = list(result)
        if path == target:
            values[0], values[6] = stat.S_IFIFO | 0o600, 0
        return os.stat_result(values)

    def special_handle(fd):
        result = real_fstat(fd)
        values = list(result)
        if stat.S_ISREG(result.st_mode):
            values[0], values[6] = stat.S_IFIFO | 0o600, 0
        return os.stat_result(values)

    def observe(path):
        if path == target:
            reads.append(path)
        return real_read(path)

    monkeypatch.setattr(type(target), "stat", special_path)
    monkeypatch.setattr(os, "fstat", special_handle)
    monkeypatch.setattr(type(target), "read_bytes", observe)
    try:
        recover(r, archive, repo)
    except Unavailable:
        pass
    assert reads == [], "metadata advertised as a special file was read"


def test_prior_malformed_files_has_a_safe_public_result(tmp_path, monkeypatch):
    """BUG-001 / FIX-004: malformed row fields cannot bypass the prior reader's rejection."""
    r = row()
    root = tmp_path / "ledger"
    store.write([r], root, "stamp")
    data = r.to_json()
    data["files"] = 7
    (root / "bugs.jsonl").write_text(json.dumps(data) + "\n", encoding="utf-8")
    anchor_log.append(root, [note(r)])
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    error, result = None, None
    try:
        result = bugs_main(["prior", "--files", "pool.py", "--repo-name", "acme/widgets",
                            "--root", str(tmp_path)])
    except TypeError as exc:
        error = str(exc)
    assert error is None, f"malformed files crashed prior: {error}"
    assert result == 0


def test_malformed_run_has_a_structured_public_result(tmp_path, monkeypatch, capsys):
    """BUG-002 / FIX-005: unsupported identity types must not become dictionary keys."""
    r, repo, archive, _ = history(tmp_path)
    root = tmp_path / "ledger"
    root.mkdir()
    data = r.to_json()
    data["run_id"] = ["run-1"]
    (root / "bugs.jsonl").write_text(json.dumps(data) + "\n", encoding="utf-8")
    monkeypatch.setenv("CCA_BUGS_DIR", str(root))
    error, result = None, None
    try:
        result = recover_anchors.main(["--repo", str(repo), "--archive", str(archive.parent),
                                       "--json"])
    except TypeError as exc:
        error = str(exc)
    assert error is None, f"malformed run crashed recovery: {error}"
    assert result in (0, 1)
    assert isinstance(json.loads(capsys.readouterr().out), dict)


def test_repository_case_preserves_authored_identity(tmp_path):
    """BUG-003 / FIX-006: selection and recovery use the same GitHub identity rules."""
    r, repo, archive, _ = history(tmp_path)
    root = tmp_path / "ledger"
    store.write([r], root, "stamp")
    p = archive / "meta.json"
    data = json.loads(p.read_text(encoding="utf-8"))
    data["repo"] = "Acme/Widgets"
    p.write_text(json.dumps(data), encoding="utf-8")
    result = plan(root, [repo], [archive.parent])
    assert len(result.annotations) == 1, result.unresolved
    assert result.annotations[0]["repo"] == r.repo


def test_each_archive_is_one_immutable_snapshot(tmp_path, monkeypatch):
    """PERF-001 / FIX-008: rows from one audit reuse its parsed and hashed snapshot."""
    r, repo, archive, _ = history(tmp_path)
    r2 = replace(r, finding_id="BUG-2")
    for filename, key in [("L2.json", "id"), ("L4.json", "finding")]:
        p = archive / filename
        data = json.loads(p.read_text(encoding="utf-8"))
        data.append({**data[0], key: r2.finding_id})
        p.write_text(json.dumps(data), encoding="utf-8")
    root = tmp_path / "ledger"
    store.write([r, r2], root, "stamp")
    reads = Counter()
    original = recover_anchors._json

    def observe(path):
        reads[path.name] += 1
        return original(path)

    monkeypatch.setattr(recover_anchors, "_json", observe)
    assert len(plan(root, [repo], [archive.parent]).annotations) == 2
    assert reads == {"meta.json": 1, "summary.json": 1, "L2.json": 1, "L4.json": 1}


def test_repaired_symbol_hashes_are_not_recomputed(tmp_path, monkeypatch):
    """PERF-002 / FIX-009: existence checks reuse their computed repaired-symbol hashes."""
    r, repo, archive, _ = history(tmp_path)
    calls = []
    original = recover_anchors.symbol_sha

    def observe(text, symbol):
        calls.append(symbol)
        return original(text, symbol)

    monkeypatch.setattr(recover_anchors, "symbol_sha", observe)
    assert recover(r, archive, repo)["anchors"][0]["symbol"] == "lease"
    assert calls == ["lease", "lease"]


def test_git_fixture_clears_ambient_repository_overrides():
    """ENV-001 / FIX-010: red proof injects overrides and makes the fixture a no-op."""
    assert not any(k in os.environ for k in ("GIT_DIR", "GIT_WORK_TREE", "GIT_INDEX_FILE"))
    assert os.environ.get("GIT_CONFIG_GLOBAL") == os.devnull
    assert os.environ.get("GIT_CONFIG_NOSYSTEM") == "1"


def test_independent_annotation_sources_do_not_erase_each_other(tmp_path, monkeypatch):
    """DAT-001 / FIX-011: failure of the optional anchor log cannot erase known causes."""
    r = row()
    store.write([r], tmp_path, "stamp")
    store.append_annotations(tmp_path, [{"bug_id": r.id, "caused_by": ["widgets:earlier"]}])
    n = note(r)
    anchor_log.append(tmp_path, [n])
    original = anchor_log.read

    def unavailable(*args):
        raise PermissionError("private fixture cannot read this source")

    monkeypatch.setattr(anchor_log, "read", unavailable)
    assert prior.load_rows(tmp_path / store.LEDGER)[0]["caused_by"] == ["widgets:earlier"]
    monkeypatch.setattr(anchor_log, "read", original)
    monkeypatch.setattr(prior, "caused_by_annotations", unavailable)
    assert prior.load_rows(tmp_path / store.LEDGER)[0]["anchors"] == n["anchors"]


def test_duplicate_parent_cannot_transfer_a_unique_child(tmp_path):
    """STAKES-001 / FIX-002: a unique child cannot establish identity under duplicate parents."""
    repo = make_repo(tmp_path / "repo")
    before = "def f():\n    return 0\n\ndef f():\n    def inner():\n        return -1\n    return inner()\n"
    base = commit(repo, "pool.py", before, "input")
    tree = run_git(repo, "rev-parse", f"{base}^{{tree}}").strip()
    fixed = "def f():\n    def inner():\n        return 99\n    return inner()\n\ndef f():\n    return 2\n"
    head = commit(repo, "pool.py", fixed, "repair")
    r = replace(row(), fix_commit=head)
    archive = tmp_path / "archives" / "run-1"
    archive.mkdir(parents=True)
    data = {"meta.json": {"repo": r.repo, "run_id": r.run_id, "commit": head},
            "summary.json": {"run_id": r.run_id, "commit": head,
                             "base_tree": tree, "base_commit": base},
            "L2.json": [{"id": r.finding_id, "file": "pool.py", "line": 6}],
            "L4.json": [{"finding": r.finding_id, "files": ["pool.py"]}]}
    for name, value in data.items():
        (archive / name).write_text(json.dumps(value), encoding="utf-8")
    try:
        n = recover(r, archive, repo)
    except Unavailable:
        return
    assert n["anchors"][0]["symbol"] is None, "duplicate parent transferred the child identity"



def test_unneeded_archives_are_not_loaded(tmp_path, monkeypatch):
    """PERF-001 / FIX-008: already anchored rows must require only archive metadata."""
    from cca_checks.bugs.anchors import Anchor
    r, repo, archive, _ = history(tmp_path)
    r = replace(r, anchors=[Anchor("pool.py", None, "a" * 64)])
    ledger = tmp_path / "ledger"
    store.write([r], ledger, "stamp")
    calls = []
    original = recover_anchors._json

    def record(path):
        calls.append(path.name)
        return original(path)

    monkeypatch.setattr(recover_anchors, "_json", record)
    report = plan(ledger, [repo], [archive.parent])
    assert report.already_anchored == 1
    assert calls == ["meta.json"], "unused archives were eagerly parsed"
