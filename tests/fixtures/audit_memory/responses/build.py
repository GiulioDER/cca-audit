"""Builds the saved `recall_records` answers the pipeline tests read, from the golden records.

The live server arrives with Session 6, so these stand in for it. They follow RE-call's
contract C5 as amended 2026-10-22 (RE-call PR #977, `docs/AUDIT_MEMORY_PLAN.md`): one envelope,
each record `recall_record` exactly as authored, `anchor_matches` per record anchor against the
query's anchors, `anchor_match` the best of them, and a record with no anchor matching at least
`file_only` left out. A code tenant returns one `kind: "code"` record per distinct anchor, with
`chunk_ids`, and a code anchor with a null repo never matches a repo query.

Run from the repository root to regenerate: `python tests/fixtures/audit_memory/responses/build.py`.
"""
from __future__ import annotations

import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
GOLDEN = json.loads((HERE.parent / "expected_records.json").read_text(encoding="utf-8"))
REPO = "GiulioDER/RE-call"
RANK = ("exact", "symbol_changed", "file_only", "none")


def _match(anchor: dict, query: list[dict]) -> str:
    best = "none"
    for q in query:
        if q["file"] != anchor["file"]:
            continue
        if q["symbol"] == anchor["symbol"]:
            level = "exact" if q["code_sha"] == anchor["code_sha"] else "symbol_changed"
        else:
            level = "file_only"
        if RANK.index(level) < RANK.index(best):
            best = level
    return best


def _envelope(tenant: str, records: list[dict]) -> dict:
    return {"tenant_id": tenant, "generation_id": "gen-fixture-1", "truncated": False,
            "records": records}


def _memo_record(name: str, query: list[dict], **extra) -> dict | None:
    rec = GOLDEN[name]
    matches = [{"anchor": a, "match": _match(a, query)} for a in rec.get("anchors", [])]
    best = min((m["match"] for m in matches), key=RANK.index, default="none")
    if best == "none":
        return None
    out = {"id": rec["id"], "kind": rec["kind"], "recall_record": rec,
           "name": name[:-3], "description": f"{rec['project']} {rec['kind']}",
           "source": f"memos/{name}", "chunk_id": f"{name}#0", "chunk_ids": [f"{name}#0"],
           "valid_from": rec["date"], "supersedes": None, "superseded_by": None,
           "valid_until": None, "caused": [], "anchor_matches": matches, "anchor_match": best}
    out.update(extra)
    return out


def _golden(name: str) -> dict:
    return GOLDEN[name]


def _anchor(name: str, index: int = 0) -> dict:
    return dict(_golden(name)["anchors"][index])


BUG_001 = "RE-call__0123456789ab__BUG-001.md"
SEC_003 = "RE-call__111111111111__SEC-003.md"
PERF_001 = "RE-call__222222222222__PERF-001~2.md"
BUG_002 = "RE-call__abcdef012345__BUG-002.md"
P2_4 = "RE-call__fedcba987654__P2-4.md"
FP = "RE-call__fp__run-20261021-9f8e7d__BUG-003.md"
LESSON = "RE-call__lesson__6a3ae35062b9.md"


def bug_query() -> list[dict]:
    """The changed code: Pool._lease unchanged since BUG-001's fix, frontmatter.py edited at
    module level, and a new symbol in store.py."""
    return [_anchor(BUG_001),
            {"file": "recall/frontmatter.py", "symbol": None, "code_sha": "f" * 64},
            {"file": "recall/store.py", "symbol": "Store.new_helper", "code_sha": "e" * 64}]


def bug_answer() -> dict:
    query = bug_query()
    records = [
        _memo_record(BUG_001, query, caused=[_golden(BUG_002)["id"]]),
        _memo_record(SEC_003, query),
        _memo_record(PERF_001, query),
        _memo_record(BUG_002, query),
        _memo_record(P2_4, query, superseded_by=_golden(BUG_002)["id"]),
        # Never shown to an auditor, though an anchor query in a mixed tenant returns them.
        _memo_record(FP, query),
        _memo_record(LESSON, query),
    ]
    return _envelope("cca", [r for r in records if r is not None])


def fp_query() -> list[dict]:
    """The findings' places: one finding sits at Pool._lease, unchanged since the FP judgement."""
    return [_anchor(FP, 0)]


def fp_answer() -> dict:
    return _envelope("cca", [r for r in [_memo_record(FP, fp_query())] if r is not None])


def code_answer() -> dict:
    """The code tenant's answer for the bug records' anchors: Pool._lease as fixed, a changed
    frontmatter.py, store.py's symbol as fixed, oidc.py's symbol gone; plus one chunk from a
    build with no project (null repo), which a repo query must never count."""
    def code(anchor: dict, n: int, repo: str | None = REPO) -> dict:
        rec = {"repo": repo, **anchor}
        ids = [f"code-{n}-{i}" for i in range(2)]
        return {"id": ids[0], "kind": "code", "recall_record": rec, "name": anchor["file"],
                "description": "", "source": anchor["file"], "chunk_id": ids[0],
                "chunk_ids": ids, "valid_from": None, "supersedes": None,
                "superseded_by": None, "valid_until": None, "caused": [],
                "anchor_matches": [{"anchor": rec, "match": "exact"}], "anchor_match": "exact"}
    return _envelope("re-call-code-gen", [
        code(_anchor(BUG_001), 1),
        code({"file": "recall/frontmatter.py", "symbol": None, "code_sha": "f" * 64}, 2),
        code(_anchor(BUG_002, 0), 3),
        code({"file": "recall_mcp/oidc.py", "symbol": None, "code_sha": "d" * 64}, 4),
        code({**_anchor(SEC_003)}, 5, repo=None),
    ])


def main() -> None:
    for name, value in (("bug_answer.json", bug_answer()), ("fp_answer.json", fp_answer()),
                        ("code_answer.json", code_answer())):
        (HERE / name).write_text(json.dumps(value, indent=1, ensure_ascii=False) + "\n",
                                 encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
