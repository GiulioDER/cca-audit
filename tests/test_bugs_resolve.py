"""Stage 2 against a fake gh and a temp ledger directory (CCA_BUGS_DIR is private)."""
import json
from datetime import datetime, timezone

import pytest
from test_bugs_forward import make_record  # reuse the record builder

from cca_checks.bugs.resolve import resolve

SHA = "a" * 40
MERGE_SHA = "d" * 40  # hex, or github.lookup would answer Unknown


def pending_record(root, tmp_path, name, *, captured="2026-10-13T10:00:00Z", commit=SHA):
    rec = make_record(tmp_path / name, proofs=[{"finding": "FIX-001", "nodeid": "t",
                                                "verdict": "RED"}])
    meta = {"schema": 1, "run_id": name, "commit": commit, "repo": "acme/widgets",
            "branch": "feat/x", "captured_utc": captured, "host": "h"}
    (rec / "meta.json").write_text(json.dumps(meta))
    dest = root / "pending" / f"{name}__{commit[:12]}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    rec.rename(dest)
    return dest


def gh_merged(args):
    if args[-1] == "repos/acme/widgets":
        return json.dumps({"default_branch": "master"})
    return json.dumps([{"number": 7, "state": "closed", "merged_at": "2026-10-14T09:00:00Z",
                        "merge_commit_sha": MERGE_SHA, "base": {"ref": "master"}}])


def gh_open(args):
    if args[-1] == "repos/acme/widgets":
        return json.dumps({"default_branch": "master"})
    return json.dumps([{"number": 7, "state": "open", "merged_at": None,
                        "merge_commit_sha": None, "base": {"ref": "master"}}])


def test_a_merged_run_is_written_once_and_moved(_private_bugs_dir, tmp_path):
    """Invariant: a merged record's rows are appended once and the folder leaves pending/.

    Red 2026-10-13: mutation deleting `_move(folder, root, RECORDED)`; failed at
    `assert not any((root / "pending").iterdir())`."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1")
    rep = resolve(root, write=True, run=gh_merged)
    assert rep.rows_written == 2 and len(rep.recorded) == 1
    assert not any((root / "pending").iterdir())
    assert (root / "recorded" / f"r1__{SHA[:12]}" / "meta.json").exists()
    assert resolve(root, write=True, run=gh_merged).rows_written == 0


def test_two_runs_in_one_merge_keep_both_rows(_private_bugs_dir, tmp_path):
    """Review Focus 1. Invariant: two runs squashed into one merge commit both keep their rows.

    Red 2026-10-13: mutation leaving every ordinal at 1; failed at
    `assert rep.rows_written == 4` (2: the second run's rows collided and were skipped).
    Red 2026-10-13 (fix round 1): mutation not marking a batch-given ordinal as taken in
    `_Ordinals.assign`; failed at the same assert (2 == 4)."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1", captured="2026-10-13T10:00:00Z")
    pending_record(root, tmp_path, "r2", captured="2026-10-13T11:00:00Z", commit="b" * 40)
    rep = resolve(root, write=True, run=gh_merged)
    assert rep.rows_written == 4
    ids = [json.loads(x)["bug_id"] for x in (root / "bugs.jsonl").read_text().splitlines()]
    assert f"widgets:{MERGE_SHA[:12]}:BUG-001~2" in ids


def test_a_crash_before_the_move_writes_nothing_twice(_private_bugs_dir, tmp_path, monkeypatch):
    """Review Focus 5. Invariant: a rerun after a crash between write and move appends
    nothing and completes the move.

    Red 2026-10-13: mutation assigning ordinals against the ledger's existing ids instead
    of within the batch; failed at `assert rep.rows_written == 0 and len(rep.recorded) == 1`.
    Red 2026-10-13 (fix round 1): mutation removing the reuse of the run's own ledger
    ordinal in `_Ordinals.assign`; failed at the same assert (2 == 0)."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1")
    import cca_checks.bugs.resolve as rs

    def crash(*args):
        raise OSError("crash")

    with monkeypatch.context() as m:
        m.setattr(rs, "_move", crash)
        with pytest.raises(OSError):
            resolve(root, write=True, run=gh_merged)
    rep = resolve(root, write=True, run=gh_merged)
    assert rep.rows_written == 0 and len(rep.recorded) == 1
    assert not any((root / "pending").iterdir())


def test_a_second_run_of_one_merge_resolved_later_keeps_its_rows(_private_bugs_dir, tmp_path):
    """Fix round 1 (a). Invariant: a run of an already recorded merge, resolved in a later
    invocation, takes ordinal 2 instead of colliding with the first run's rows.

    Red 2026-10-13: mutation making `_Ordinals` ignore the ledger (ordinals count within
    the batch only, the original code); failed at `assert rep.rows_written == 2` (0)."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1", captured="2026-10-13T10:00:00Z")
    assert resolve(root, write=True, run=gh_merged).rows_written == 2
    pending_record(root, tmp_path, "r2", captured="2026-10-13T11:00:00Z", commit="b" * 40)
    rep = resolve(root, write=True, run=gh_merged)
    assert rep.rows_written == 2
    rows = [json.loads(x) for x in (root / "bugs.jsonl").read_text().splitlines()]
    assert {(r["bug_id"], r["run_id"]) for r in rows if r["run_id"] == "r2"} == {
        (f"widgets:{MERGE_SHA[:12]}:BUG-001~2", "r2"),
        (f"widgets:{MERGE_SHA[:12]}:DOC-001~2", "r2")}


