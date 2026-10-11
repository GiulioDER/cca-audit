"""Read a CCA fix commit message into one bullet per finding.

Deterministic on purpose: a bug description in the ledger is the commit's own text,
never a model's summary of it. A message this parser cannot read yields no bullets,
and the caller reports the commit as unparsed rather than guessing.

Every unindented line that is not a bullet is a heading, of one of three kinds:

    P1: / P2:                a SEVERITY section. Every top-level "- " bullet is a fix:
                             also "P1 Critical:", "**P1:**", "## P1". A bullet naming
                             known finding IDs is those findings; any other bullet
                             ("- B", "- F-01: ...", "- gate.py: ...") is one fix under a
                             stand-in ID, P1-1, P1-2, with its whole text as summary.
    Deferred (P3): ...       a SKIP section: P3 (never verified at L2.5), and any heading
                             saying deferred, stale, skipped, not fixed, and the like.
    anything else            a TOPIC section ("Shipped library and install:"): only
                             bullets naming known finding IDs count, with no severity.

    - STAKES-001: text       also "- SEC-001 text", "- FIX-1, text"
      continuation lines     indented, joined into the summary
    - CODE-001/002: text     one bullet per ID

Measured on 2026-10-11 against real history: most fix commits outside the projects CCA
was developed in list their fixes without finding IDs, or under topic headings, so a
parser that needed an ID under a P1/P2 heading read 18% of them.
"""
from __future__ import annotations

import re
from collections import Counter
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
_DASH = re.compile(r"^-\s+(\S.*)$")
_ONE_ID = re.compile(r"([A-Z][A-Z0-9]*)-(\d+)")
_SEVERITY = re.compile(r"(P[123])\b")
_P3 = re.compile(r"\bP3\b")
_SKIP_HEADING = ("deferred", "not fixed", "owner decision", "won't fix", "wontfix", "stale",
                 "skipped", "false positive", "rejected", "dropped", "parked", "uncertain")
_SKIP_TEXT = ("deferred", "(deferred", "not fixed", "owner decision", "won't fix", "wontfix",
              "stale", "skipped", "false positive", "rejected", "dropped", "parked",
              "uncertain")
# The finding prefixes the pipeline's auditors emit, plus FIX. Anything else that looks
# like an ID (UTF-8, SHA-256, F-01) never becomes a finding ID.
KNOWN_PREFIXES = frozenset({
    "FIX", "SEC", "BUG", "CODE", "PERF", "DOC", "ENV", "DEP", "DEPLOY",
    "STAKES", "NUM", "DAT", "DIFF", "ARCH",
})
SEVERITY, TOPIC, SKIP = "severity", "topic", "skip"


@dataclass(frozen=True)
class Bullet:
    finding_id: str
    severity: str | None
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


def _known_ids(ids: list[str]) -> list[str]:
    return [i for i in ids if i.split("-", 1)[0] in KNOWN_PREFIXES]


def _heading_kind(line: str) -> tuple[str, str | None]:
    is_markdown = line.lstrip().startswith("#")
    text = line.strip().lstrip("#").strip().strip("*").strip()
    if any(word in text.lower() for word in _SKIP_HEADING):
        return SKIP, None  # "P2 (deferred to follow-up):", "Stale (already fixed):"
    m = _SEVERITY.match(text)
    if m and (text.endswith(":") or is_markdown or text == m.group(1)):
        level = m.group(1)
        if level == "P3":
            return SKIP, None
        if _P3.search(text):
            return TOPIC, None  # "P2/P3:" mixes a counted level with an uncounted one
        return SEVERITY, level
    return TOPIC, None


def _flat(parts: list[str]) -> str:
    return " ".join(" ".join(parts).split())


def parse_message(body: str, rejected: list[str] | None = None) -> list[Bullet]:
    """Bullets for the fixed findings in `body`.

    IDs with an unknown prefix in a topic section go to `rejected`: there they are the only
    sign the bullet was a finding, so the caller reports them instead of counting them.
    """
    bullets: list[Bullet] = []
    kind, level = TOPIC, None
    stand_ins: Counter = Counter()
    current: dict | None = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        rest = _flat([current["rest"], *current["more"]])
        full = _flat([current["full"], *current["more"]])
        ids, sect, sev = current["ids"], current["kind"], current["level"]
        current = None
        if sect == SKIP or not full or rest.lower().startswith(_SKIP_TEXT):
            return
        known = _known_ids(ids)
        if sect == SEVERITY:
            if known:
                bullets.extend(Bullet(i, sev, rest) for i in known)
            else:
                stand_ins[sev] += 1
                bullets.append(Bullet(f"{sev}-{stand_ins[sev]}", sev, full))
        elif known:
            bullets.extend(Bullet(i, None, rest) for i in known)
        elif rejected is not None:
            rejected.extend(ids)

    for raw in body.splitlines():
        line = raw.rstrip()
        if not line.strip():
            flush()
            continue
        if line[0] in " \t":
            if current is not None:
                current["more"].append(line.strip())
            continue
        m = _BULLET_RE.match(line)
        dash = _DASH.match(line)
        # A '*' line whose leading ID runs straight into words ("* R2-2 forget: ...") is a
        # commit subject inside a GitHub squash body, not a finding; so is any '*' line
        # without an ID. Only '- ' bullets, or '* ID:' bullets, are findings.
        star_finding = m is not None and line[0] == "*" and m.group("rest")[:1] in (":", ",")
        if dash or star_finding:
            flush()
            full = dash.group(1) if dash else line[1:].strip()
            current = {
                "ids": expand_ids(m.group("ids")) if m else [],
                "rest": m.group("rest").lstrip(" :,\t") if m else full,
                "full": full, "more": [], "kind": kind, "level": level,
            }
            continue
        flush()
        kind, level = _heading_kind(line)
    flush()
    return bullets
