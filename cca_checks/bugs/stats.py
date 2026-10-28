"""Counts over the bug ledger: what CCA fixed, where, and of which kind.

`python -m cca_checks bugs stats [--repo OWNER/NAME] [--top N] [--json]`

Read-only. It counts the rows `records.read_ledger` returns, so a recovered anchor counts the
same as an authored one and a superseded bug row counts once, as its successor. Only bug rows
are fixes; false positives and lessons are reported beside them, never added to them.

Two choices decide what a count means, and both are stated in the output:

* **Domain** comes from the finding id's prefix (`SEC-002` is SEC), because the `auditor` field
  is missing on 420 of 922 rows (2026-10-28) and is spelled two ways when present. Legacy ids
  that are only a severity and a number (`P1-03`, `F-12`) carry no domain and count as
  `unattributed`.
* **Files** are the bug's anchors, not the fix commit's files. A fix commit lists every file it
  touched, so CHANGELOG.md and README.md led the per-file counts; an anchor names the place the
  finding was. Rows without an anchor are counted as such, not spread over their commit's files.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from .model import BugRow
from .records import FalsePositiveRow, LessonRow, read_ledger
from .store import bugs_dir

SEVERITIES = ("P1", "P2", "P3")
UNKNOWN = "unknown"
#: Finding-id prefixes that name an auditor domain. Anything else is legacy numbering.
DOMAINS = ("BUG", "SEC", "CODE", "DOC", "ENV", "PERF", "NUM", "DAT", "STAKES", "DEP", "DEPLOY")
_PREFIX = re.compile(r"^([A-Za-z]+)-")


def severity(row: BugRow) -> str:
    """P1, P2 or P3, else `unknown`: the bug rows hold nothing else (2026-10-28)."""
    value = (row.severity or "").strip().upper()
    return value if value in SEVERITIES else UNKNOWN


def domain(row: BugRow) -> str:
    match = _PREFIX.match(row.finding_id or "")
    prefix = match.group(1).upper() if match else ""
    return prefix if prefix in DOMAINS else "unattributed"


def _bugs(rows: dict) -> list[BugRow]:
    """Bug rows that no later bug row supersedes."""
    bugs = [r for r in rows.values() if isinstance(r, BugRow)]
    replaced = {r.supersedes for r in bugs if r.supersedes}
    return [r for r in bugs if r.bug_id not in replaced]


def summarize(rows: dict, *, repo: str | None = None, top: int = 15) -> dict:
    """The counts, as one JSON-ready object."""
    bugs = _bugs(rows)
    others = [r for r in rows.values() if not isinstance(r, BugRow)]
    if repo is not None:
        bugs = [r for r in bugs if (r.repo or "").lower() == repo.lower()]
        others = [r for r in others if (getattr(r, "repo", "") or "").lower() == repo.lower()]

    by_project: dict[str, Counter] = {}
    for r in bugs:
        by_project.setdefault(r.project, Counter())[severity(r)] += 1
    files: Counter = Counter()
    for r in bugs:
        for place in {(r.project, a.file) for a in r.anchors}:
            files[place] += 1
    by_domain: dict[str, Counter] = {}
    for r in bugs:
        by_domain.setdefault(domain(r), Counter())[severity(r)] += 1

    def table(counter_by: dict[str, Counter]) -> dict[str, dict[str, int]]:
        return {k: {s: v[s] for s in (*SEVERITIES, UNKNOWN)} | {"total": sum(v.values())}
                for k, v in sorted(counter_by.items(), key=lambda kv: -sum(kv[1].values()))}

    return {
        "repo": repo,
        "fixes": len(bugs),
        "severity": {s: sum(1 for r in bugs if severity(r) == s) for s in (*SEVERITIES, UNKNOWN)},
        "by_project": table(by_project),
        "by_domain": table(by_domain),
        "by_category": dict(Counter(r.category or "(none)" for r in bugs).most_common(top)),
        "uncategorized": sum(1 for r in bugs if not r.category),
        "by_month": dict(sorted(Counter(r.date[:7] for r in bugs if r.date).items())),
        "tier": dict(Counter(r.tier for r in bugs)),
        "anchored": sum(1 for r in bugs if r.anchors),
        "top_files": [{"project": p, "file": f, "fixes": n}
                      for (p, f), n in files.most_common(top)],
        # A fix whose lines an earlier CCA fix had written: the ledger's regression signal.
        "regressions": sum(1 for r in bugs if r.caused_by),
        "false_positives": sum(1 for r in others if isinstance(r, FalsePositiveRow)),
        "lessons": sum(1 for r in others if isinstance(r, LessonRow)),
    }


def render(s: dict) -> str:
    sev = (*SEVERITIES, UNKNOWN)
    lines = [f"CCA fixes in the ledger{' for ' + s['repo'] if s['repo'] else ''}: {s['fixes']}",
             "  " + "  ".join(f"{k} {s['severity'][k]}" for k in sev), ""]

    def block(title: str, table: dict) -> None:
        lines.append(f"{title:<22}" + "".join(f"{k:>9}" for k in (*sev, "total")))
        for name, row in table.items():
            lines.append(f"  {name:<20}" + "".join(f"{row[k]:>9}" for k in (*sev, "total")))
        lines.append("")

    block("By project", s["by_project"])
    block("By domain (id prefix)", s["by_domain"])
    lines.append(f"By category ({s['uncategorized']} of {s['fixes']} have none)")
    lines += [f"  {n:>5}  {c}" for c, n in s["by_category"].items()]
    lines += ["", f"Anchored: {s['anchored']} of {s['fixes']}; most-fixed anchored files:"]
    lines += [f"  {t['fixes']:>5}  {t['project']}  {t['file']}" for t in s["top_files"]]
    lines += ["", "By month: " + ", ".join(f"{m} {n}" for m, n in s["by_month"].items()),
              "Evidence tier: " + ", ".join(f"{k} {v}" for k, v in sorted(s["tier"].items())),
              f"Regressions (a fix rewrote an earlier CCA fix's lines): {s['regressions']}",
              f"Beside the fixes: {s['false_positives']} false positives, {s['lessons']} lessons"]
    return "\n".join(lines)


def run(*, repo: str | None, top: int, as_json: bool, root: Path | None = None) -> int:
    rows, errors = read_ledger(root or bugs_dir())
    summary = summarize(rows, repo=repo, top=top)
    summary["unreadable_lines"] = len(errors)
    print(json.dumps(summary, indent=2) if as_json else render(summary)
          + (f"\n{len(errors)} unreadable ledger lines were skipped" if errors else ""))
    return 0
