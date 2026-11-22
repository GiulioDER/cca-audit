"""Bookkeeping for the fix audit layer L5.4: the repair journal, the side ledger of verdicts,
and the `fixaudit` commands that check the fixes and record L5.4.

Both files are append-only, like the disposition ledger: a line is never rewritten. The journal
lives in the run's directory and is what makes the 3-cycle repair cap a rule the code enforces,
not one the agent is trusted to count. The side ledger is a separate file because fix audit
verdicts reuse the words CONFIRMED and FALSE_POSITIVE, and the disposition ledger's readers
would tally them as L2.5 dispositions.
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import pathlib
import sys

from . import fixaudit, layer_outputs, pipeline_state

JOURNAL = "fixaudit-journal.jsonl"
LEDGER = pathlib.Path(".claude/audits/FIXAUDIT_LEDGER.jsonl")
#: A finding that stays CONFIRMED through this many repair cycles is parked, not retried.
CAP = 3


def _append(path: pathlib.Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    torn = False
    if path.exists() and path.stat().st_size:
        with path.open("rb") as handle:
            handle.seek(-1, os.SEEK_END)
            torn = handle.read(1) != b"\n"
    with path.open("ab", buffering=0) as handle:
        if torn:  # a crash left half a line; the next row must not join it
            handle.write(b"\n")
        for row in rows:
            handle.write((json.dumps(row, sort_keys=True) + "\n").encode("utf-8"))


def _journal_path(root: pathlib.Path, run_id: str) -> pathlib.Path:
    return root / ".claude" / "audits" / "runs" / run_id / JOURNAL


def _rows(root: pathlib.Path, run_id: str) -> list[dict]:
    """The journal rows. A malformed line raises: skipping it could hide a CONFIRMED cycle and
    let a finding past the cap, so the run stops and the line is repaired by hand."""
    path = _journal_path(root, run_id)
    if not path.exists():
        return []
    rows = []
    for number, line in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path} line {number} is not valid JSON ({exc.msg})") from exc
    return rows


def journal(root: pathlib.Path, run_id: str, *, finding: str, fix_id: str, pass_no: int,
            verdict: str, reason: str) -> None:
    """Append one fix audit outcome for `finding`, the original finding the fix answered."""
    _append(_journal_path(root, run_id), [{
        "finding": finding, "fix_id": fix_id, "pass": pass_no, "verdict": verdict,
        "reason": reason,
    }])


def cycles(root: pathlib.Path, run_id: str) -> dict[str, int]:
    """Per original finding, how many fix audit passes confirmed a defect in its fix."""
    counts: dict[str, int] = {}
    for row in _rows(root, run_id):
        if row.get("verdict") == "CONFIRMED":
            counts[row["finding"]] = counts.get(row["finding"], 0) + 1
    return counts


def parked(root: pathlib.Path, run_id: str) -> set[str]:
    """Findings that reached the cap, plus any the journal parks outright."""
    held = {finding for finding, n in cycles(root, run_id).items() if n >= CAP}
    return held | {row["finding"] for row in _rows(root, run_id) if row.get("verdict") == "PARKED"}


def next_pass(root: pathlib.Path, run_id: str) -> int:
    """One past the highest pass journaled, so numbering survives a pause and resume."""
    return 1 + max((row["pass"] for row in _rows(root, run_id)), default=0)


def append_verdicts(root: pathlib.Path, run_id: str, audit: dict) -> int:
    """Append one side ledger row per finding of a checked L5.4 file; return how many."""
    ts = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    rows = [{**finding, "run_id": run_id, "layer": "L5.4", "pass": audit["pass"], "ts": ts}
            for finding in audit["findings"]]
    _append(root / LEDGER, rows)
    return len(rows)


AUDIT = pathlib.Path(".claude/audits/out/L5.4.json")
SIBLINGS = pathlib.Path(".claude/audits/out/L5.4-siblings.json")
#: A confirmed fix audit finding of these priorities blocks L5.4; a P3 goes to the deferred pool.
BLOCKING = ("P1", "P2")
JOURNAL_VERDICTS = (*layer_outputs.FA_VERDICTS, "PARKED")
#: The kept passes whose verdicts reached the side ledger, so a retry appends none twice.
APPENDED = "fixaudit-appended.json"


def _dispositions(root: pathlib.Path) -> tuple[dict, list[str]]:
    """The sibling dispositions file (none written means none given), or why it is unusable."""
    path = root / SIBLINGS
    if not path.exists():
        return {}, []
    try:
        data = json.loads(path.read_bytes())
    except (OSError, ValueError) as exc:
        return {}, [f"{SIBLINGS.as_posix()} is not readable JSON ({exc})"]
    if not isinstance(data, dict) or not all(isinstance(v, dict) for v in data.values()):
        return {}, [f'{SIBLINGS.as_posix()} must map each hit id to {{"disposition", "reason"}}']
    return data, []


def _audit_file(root: pathlib.Path, rel: pathlib.Path) -> tuple[dict | None, str | None]:
    """The checked verdict file, or why it cannot be used."""
    path = root / rel
    if not pipeline_state.core._inside(str(path), str(root / pipeline_state.core.EXCLUDED)):
        # Anywhere else it is part of the tree, so writing it would change what was audited.
        return None, f"the L5.4 verdict file {rel} must lie under .claude/audits/"
    if not path.is_file():
        return None, (f"no L5.4 verdict file at {rel}: on STANDARD and DEEP the fix hunks are "
                      "audited, verified and written there before L5.4 is recorded")
    try:
        return layer_outputs.l54_audit(json.loads(path.read_bytes())), None
    except (OSError, ValueError) as exc:  # ShapeError is a ValueError
        return None, f"the L5.4 verdict file {rel} is unusable: {exc}"


def _pass_reasons(data: dict, passes: dict[int, dict], report: dict, fixes: list[dict]
                  ) -> tuple[list[str], bool]:
    """Why the verdict file cannot stand as the run's latest pass, and whether it is a new pass
    (False for a pass already kept and sent again, which was checked when it was kept)."""
    k, latest = data["pass"], max(passes, default=0)
    reasons = []
    if data["audited_tree"] != report["tree"]:
        reasons.append(f"L5.4 pass {k} audited tree {data['audited_tree']}, not the current "
                       f"{report['tree']}: audit the report `fixaudit check` writes at this tree")
    if passes.get(k) == data:
        return reasons, False
    if k != latest + 1:
        # A kept pass is never replaced: the next pass's delta and RESOLVED rows point at it.
        return reasons + [f"L5.4 is numbered pass {k}, but the last kept pass is {latest}: "
                          f"number it {latest + 1}"], True
    missing = [h["id"] for h in report["delta_hunks"] if h["id"] not in data["hunks"]]
    if missing:
        reasons.append(f"L5.4 pass {k} does not cover the changed hunks {', '.join(missing)}")
    pairs = {(f["fix_id"], f["finding"]) for f in fixes}
    for row in data["findings"]:
        if (row["fix_id"], row["finding"]) not in pairs:
            # The cap and the revert are found through this pair; a wrong one escapes both.
            reasons.append(f"{row['id']} names fix {row['fix_id']} for finding {row['finding']}, "
                           "which L4 does not have")
        if row["verdict"] != "RESOLVED":
            continue
        p = int(row["resolves"].split("-")[1])
        target = next((f for f in passes.get(p, {}).get("findings", [])
                       if f["id"] == row["resolves"]), None)
        if target is None or target["verdict"] != "CONFIRMED":
            reasons.append(f"{row['id']} resolves {row['resolves']}, which no kept pass confirmed")
        elif target["fix_id"] != row["fix_id"]:
            reasons.append(f"{row['id']} is about {row['fix_id']}, but {row['resolves']} is about "
                           f"{target['fix_id']}")
        elif passes[p]["audited_tree"] == data["audited_tree"]:
            reasons.append(f"{row['id']} resolves {row['resolves']} at the tree pass {p} "
                           "audited: nothing was edited since, so nothing was fixed")
    return reasons, True


def _reverted(row: dict, fixes: list[dict], hunks: list[dict]) -> bool:
    """True when the fix that introduced `row` is out of the tree: no file that fix alone lists
    differs from prefix_tree, and no current fix hunk holds the finding's line."""
    changed = {h["path"] for h in hunks} | {h["old_path"] for h in hunks if h["old_path"]}
    others = {p for f in fixes if f["fix_id"] != row["fix_id"] for p in f["files"]}
    alone = {p for f in fixes if f["fix_id"] == row["fix_id"] for p in f["files"]} - others
    if alone & changed:
        return False
    return not any(h["path"] == row["file"]
                   and h["new_start"] <= row["line"] < h["new_start"] + h["new_len"]
                   for h in hunks)


