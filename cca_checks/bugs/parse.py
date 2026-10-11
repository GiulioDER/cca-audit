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
# After '/', a bare number continues the previous prefix (CODE-001/002); after ',', only a
# full ID does, so "- FIX-1, 3 call sites" is FIX-1 followed by text.
_BULLET_RE = re.compile(
    rf"^[-*]\s+(?P<ids>{_ID}(?:\s*/\s*(?:[A-Z][A-Z0-9]*-)?\d+|\s*,\s*{_ID})*)"
    r"(?P<rest>(?:[:,\s].*)?)$"
)
_ONE_ID = re.compile(r"([A-Z][A-Z0-9]*)-(\d+)")
_SEVERITY = re.compile(r"(P[123])\b")
_SKIP_TEXT = ("deferred", "(deferred", "not fixed", "owner decision")
_SKIP_HEADING = ("deferred", "not fixed", "owner decision", "won't fix", "wontfix")
_FIXED_SEVERITIES = ("P1", "P2")
# The finding prefixes the pipeline's auditors emit, plus FIX. Anything else that looks
# like an ID (UTF-8, SHA-256) is reported by the caller, never counted.
KNOWN_PREFIXES = frozenset({
    "FIX", "SEC", "BUG", "CODE", "PERF", "DOC", "ENV", "DEP", "DEPLOY",
    "STAKES", "NUM", "DAT", "DIFF", "ARCH",
})


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
        if any(word in text.lower() for word in _SKIP_HEADING):
            return None  # "P2 (deferred to follow-up):" opens nothing
        return m.group(1)
    return None


def parse_message(body: str, rejected: list[str] | None = None) -> list[Bullet]:
    """Bullets for the fixed findings in `body`; IDs with an unknown prefix go to `rejected`."""
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
                for i in ids:
                    if i.split("-", 1)[0] in KNOWN_PREFIXES:
                        bullets.append(Bullet(i, severity, text))
                    elif rejected is not None:
                        rejected.append(i)
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
        # A '*' line whose leading ID runs straight into words ("* R2-2 forget: ...") is a
        # commit subject inside a GitHub squash body, not a finding.
        is_finding = bool(m) and (line[0] == "-" or m.group("rest")[:1] in (":", ","))
        if is_finding and section:
            flush()
            current = [expand_ids(m.group("ids")), section, [m.group("rest").lstrip(" :,\t")]]
            continue
        flush()
        severity = _heading_severity(line)
        if severity:
            section = severity
        elif line[0] == "-":
            continue  # a bullet naming no finding ID leaves the section as it is
        else:
            section = None  # prose, a skipped heading, or a squash body's "* subject" line
    flush()
    return bullets
