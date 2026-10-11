"""The shape of the work files L2, L2.5 and L3 store, checked when they are recorded.

`record` used to accept any JSON for these layers. A later run reads them back: the deferred
pass finds its findings by id in L2, and its pool in L2.5 and L3. A file that disagreed with the
counts recorded beside it would hand that run a different pool from the one the gate counted,
so here the file and the counts are made to agree.
"""

from __future__ import annotations

PRIORITIES = ("P1", "P2", "P3")
#: Verdict in an L2.5 file, and the count flag that tallies it. A finding passed on unverified
#: is written DEFERRED in the file and counted with `--unverified`.
COUNT_OF = {
    "CONFIRMED": "confirmed", "FALSE_POSITIVE": "fp", "UNCERTAIN": "uncertain",
    "DUPLICATE": "duplicate", "DEFERRED": "unverified", "STALE": "stale",
}
VERDICTS = tuple(COUNT_OF)


class ShapeError(ValueError):
    """A layer file whose shape, or whose agreement with its counts, is wrong."""


def _ids(rows: list, layer: str) -> list[str]:
    ids: list[str] = []
    for n, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ShapeError(f'{layer} entry {n} needs a non-empty string "id"')
        ids.append(row["id"])
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ShapeError(f"{layer} names {', '.join(dupes)} more than once; ids are unique")
    return ids


def l2_findings(data, unique: int) -> list:
    """The consolidated findings: one object per unique finding, each with an id and a priority."""
    if not isinstance(data, list):
        raise ShapeError("L2's file must be a JSON array of finding objects")
    _ids(data, "L2")
    for row in data:
        if row.get("priority") not in PRIORITIES:
            raise ShapeError(f'L2 finding {row["id"]} needs "priority" set to P1, P2 or P3')
    if len(data) != unique:
        raise ShapeError(f"L2's file holds {len(data)} findings but --unique is {unique}")
    return data


def l25_verdicts(data, counts: dict, l2_ids) -> dict[str, str]:
    """One verdict per L2 finding, tallying exactly to the recorded counts (id to verdict)."""
    if not isinstance(data, list):
        raise ShapeError('L2.5\'s file must be a JSON array of {"id", "verdict"} objects')
    _ids(data, "L2.5")
    known = set(l2_ids)
    tally = dict.fromkeys(VERDICTS, 0)
    verdicts: dict[str, str] = {}
    for row in data:
        verdict = row.get("verdict")
        if not isinstance(verdict, str) or verdict not in COUNT_OF:
            raise ShapeError(
                f"L2.5 entry {row['id']} has verdict {verdict!r}; expected one of "
                f"{', '.join(VERDICTS)}"
            )
        if row["id"] not in known:
            raise ShapeError(f"L2.5 gives a verdict for {row['id']}, which L2 did not record")
        tally[verdict] += 1
        verdicts[row["id"]] = verdict
    wrong = [
        f"--{COUNT_OF[v]} {counts.get(COUNT_OF[v], 0)} but {tally[v]} {v}"
        for v in VERDICTS if tally[v] != (counts.get(COUNT_OF[v]) or 0)
    ]
    if wrong:
        raise ShapeError("L2.5's file disagrees with its counts: " + "; ".join(wrong))
    return verdicts


def l3_plan(data, to_fix: int, deferred: int, l25: dict[str, str]) -> dict[str, list[str]]:
    """The fix plan: CONFIRMED ids to fix and CONFIRMED ids deferred, disjoint, matching counts."""
    if not isinstance(data, dict) or not all(
        isinstance(data.get(k), list) for k in ("to_fix", "deferred")
    ):
        raise ShapeError(
            'L3\'s file must be a JSON object with two arrays of finding ids, "to_fix" and '
            '"deferred"'
        )
    plan: dict[str, list[str]] = {}
    for key, want in (("to_fix", to_fix), ("deferred", deferred)):
        ids = data[key]
        if not all(isinstance(i, str) for i in ids):
            raise ShapeError(f"L3 {key} must hold finding id strings")
        if len(set(ids)) != len(ids):
            raise ShapeError(f"L3 {key} names an id more than once")
        if len(ids) != want:
            raise ShapeError(
                f"L3's {key} lists {len(ids)} ids but --{key.replace('_', '-')} is {want}"
            )
        plan[key] = ids
    both = sorted(set(plan["to_fix"]) & set(plan["deferred"]))
    if both:
        raise ShapeError(f"L3 both fixes and defers {', '.join(both)}")
    unconfirmed = [i for i in plan["to_fix"] + plan["deferred"] if l25.get(i) != "CONFIRMED"]
    if unconfirmed:
        raise ShapeError(f"L3 plans {', '.join(unconfirmed)}, which L2.5 did not confirm")
    return plan