def _unresolved(passes: dict[int, dict], held: set[str], fixes: list[dict],
                hunks: list[dict]) -> list[str]:
    """A confirmed P1 or P2 blocks until a later pass resolves it, or until its finding is
    parked at the cap and its fix reverted: no run commits a fix with a known bug in it."""
    cleared = {row["resolves"] for p in passes.values() for row in p["findings"]
               if row["verdict"] == "RESOLVED"}
    out = []
    for k, p in passes.items():
        for row in p["findings"]:
            if row["verdict"] != "CONFIRMED" or row["priority"] not in BLOCKING \
                    or row["id"] in cleared:
                continue
            what = f"{row['id']} ({row['priority']}, {row['fix_id']})"
            if row["finding"] not in held:
                out.append(f"{what} was confirmed in pass {k} and is unresolved: fix it and have "
                           "the next pass re-verify it RESOLVED")
            elif not _reverted(row, fixes, hunks):
                out.append(f"{what}: {row['finding']} is parked, but {row['fix_id']} is still in "
                           "the tree; revert that fix whole")
    return out


def _evaluate(root: pathlib.Path, state, audit: pathlib.Path | None
              ) -> tuple[list[str], dict | None, dict | None]:
    """(reasons, a new pass to keep, the report rebuilt at the current tree).

    The checks run on a fresh report, so an L4 field added after `fixaudit check` counts; the
    stored one only has to be current. A new pass is kept when every check holds except the
    resolution of its confirmed findings, because the next pass is measured from it.
    """
    tree = pipeline_state.current_tree(state, root)
    if tree is None:
        return ["the working tree could not be fingerprinted (git missing from PATH, or timed "
                "out), so L5.4 could never be shown current"], None, None
    reasons = []
    stored = _journal_path(root, state.run_id).with_name(fixaudit.REPORT)
    if not stored.is_file():
        reasons.append(f"no {fixaudit.REPORT} in this run: "
                       "run `python -m cca_checks fixaudit check`")
    elif json.loads(stored.read_bytes()).get("tree") != tree:
        reasons.append(f"{fixaudit.REPORT} is stale: the tree changed since `fixaudit check`; "
                       "run it again and audit that report")
    report = fixaudit.build_report(root, state, tree)
    fixes = fixaudit.stored_fixes(root, state)
    dispositions, bad = _dispositions(root)
    reasons += bad
    for hit in report["siblings"]["hits"]:
        reasons += [f"{hit['fix_id']}: sibling {hit['path']}:{hit['line']}: {why}"
                    for why in fixaudit.check_dispositions([hit], dispositions, root)]
    reasons += fixaudit.failure_test_reasons(report["mechanisms"], fixes, root)
    reasons += [f"{s['fix_id']}: {s['lines']:g} changed lines, over {fixaudit.SIZE_THRESHOLD}; "
                "give size_reason in L4"
                for s in report["sizes"] if s["over"] and not s["has_reason"]]
    if state.tier == "FAST":
        if audit is not None:
            reasons.append("a FAST run has no auditor pass: record L5.4 without --audit")
        return reasons, None, report
    passes = fixaudit.kept_passes(root, state.run_id)
    held = parked(root, state.run_id)
    data, why = _audit_file(root, audit or AUDIT)
    if data is None:
        return (reasons + [str(why)] + _unresolved(passes, held, fixes, report["hunks"]),
                None, report)
    file_reasons, new = _pass_reasons(data, passes, report, fixes)
    keep = data if new and not file_reasons and not reasons else None
    considered = passes if file_reasons else {**passes, data["pass"]: data}
    return (reasons + file_reasons + _unresolved(considered, held, fixes, report["hunks"]),
            keep, report)


