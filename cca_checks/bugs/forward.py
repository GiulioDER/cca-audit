"""Ledger rows for one captured run whose pull request merged.

Built only from the run's own files and the merge GitHub reported, never from a commit
message, so a squash merge (whose message does not carry CCA's fix bullets) loses nothing.
The row is keyed by the merge commit, the same `bug_id` a backfill of that commit makes.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .anchors import Anchor, well_formed_file, well_formed_symbol
from .caused_by import bugs_for_commits
from .ledgers import fix_number
from .model import ABSENT, CLAIMED, EVIDENCED, NOT_REQUIRED, BugRow

_SAFE_ID = re.compile(r"[A-Za-z][A-Za-z0-9_.-]{0,63}")
_SHA256 = re.compile(r"[0-9a-f]{64}")
_SHA40 = re.compile(r"[0-9a-f]{40}")


@dataclass(frozen=True)
class Merge:
    repo: str
    sha: str
    date: str
    pr: int


def _load(record: Path, name: str, default):
    path = record / name
    if not path.exists():
        return default
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _scorecard(record: Path) -> list[dict]:
    path = record / "scorecard.jsonl"
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(row, dict):
                rows.append(row)
    return rows


def _same(a: str, b: str) -> bool:
    na, nb = fix_number(a), fix_number(b)
    return a == b or (na is not None and na == nb)


def _architect(rows: list[dict], fix_id: str) -> str:
    for row in reversed(rows):  # the last verdict decides, as in backfill
        if _same(str(row.get("fix_id") or ""), fix_id) and "architect_verdict" in row:
            verdict = str(row["architect_verdict"]).upper()
            if verdict.startswith("APPROVED"):
                return EVIDENCED
            if verdict in ("BLOCKED", "REVISE"):
                return ABSENT
    return CLAIMED


# The pipeline asks for a red proof only for a P1 fix (audit-fix Step 5.6).
_PROOF_OPTIONAL = ("P2", "P3")


def _attributed(proof: dict, finding: str, fix_id: str) -> bool:
    """Whether a proof entry names this finding or its fix, ignoring case and padding."""
    key = str(proof.get("finding", "")).strip()
    return bool(key) and (key.upper() in (finding.strip().upper(), fix_id.strip().upper())
                          or _same(key, fix_id))


def _proof(proofs: list[dict], finding: str, fix_id: str, priority: str | None,
           orphaned: bool) -> str:
    """EVIDENCED when every proof is RED; NOT_REQUIRED for a P2 or P3 with no proof entry.

    A proof that exists and is not RED stays ABSENT whatever the priority, and so does a
    P1, or a finding with no known priority, that has none. When the run holds a proof
    that no L4 entry can claim (`orphaned`), no finding is excused: it might be this one's.
    A proof file that is not a list of entries counts as unattributable, so no P2 or P3 in
    that run is excused.
    """
    verdicts = [str(p.get("verdict", "")).upper() for p in proofs
                if _attributed(p, finding, fix_id)]
    if not verdicts:
        return NOT_REQUIRED if priority in _PROOF_OPTIONAL and not orphaned else ABSENT
    return EVIDENCED if all(v == "RED" for v in verdicts) else ABSENT


def _files(value) -> list[str]:
    """L4 stores files as a list or as one comma separated string; anything else is []."""
    if isinstance(value, str):
        value = value.split(",")
    if not isinstance(value, list):
        return []
    return [f.strip() for f in value if isinstance(f, str) and f.strip()]


def _text(value) -> str | None:
    """A field from L2 or L4 kept only when it is a non-empty string."""
    return value if isinstance(value, str) and value.strip() else None


def _result(rows: list[dict], fix_id: str) -> str | None:
    """The fix's last scorecard outcome (fixed, parked or reverted), or None without one."""
    for row in reversed(rows):
        if _same(str(row.get("fix_id") or ""), fix_id) and "result" in row:
            return str(row["result"]).strip().lower()
    return None


def _not_fixed(entry: dict, fix_id: str, card: list[dict]) -> str | None:
    """Why this L4 entry is not a fix that reached the merge, or None when it is."""
    if not fix_id:
        said = " ".join(v for v in entry.values() if isinstance(v, str)).lower()
        return "parked (no fix id)" if "parked" in said else "has no fix id"
    result = _result(card, fix_id)
    if result is not None and result != "fixed":
        return f"{fix_id} scorecard result {result}"
    return None