def test_a_backfill_row_is_the_same_bug_not_a_blocker(_private_bugs_dir, tmp_path):
    """Fix round 1 (c). Invariant: a ledger row without a run_id (backfill) at ordinal 1
    does not push the forward row to ordinal 2; the forward row lands on it and is skipped.

    Red 2026-10-13: mutation letting run_id null rows block an ordinal (deleting
    `if run_id is None: continue`); failed at `assert rep.rows_written == 1` (2: BUG-001~2
    was written as a second copy of the backfilled bug)."""
    root = _private_bugs_dir
    backfilled = {"bug_id": f"widgets:{MERGE_SHA[:12]}:BUG-001", "project": "widgets",
                  "fix_commit": MERGE_SHA, "finding_id": "BUG-001", "run_id": None,
                  "ordinal": 1}
    # Corrupt lines are skipped or tolerated, never fatal (not a red-proved claim: without
    # the guards they raise TypeError or OverflowError rather than fail an assert).
    corrupt = ['{garbage', '{"project": "widgets", "fix_commit": "x", "finding_id": "O",'
               ' "run_id": [1]}', '{"project": "w", "fix_commit": "x", "finding_id": "O",'
               ' "ordinal": Infinity, "run_id": "z"}']
    (root / "bugs.jsonl").write_text(json.dumps(backfilled) + "\n" + "\n".join(corrupt) + "\n")
    pending_record(root, tmp_path, "r1")
    rep = resolve(root, write=True, run=gh_merged)
    assert rep.rows_written == 1
    ids = [json.loads(x)["bug_id"] for x in (root / "bugs.jsonl").read_text().splitlines()
           if '"bug_id"' in x]
    assert ids == [backfilled["bug_id"], f"widgets:{MERGE_SHA[:12]}:DOC-001"]
    assert not any((root / "pending").iterdir())


def test_waiting_records_stay_and_old_ones_are_stale(_private_bugs_dir, tmp_path):
    """Review Focus 2. Invariant: an unmerged record stays; only an old one is stale.

    Red 2026-10-13: mutation comparing against stale_days * 0; failed at
    `assert [s.split(":")[0] for s in rep.stale] == [f"old__{SHA[:12]}"]` (the fresh
    record was listed too)."""
    root = _private_bugs_dir
    pending_record(root, tmp_path, "old", captured="2026-09-01T00:00:00Z")
    pending_record(root, tmp_path, "new", captured="2026-10-13T00:00:00Z", commit="b" * 40)
    now = datetime(2026, 10, 14, tzinfo=timezone.utc)
    rep = resolve(root, write=True, run=gh_open, now=now)
    assert len(rep.waiting) == 2 and rep.rows_written == 0
    assert [s.split(":")[0] for s in rep.stale] == [f"old__{SHA[:12]}"]
    assert len(list((root / "pending").iterdir())) == 2


def test_gh_failure_touches_nothing_and_exits_3(_private_bugs_dir, tmp_path, capsys,
                                                monkeypatch):
    """Review Focus 3. Invariant: gh failing reports unknown, moves nothing, exits 3.

    Red 2026-10-13: mutation returning 0 when unknown is non-empty; failed at
    `assert code == 3`."""
    from cca_checks.bugs.__main__ import main
    root = _private_bugs_dir
    pending_record(root, tmp_path, "r1")
    import cca_checks.bugs.github as g
    import cca_checks.bugs.resolve as rs

    def broken(args):
        raise g.GhError("gh: not logged in")

    monkeypatch.setattr(rs, "DEFAULT_RUNNER", broken)
    code = main(["resolve", "--write", "--json"])
    assert code == 3
    assert json.loads(capsys.readouterr().out)["rows_written"] == 0
    assert len(list((root / "pending").iterdir())) == 1