def record_reasons(root: pathlib.Path, state, audit: pathlib.Path | None = None) -> list[str]:
    """Why L5.4 cannot be recorded now; empty when it can. Writes nothing."""
    return _evaluate(root, state, audit)[0]


def record(root: pathlib.Path, audit: pathlib.Path | None = None) -> list[str]:
    """Record L5.4 through the gate when every condition holds; otherwise return why not.

    Writes in an order a retry survives: a new pass's file is kept first (even when its
    confirmed findings then refuse the record), and the side ledger gets each kept pass's rows
    once, only after the gate recorded L5.4, so a refused record appends nothing.
    """
    state = pipeline_state.load(root)
    if state is None:
        return ["no CCA run is open"]
    reasons, keep, report = _evaluate(root, state, audit)
    if keep is not None:
        pipeline_state._store_run_file(state, fixaudit.PASS_FILE.format(keep["pass"]), keep, root)
    if reasons:
        return reasons
    assert report is not None  # a None report always comes with a reason
    if (root / SIBLINGS).exists():
        pipeline_state._store_run_file(state, SIBLINGS.name, _dispositions(root)[0], root)
    passes = fixaudit.kept_passes(root, state.run_id)
    # The run's own record of what reached the shared ledger: reading that ledger instead
    # would let one bad line from any run block this one.
    appended_path = _journal_path(root, state.run_id).with_name(APPENDED)
    appended = json.loads(appended_path.read_bytes()) if appended_path.exists() else []
    checks = (f"{len(report['siblings']['hits'])} sibling hits disposed, "
              f"{len(report['mechanisms'])} mechanism flags")
    detail = f"FAST checks: {checks}" if state.tier == "FAST" else \
        f"pass {max(passes)}: {checks}, {sum(len(p['findings']) for p in passes.values())} findings"
    pipeline_state.record("L5.4", detail=detail, source="tool", root=root, tree=report["tree"])
    for k, p in passes.items():
        if k in appended:
            continue
        append_verdicts(root, state.run_id, p)
        appended.append(k)
        pipeline_state._store_run_file(state, APPENDED, appended, root)
    return []


