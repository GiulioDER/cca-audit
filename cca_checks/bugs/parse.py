"""Read a CCA fix commit message into one bullet per finding.

Deterministic on purpose: a bug description in the ledger is the commit's own text,
never a model's summary of it. A message this parser cannot read yields no bullets,
and the caller reports the commit as unparsed rather than guessing.

Shape read (Step 7 of audit-fix.md, and the variants found in real history):

    P1:                      also "P1 Critical:", "**P1:**", "## P1"
    - STAKES-001: text       also "- SEC-001 text", "- FIX-1, text"
      continuation lines     indented, joined into the summary
    - CODE-001/002: text     one bullet per ID
    Deferred (P3): ...       any other unindented line closes the section
"""
from __future__ import annotations

import re
from dataclasses import dataclass

SUBJECT_RE = re.compile(
    r"audit fixes? (?:from|for) |from CCA|CCA (?:audit|self-audit)", re.IGNORECASE
)
_ID = r"[A-Z][A-Z0-9]*-\d+"
_BULLET_RE = re.compile(
    rf"^[-*]\s+(?P<ids>{_ID}(?:\s*[/,]\s*(?:[A-Z][A-Z0-9]*-)?\d+)*)(?P<rest>(?:[:,\s].*)?)$"
)
_ONE_ID = re.compile(r"([A-Z][A-Z0-9]*)-(\d+)")
_SEVERITY = re.compile(r"(P[123])\b")
_SKIP_TEXT = ("deferred", "(deferred", "not fixed", "owner decision")
_FIXED_SEVERITIES = ("P1", "P2")


@dataclass(frozen=True)
class Bullet:
    finding_id: str
    severity: str
    summary: str


def is_cca_fix_subject(subject: str) -> bool:
    return bool(SUBJECT_RE.search(subject))


def expand_ids(ids: str) -> list[str]:
    out: list[str] = []
    prefix: str | None = None
    for part in re.split(r"\s*[/,]\s*", ids.strip()):
        m = _ONE_ID.fullmatch(part)
        if m:
            prefix = m.group(1)
            out.append(part)
        elif part.isdigit() and prefix:
            out.append(f"{prefix}-{part}")
    return out


def _heading_severity(line: str) -> str | None:
    is_markdown = line.lstrip().startswith("#")
    text = line.strip().lstrip("#").strip().strip("*").strip()
    m = _SEVERITY.match(text)
    if not m:
        return None
    if text.endswith(":") or is_markdown or text == m.group(1):
        return m.group(1)
    return None


def parse_message(body: str) -> list[Bullet]:
    bullets: list[Bullet] = []
    section: str | None = None
    current: list | None = None  # [ids, severity, parts]

    def flush() -> None:
        nonlocal current
        if current:
            ids, severity, parts = current
            text = " ".join(" ".join(parts).split())
            if (severity in _FIXED_SEVERITIES and text
                    and not text.lower().startswith(_SKIP_TEXT)):
                bullets.extend(Bullet(i, severity, text) for i in ids)
        current = None

    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if line[0] in " \t":
            if current:
                current[2].append(line.strip())
            continue
        m = _BULLET_RE.match(line)
        if m and section:
            flush()
            current = [expand_ids(m.group("ids")), section, [m.group("rest").lstrip(" :,\t")]]
            continue
        flush()
        severity = _heading_severity(line)
        if severity:
            section = severity
        elif line.startswith(("-", "*")):
            continue  # a bullet naming no finding ID leaves the section as it is
        else:
            section = None
    flush()
    return bullets
