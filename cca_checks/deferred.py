"""Which findings a closed run deferred, and whether a second pass over them is worth it.

Pure rules over files already on disk: the summary `pipeline close` writes for every
completed run, and the L2 file that run stored. No git and no run state here: the caller
(`pipeline_state`) supplies what needs them, so these rules test without a repository.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field

SUMMARY_SCHEMA = 1
#: Fewest P3 findings, with no P2 among them, for which a second pass is recommended. A
#: starting value, not a measured one: a deferred run costs at least four agent passes
#: whatever the size of its pool.
DEFERRED_MIN_P3 = 3
_FIXED_COST = "fp-check, the scorecard, differential-review and architect-reviewer"


def runs_dir(root) -> pathlib.Path:
    return pathlib.Path(root) / ".claude" / "audits" / "runs"


def pool_ids(l25, l3) -> list[str]:
    """The pool: ids L2.5 marked DEFERRED, then ids L3 deferred, each once, in that order."""
    ids: list[str] = []
    if isinstance(l25, list):
        ids += [r["id"] for r in l25 if isinstance(r, dict)
                and r.get("verdict") == "DEFERRED" and isinstance(r.get("id"), str)]
    if isinstance(l3, dict) and isinstance(l3.get("deferred"), list):
        ids += [i for i in l3["deferred"] if isinstance(i, str)]
    return list(dict.fromkeys(ids))


def parked_count(root, run_id: str) -> int:
    """Outcome rows for `run_id` whose result is `parked`: findings left to a human at Step 6.

    Read as `utf-8-sig`, like the pipeline's own ledger reader: a ledger created by Windows
    PowerShell 5.1 starts with a BOM, which would make its first row unparseable here.
    """
    path = pathlib.Path(root) / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return 0
    count = 0
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and "verdict" not in row and \
                str(row.get("run_id")) == run_id and row.get("result") == "parked":
            count += 1
    return count


def load_summaries(root) -> tuple[list[dict], list[str]]:
    """Every readable summary, oldest first, and the paths of the unreadable ones."""
    found: list[dict] = []
    unreadable: list[str] = []
    base = runs_dir(root)
    for path in sorted(base.glob("*/summary.json")) if base.is_dir() else []:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(path.as_posix())
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("run_id"), str) \
                or not isinstance(raw.get("pool"), list):
            unreadable.append(path.as_posix())
            continue
        found.append(raw)
    found.sort(key=lambda s: (str(s.get("completed_utc", "")), s["run_id"]))
    return found, unreadable


def consumed_by(parent: str, summaries) -> str | None:
    """The completed DEFERRED run that took `parent`'s pool, if any."""
    for s in summaries:
        if s.get("mode") == "DEFERRED" and s.get("parent") == parent:
            return s["run_id"]
    return None


def why_not_offered(summary: dict, summaries) -> str | None:
    """Why this run's pool may not be taken by a deferred run; None when it may."""
    if not summary.get("pool"):
        return "nothing was deferred"
    if summary.get("no_fix"):
        return "the run was started with no-fix, which forbids editing"
    if summary.get("mode") == "DEFERRED":
        return "a deferred run never offers a third pass"
    if summary.get("mode") == "HUNT":
        return "deferred passes over hunt runs are out of scope; the pool is listed, not offered"
    child = consumed_by(summary["run_id"], summaries)
    if child:
        return f"deferred run {child} already took this pool"
    return None


def findings_for(root, summary: dict) -> list[dict]:
    """The run's stored L2 objects for exactly its pool ids, in pool order."""
    path = runs_dir(root) / summary["run_id"] / "L2.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError(
            f"run {summary['run_id']} has no readable L2 findings at {path.as_posix()} "
            f"({exc.__class__.__name__}); its run directory is incomplete, so its pool "
            "cannot be loaded"
        ) from exc
    by_id = {r.get("id"): r for r in rows if isinstance(r, dict)} if isinstance(rows, list) \
        else {}
    missing = [i for i in summary["pool"] if i not in by_id]
    if missing:
        raise LookupError(f"run {summary['run_id']}'s L2 file lacks pool ids {', '.join(missing)}")
    return [by_id[i] for i in summary["pool"]]


@dataclass
class Offer:
    run_id: str
    offered: bool
    recommendation: str | None
    reasons: list[str]
    pool: list[dict]
    counts: dict
    human_pending: int
    commit_reachable: bool
    other_pools: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def assess(summary: dict, summaries, findings: list[dict], *, commit_reachable: bool,
           unreadable=()) -> Offer:
    """Whether a second pass over this run's pool is offered, and RUN or SKIP with reasons."""
    counts = {p: sum(1 for f in findings if f.get("priority") == p) for p in ("P1", "P2", "P3")}
    pool = [{k: f.get(k) for k in ("id", "priority", "file", "line", "claim")} for f in findings]
    human = int(summary.get("human_pending", 0) or 0)
    others = [s["run_id"] for s in summaries
              if s["run_id"] != summary["run_id"] and why_not_offered(s, summaries) is None]
    notes = [] if commit_reachable else [
        f"the run's commit {summary.get('commit')} is not an ancestor of HEAD, so fp-check "
        "cannot tell STALE from FALSE_POSITIVE against it"
    ]

    def offer(offered: bool, rec: str | None, why: str) -> Offer:
        return Offer(summary["run_id"], offered, rec, [why, *notes], pool, counts, human,
                     commit_reachable, others, list(unreadable))

    why_not = why_not_offered(summary, summaries)
    if why_not:
        return offer(False, None, why_not)
    if human > 0:
        return offer(True, "SKIP",
                     f"{human} finding(s) from this run await a human decision; settle them first")
    if counts["P2"]:
        return offer(True, "RUN", f"the pool holds {counts['P2']} P2 finding(s)")
    if counts["P3"] >= DEFERRED_MIN_P3:
        return offer(True, "RUN", f"{counts['P3']} P3 findings, at or above the threshold of "
                                  f"{DEFERRED_MIN_P3}")
    return offer(True, "SKIP", f"{counts['P3']} P3 finding(s), below the threshold of "
                               f"{DEFERRED_MIN_P3}: a deferred run costs at least four agent "
                               f"passes ({_FIXED_COST}) whatever its size")