def _journal_cli(root: pathlib.Path, state, a: argparse.Namespace) -> list[str]:
    """Validate one journal row at the boundary (`journal` checks nothing), then append it."""
    reasons = []
    if a.verdict not in JOURNAL_VERDICTS:
        reasons.append(f"--verdict {a.verdict!r} is not one of {', '.join(JOURNAL_VERDICTS)}")
    kept = fixaudit.kept_passes(root, state.run_id)
    if a.pass_no not in kept:
        reasons.append(f"--pass {a.pass_no} is not a kept pass (kept: "
                       f"{', '.join(map(str, kept)) or 'none'}); `fixaudit record` keeps a pass")
    if not any(f["finding"] == a.finding and f["fix_id"] == a.fix_id
               for f in fixaudit.stored_fixes(root, state)):
        # The cap counts per original finding: a misspelt one would never reach it.
        reasons.append(f"L4 has no fix {a.fix_id} for finding {a.finding}")
    # The cap counts CONFIRMED rows, so each must be a real cycle: one per finding and pass,
    # and only for a pass that confirmed a blocking defect in that fix.
    if any(r["finding"] == a.finding and r["pass"] == a.pass_no for r in _rows(root, state.run_id)):
        reasons.append(f"the journal already has a row for {a.finding} in pass {a.pass_no}")
    if a.verdict == "CONFIRMED" and not any(
            f["verdict"] == "CONFIRMED" and f["priority"] in BLOCKING and f["fix_id"] == a.fix_id
            for f in kept.get(a.pass_no, {}).get("findings", [])):
        reasons.append(f"pass {a.pass_no} confirmed no P1 or P2 in {a.fix_id}")
    if a.verdict == "PARKED" and cycles(root, state.run_id).get(a.finding, 0) < CAP:
        reasons.append(f"{a.finding} is parked only after {CAP} confirmed cycles; it has "
                       f"{cycles(root, state.run_id).get(a.finding, 0)}")
    if not a.reason.strip():
        reasons.append("--reason is empty")
    if reasons:
        return reasons
    journal(root, state.run_id, finding=a.finding, fix_id=a.fix_id, pass_no=a.pass_no,
            verdict=a.verdict, reason=a.reason)
    return []


def _run(a: argparse.Namespace, root: pathlib.Path) -> list[str]:
    if a.cmd == "record":
        reasons = record(root, None if a.audit is None else pathlib.Path(a.audit))
        if not reasons:
            print("L5.4 recorded")
        return reasons
    state = pipeline_state.load(root)
    if state is None:
        return ["no CCA run is open"]
    if a.cmd == "journal":
        return _journal_cli(root, state, a)
    report = fixaudit.check_report(root, state)
    print(json.dumps({
        "report": f".claude/audits/runs/{state.run_id}/{fixaudit.REPORT}", "tree": report["tree"],
        "sibling_hits": len(report["siblings"]["hits"]),
        "too_broad": len(report["siblings"]["too_broad"]),
        "mechanisms": len(report["mechanisms"]),
        "oversized": [s["fix_id"] for s in report["sizes"] if s["over"]],
        "auditors": report["auditors"], "delta_hunks": [h["id"] for h in report["delta_hunks"]],
    }))
    return []


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(prog="python -m cca_checks fixaudit",
                                description="the fix audit layer, L5.4")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="write the deterministic report, runs/<run_id>/fixaudit.json")
    j = sub.add_parser("journal", help="append one fix audit cycle to the run's journal")
    j.add_argument("--finding", required=True, help="the original finding the fix answered")
    j.add_argument("--fix-id", required=True)
    j.add_argument("--pass", dest="pass_no", type=int, required=True)
    j.add_argument("--verdict", required=True)
    j.add_argument("--reason", required=True)
    r = sub.add_parser("record", help="record L5.4 once every condition holds")
    r.add_argument("--audit", help=f"the verdict file, STANDARD and DEEP only (default {AUDIT})")
    for s in (c, j, r):
        s.add_argument("--root", default=".")
    a = p.parse_args(argv)
    try:
        reasons = _run(a, pathlib.Path(a.root))
    except ValueError as exc:
        # Every refusal below (FixAuditRefused, ShapeError, RecordRefused) is a ValueError,
        # and a refusal is a message and exit 2, not a traceback.
        reasons = [str(exc)]
    for reason in reasons:
        print(f"refused: {reason}", file=sys.stderr)
    return 2 if reasons else 0
