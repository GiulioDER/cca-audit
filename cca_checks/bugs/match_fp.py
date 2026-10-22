"""The mechanical PRIOR_FP matcher: between L2 and L2.5, never in the brief.

A finding is suppressed (verdict PRIOR_FP, no fp-check) only when it sits at the place of a
`false_positive` record and ALL of these hold:

* the record is current: not superseded, not retracted, not expired;
* every one of the record's anchors is `exact`, so neither the code it was judged on nor any
  mitigation it cited has changed;
* the finding's `category` equals the record's;
* the finding is not high stakes, and its priority is not higher than the record's;
* `CCA_PRIOR_FP` is not `off`, the switch for the day the match rule itself is wrong.

Any other finding at that place is `attach`: it goes to fp-check as usual, with the record as
evidence, so the verification DEEP requires for a high-stakes P1 is never bypassed. A finding
at another place, a renamed file among them, matches nothing. Auditors never see these records.

An anchor counts as `exact` when RE-call reported it so, or, for an anchor the query did not
cover (reported `none` or `file_only`, typically a mitigation in another symbol), when the
checkout's hash of that symbol equals the record's. `symbol_changed` is never exact.

Red proof (user rule, 2026-09-08): see the docstrings of `tests/test_match_fp.py`.
"""
from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date

SWITCH = "CCA_PRIOR_FP"
SUPPRESS = "suppress"
ATTACH = "attach"
_RANK = {"P1": 1, "P2": 2, "P3": 3}


@dataclass(frozen=True)
class Match:
    finding_id: str
    record_id: str
    action: str
    # Why the finding was not suppressed; empty for a suppression.
    reasons: tuple[str, ...] = ()
    citation: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {"finding_id": self.finding_id, "record_id": self.record_id,
                "action": self.action, "reasons": list(self.reasons),
                "citation": self.citation}


def enabled() -> bool:
    return os.environ.get(SWITCH, "").strip().lower() != "off"


def _key(anchor: object) -> tuple | None:
    if not isinstance(anchor, dict):
        return None
    return anchor.get("file"), anchor.get("symbol"), anchor.get("code_sha")


def _expired(valid_until: object, today: str) -> bool:
    if valid_until is None:
        return False
    # Fail closed: a value that does not read as a date is not proof the record is current.
    return not isinstance(valid_until, str) or valid_until[:10] <= today


def _statuses(record: dict, rec: dict,
              local: Callable[[dict], str | None] | None) -> list[tuple[dict, str]]:
    reported = {_key(m["anchor"]): m["match"] for m in record.get("anchor_matches", [])}
    out = []
    for anchor in rec["anchors"]:
        status = reported.get(_key(anchor), "none")
        if status in ("none", "file_only") and local is not None \
                and local(anchor) == anchor.get("code_sha"):
            status = "exact"
        out.append((anchor, status))
    return out


def _citation(record: dict, rec: dict, envelope: dict) -> dict:
    return {"record_id": rec.get("id"), "source": record.get("source"),
            "chunk_id": record.get("chunk_id"), "generation_id": envelope.get("generation_id"),
            "run_id": rec.get("run_id"), "finding_id": rec.get("finding_id"),
            "date": rec.get("date"), "claim": rec.get("claim")}


def _fp(record: dict, repo: str | None) -> dict | None:
    rec = record["recall_record"]
    anchors = rec.get("anchors")
    if not (record.get("kind") == "false_positive" and rec.get("kind") == "false_positive"
            and rec.get("fp_reason") == "not_real" and isinstance(rec.get("id"), str)
            and isinstance(anchors, list) and anchors
            and all(isinstance(a, dict) and isinstance(a.get("code_sha"), str)
                    for a in anchors)):
        return None
    if repo is not None and str(rec.get("repo", "")).lower() != repo.lower():
        return None
    return rec


def match(findings: list[dict], response: dict, *, repo: str | None = None,
          retracted: frozenset[str] | set[str] = frozenset(),
          local: Callable[[dict], str | None] | None = None, today: str | None = None,
          on: bool | None = None) -> list[Match]:
    """One Match per (finding, false_positive record at the finding's own place).

    `findings` are L2 findings, each with an `anchor` (`{file, symbol, code_sha}` of its place
    in the checkout). `response` is a `recall_records` envelope. `retracted` holds record ids
    the local ledger retracts (RE-call learns of a retraction one refresh later). `local`
    hashes an anchor's symbol in the checkout. At most one record suppresses a finding.
    """
    from .envelope import records

    today = today or date.today().isoformat()
    on = enabled() if on is None else on
    held, envelope, _ = records(response)
    fps = [(r, rec) for r in held if (rec := _fp(r, repo)) is not None]
    retractions = {r["recall_record"].get("target_id") for r in held
                   if r["kind"] == "false_positive_retraction"}
    out: list[Match] = []
    for finding in findings:
        anchor = finding.get("anchor")
        fid = finding.get("id")
        if not isinstance(anchor, dict) or not isinstance(fid, str):
            continue
        suppressed = False
        for record, rec in sorted(fps, key=lambda item: item[1]["id"]):
            primary = rec["anchors"][0]
            if (primary.get("file"), primary.get("symbol")) != \
                    (anchor.get("file"), anchor.get("symbol")):
                continue
            reasons: list[str] = []
            if record.get("superseded_by") is not None:
                reasons.append(f"superseded by {record['superseded_by']}")
            if rec["id"] in retracted or rec["id"] in retractions:
                reasons.append("retracted")
            if _expired(record.get("valid_until"), today):
                reasons.append(f"expired {record.get('valid_until')}")
            for where, status in _statuses(record, rec, local):
                if status != "exact":
                    reasons.append(f"anchor {where.get('file')} {where.get('symbol')} "
                                   f"is {status}")
            if primary.get("code_sha") != anchor.get("code_sha"):
                reasons.append("the finding's code differs from the record's")
            if finding.get("category") != rec.get("category"):
                reasons.append(f"category {finding.get('category')!r} is not the record's "
                               f"{rec.get('category')!r}")
            if finding.get("high_stakes") is True:
                reasons.append("high stakes")
            mine = _RANK.get(str(finding.get("priority")), 0)
            theirs = _RANK.get(str(rec.get("priority")), 0)
            if not mine or not theirs or mine < theirs:
                reasons.append(f"priority {finding.get('priority')} is above the record's "
                               f"{rec.get('priority')}")
            if not on:
                reasons.append(f"{SWITCH}=off")
            if not reasons and suppressed:
                reasons.append("another record already suppresses this finding")
            action = ATTACH if reasons else SUPPRESS
            suppressed = suppressed or action == SUPPRESS
            out.append(Match(fid, rec["id"], action, tuple(reasons),
                             _citation(record, rec, envelope)))
    return out