def test_an_unfinished_or_hostile_meta_is_never_looked_up(_private_bugs_dir, tmp_path):
    """R1. Invariant: a folder whose meta.json is missing, unparsable, lacks a run_id or
    names a commit that is not 40 hex characters is reported incomplete, left in place,
    and never reaches gh.

    Red 2026-10-13: mutation deleting the `_check_meta` refusal and passing
    `meta.get("commit", "")` to lookup, as before the ruling; failed at `assert calls == []`."""
    root = _private_bugs_dir
    no_meta = pending_record(root, tmp_path, "crashed")
    (no_meta / "meta.json").unlink()
    garbled = pending_record(root, tmp_path, "garbled", commit="b" * 40)
    (garbled / "meta.json").write_text("{not json")
    no_run = pending_record(root, tmp_path, "norun", commit="c" * 40)
    (no_run / "meta.json").write_text(json.dumps({"run_id": "", "commit": "c" * 40}))
    hostile = pending_record(root, tmp_path, "hostile", commit="e" * 40)
    (hostile / "meta.json").write_text(json.dumps(
        {"run_id": "hostile", "commit": "../../../repos/x/y?z", "repo": "acme/widgets"}))
    calls = []

    def spy(args):
        calls.append(args)
        return gh_merged(args)

    rep = resolve(root, write=True, run=spy)
    assert calls == []
    assert sorted(s.split(":")[0] for s in rep.incomplete) == sorted(
        p.name for p in (no_meta, garbled, no_run, hostile))
    assert rep.recorded == [] and rep.rows_written == 0
    assert len(list((root / "pending").iterdir())) == 4


def test_a_malformed_run_file_stops_only_its_own_record(_private_bugs_dir, tmp_path):
    """R2. Invariant: when rows_for raises on one record, that record is reported
    incomplete and stays pending, while the good record in the same merge is written
    and moved.

    Red 2026-10-13: mutation in the `rows_for` handler appending the bad record to the
    batch with no rows (so it is moved to recorded/); failed at `assert bad.exists()`."""
    root = _private_bugs_dir
    bad = pending_record(root, tmp_path, "bad", captured="2026-10-13T09:00:00Z")
    (bad / "L4.json").write_text("[{broken")
    pending_record(root, tmp_path, "good", commit="b" * 40)
    rep = resolve(root, write=True, run=gh_merged)
    assert bad.exists()
    assert [s.split(":")[0] for s in rep.incomplete] == [bad.name]
    assert rep.recorded == [f"good__{'b' * 12}"] and rep.rows_written == 2
    ids = [json.loads(x)["bug_id"] for x in (root / "bugs.jsonl").read_text().splitlines()]
    assert f"widgets:{MERGE_SHA[:12]}:BUG-001" in ids  # the good run keeps ordinal 1


def test_incomplete_alone_exits_0(_private_bugs_dir, tmp_path, capsys):
    """R3. Invariant: an incomplete record is reported but does not make the worker retry.

    Red 2026-10-13: mutation `return 3 if report.unknown or report.incomplete else 0`;
    failed at `assert code == 0`."""
    from cca_checks.bugs.__main__ import main
    root = _private_bugs_dir
    folder = pending_record(root, tmp_path, "crashed")
    (folder / "meta.json").unlink()
    code = main(["resolve", "--json"])
    assert code == 0
    assert len(json.loads(capsys.readouterr().out)["incomplete"]) == 1


def test_dry_run_writes_and_moves_nothing(_private_bugs_dir, tmp_path):
    """Invariant: without --write a merged record is reported but nothing is written or moved.

    Red 2026-10-13: mutation dropping `write and` from the write guard; failed at
    `assert rep.recorded == [folder.name] and rep.rows_written == 0` (2 rows written)."""
    root = _private_bugs_dir
    folder = pending_record(root, tmp_path, "r1")
    rep = resolve(root, write=False, run=gh_merged)
    assert rep.recorded == [folder.name] and rep.rows_written == 0
    assert not (root / "bugs.jsonl").exists()
    assert folder.exists()