def _anchors(facts: dict) -> list[Anchor]:
    """The finding's anchors from run_facts.json, keeping only ones capture could have written."""
    out = []
    raw = facts.get("anchors")
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        file, symbol, sha = a.get("file"), a.get("symbol"), a.get("code_sha")
        if (well_formed_file(file) and well_formed_symbol(symbol)
                and isinstance(sha, str) and _SHA256.fullmatch(sha)):
            out.append(Anchor(str(file), symbol if isinstance(symbol, str) else None, sha))
    return out


def _introduced_by(facts: dict) -> list[str]:
    raw = facts.get("introduced_by")
    if not isinstance(raw, list):
        return []
    return [s for s in raw if isinstance(s, str) and _SHA40.fullmatch(s)]


def rows_for(record: Path, merge: Merge, ledger: list[BugRow] | None = None) -> list[BugRow]:
    return [row for row, _ in rows_with_fix_ids(record, merge, ledger)]


def rows_with_fix_ids(record: Path, merge: Merge,
                      ledger: list[BugRow] | None = None) -> list[tuple[BugRow, str]]:
    """Each row with the L4 fix id it came from, which a backfill row may be keyed by."""
    return rows_and_skips(record, merge, ledger)[0]


def rows_and_skips(record: Path, merge: Merge, ledger: list[BugRow] | None = None,
                   ) -> tuple[list[tuple[BugRow, str]], list[str]]:
    """The rows, and "<finding id> <reason>" for each L4 entry that was not fixed.

    A fix parked or reverted after L4 was recorded stays in L4.json, so only an entry
    with a fix id whose scorecard result, when there is one, is `fixed` becomes a row.

    Anchors and caused_by come from the record's run_facts.json, computed at capture where
    the checkout was; the commits it names become caused_by through `ledger`'s rows of the
    same repository. A record captured before run_facts existed gives rows with neither.
    """
    raw_facts = _load(record, "run_facts.json", {})
    by_finding = raw_facts.get("findings") if isinstance(raw_facts, dict) else None
    by_finding = by_finding if isinstance(by_finding, dict) else {}
    meta = _load(record, "meta.json", {})
    l2 = {f.get("id"): f for f in _load(record, "L2.json", []) if isinstance(f, dict)}
    l25 = {v.get("id"): v.get("verdict") for v in _load(record, "L2.5.json", [])
           if isinstance(v, dict)}
    raw_proofs = _load(record, "L5.6.json", [])
    proofs = [p for p in raw_proofs if isinstance(p, dict)] if isinstance(raw_proofs, list) else []
    malformed = not isinstance(raw_proofs, list) or len(proofs) != len(raw_proofs)
    card = _scorecard(record)
    entries = [e for e in _load(record, "L4.json", []) if isinstance(e, dict)]
    claims = [(str(e.get("finding") or e.get("finding_id") or ""), str(e.get("fix_id") or ""))
              for e in entries]
    orphaned = malformed or any(not any(_attributed(p, f, x) for f, x in claims) for p in proofs)
    run_id = _text(meta.get("run_id")) if isinstance(meta, dict) else None
    out = []
    skipped = []
    for entry in entries:
        raw_fix = entry.get("fix_id")
        fix_id = raw_fix if isinstance(raw_fix, str) else ""
        finding = entry.get("finding") or entry.get("finding_id") or fix_id
        # The id becomes a memo filename, and L4 comes from untrusted places.
        if (not isinstance(finding, str) or not _SAFE_ID.fullmatch(finding)
                or not finding.strip(".")):
            continue
        reason = _not_fixed(entry, fix_id, card)
        if reason is not None:
            skipped.append(f"{finding} {reason}")
            continue
        found = l2.get(finding, {})
        severity = _text(found.get("priority"))
        facts = by_finding.get(finding)
        facts = facts if isinstance(facts, dict) else {}
        out.append((BugRow(
            project=merge.repo.split("/", 1)[1], repo=merge.repo, finding_id=finding,
            fix_commit=merge.sha, date=merge.date, files=_files(entry.get("files")),
            summary=(_text(found.get("claim")) or _text(entry.get("summary"))
                     or "(no description recorded)"),
            severity=severity, category=_text(found.get("category")),
            auditor=_text(found.get("auditor")), run_id=run_id,
            evidence={
                "confirmed_l25": EVIDENCED if l25.get(finding) == "CONFIRMED" else ABSENT,
                "fixed": EVIDENCED,
                "l6_approved": _architect(card, fix_id),
                "red_proof": _proof(proofs, finding, fix_id, severity, orphaned),
            },
            sources=[f"run:{merge.repo}:{run_id}", f"pr:{merge.repo}#{merge.pr}",
                     f"git:{merge.sha}"],
            anchors=_anchors(facts),
            caused_by=bugs_for_commits(_introduced_by(facts), merge.repo, ledger or []),
        ), fix_id))
    return out, skipped
