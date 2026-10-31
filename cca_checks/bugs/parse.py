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
                             Except in a second pass: when the subject says "second pass"
                             or "deferred", a "P3:" / "P3 fixes:" heading is a SEVERITY
                             section, because the deferred pass verified its P3s.
    FIXED:                   a SEVERITY section at the one level the subject names
                             ("deferred P3 hygiene fixes"); a TOPIC section otherwise.
    P1 — title               underlined with ----- or =====: a SEVERITY section, and when
                             no bullet follows, the title itself is the one fix.
    anything else            a TOPIC section ("Shipped library and install:"): only
                             bullets naming known finding IDs count, with no severity.

    P2: a (DAT-001); b       an inline severity line, continuation lines indented: one
                             fix per ';' part, named by its parenthesised IDs.

    - STAKES-001: text       also "- SEC-001 text", "- FIX-1, text"
      continuation lines     indented, joined into the summary
    - CODE-001/002: text     one bullet per ID

Measured on 2026-10-11 against real history: most fix commits outside the projects CCA
was developed in list their fixes without finding IDs, or under topic headings, so a
parser that needed an ID under a P1/P2 heading read 18% of them. Measured 2026-10-29: 36
CCA fix commits on the default branches still read as unparsed, none with a ledger row;
the second-pass, FIXED, inline and underlined forms above cover the structured ones. The
rest are prose without per-fix structure, and stay unparsed rather than guessed.
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
                 "skipped", "false positive", "rejected", "dropped", "parked", "uncertain",
                 "declined")
# Not "stale": at the start of a bullet it names what was fixed ("Stale comments: ..."),
# which a measured real commit does; only as a heading does it mean "nothing to do".
_SKIP_TEXT = ("deferred", "(deferred", "not fixed", "owner decision", "won't fix", "wontfix",
              "skipped", "false positive", "rejected", "dropped", "parked", "uncertain",
              "declined")
# The finding prefixes the pipeline's auditors emit, plus FIX. Anything else that looks
# like an ID (UTF-8, SHA-256, F-01) never becomes a finding ID.
KNOWN_PREFIXES = frozenset({
    "FIX", "SEC", "BUG", "CODE", "PERF", "DOC", "ENV", "DEP", "DEPLOY",
    "STAKES", "NUM", "DAT", "DIFF", "ARCH",
    # sentiment-agent's financial-correctness auditor (FIN-001 ...), in its fix commits.
    "FIN",
})
_SECOND_PASS = re.compile(r"second pass|deferred", re.IGNORECASE)
_UNDERLINE = re.compile(r"^[-=]{3,}\s*$")
_TITLED = re.compile(r"^(P[123])\s+[\u2014\u2013-]+\s+(\S.*)$")
_INLINE = re.compile(r"^(?:\*\*)?(P[12])(?:\*\*)?:\s+(\S.*)$")
_PAREN_IDS = re.compile(rf"\s*\(({_ID}(?:\s*/\s*(?:[A-Z][A-Z0-9]*-)?\d+)*)\)")
_FIXED_HEADINGS = ("fixed", "fixes", "fixed in this pass")
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


def _heading_kind(line: str, second_pass: bool = False,
                  subject_level: str | None = None) -> tuple[str, str | None]:
    is_markdown = line.lstrip().startswith("#")
    text = line.strip().lstrip("#").strip().strip("*").strip()
    if any(word in text.lower() for word in _SKIP_HEADING):
        return SKIP, None  # "P2 (deferred to follow-up):", "Stale (already fixed):"
    if text.lower().rstrip(":").strip() in _FIXED_HEADINGS and subject_level:
        return SEVERITY, subject_level
    m = _SEVERITY.match(text)
    if m and (text.endswith(":") or is_markdown or text == m.group(1)
              or (m.group(1) == "P3" and text.lower().rstrip(":") == "p3 fixes")):
        level = m.group(1)
        if level == "P3":
            return (SEVERITY, "P3") if second_pass else (SKIP, None)
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
    subject = next((ln.strip() for ln in body.splitlines() if ln.strip()), "")
    second_pass = bool(_SECOND_PASS.search(subject))
    levels = set(_SEVERITY.findall(subject))
    subject_level = levels.pop() if len(levels) == 1 else None
    # An underlined "P1 — title" section whose title is its only fix if no bullet follows.
    titled: dict | None = None

    def stand_in(sev: str, text: str) -> None:
        stand_ins[sev] += 1
        bullets.append(Bullet(f"{sev}-{stand_ins[sev]}", sev, text))

    def close_titled() -> None:
        nonlocal titled
        if titled is not None and not titled["bullets"]:
            stand_in(titled["level"], titled["title"])
        titled = None

    def flush() -> None:
        nonlocal current
        if current is None:
            return
        rest = _flat([current["rest"], *current["more"]])
        full = _flat([current["full"], *current["more"]])
        ids, sect, sev = current["ids"], current["kind"], current["level"]
        current = None
        if sect == "inline":
            for part in (x.strip() for x in full.split(";")):
                if not part or part.lower().startswith(_SKIP_TEXT):
                    continue
                named = _known_ids([i for m in _PAREN_IDS.finditer(part)
                                    for i in expand_ids(m.group(1))])
                text = _PAREN_IDS.sub("", part).strip()
                if named:
                    bullets.extend(Bullet(i, sev, text) for i in named)
                else:
                    stand_in(sev, part)
            return
        if sect == SKIP or not full or rest.lower().startswith(_SKIP_TEXT):
            return
        if titled is not None:
            titled["bullets"] += 1
        known = _known_ids(ids)
        if sect == SEVERITY:
            if known:
                bullets.extend(Bullet(i, sev, rest) for i in known)
            else:
                stand_in(sev, full)
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
        if _UNDERLINE.match(line):
            continue  # the rule under a heading, not a heading of its own
        close_titled()
        heading = _TITLED.match(line)
        inline = _INLINE.match(line)
        if heading and heading.group(1) != "P3":
            kind, level = SEVERITY, heading.group(1)
            titled = {"level": level, "title": heading.group(2).strip(), "bullets": 0}
            continue
        if inline:
            current = {"ids": [], "rest": inline.group(2), "full": inline.group(2),
                       "more": [], "kind": "inline", "level": inline.group(1)}
            kind, level = TOPIC, None
            continue
        kind, level = _heading_kind(line, second_pass, subject_level)
    flush()
    close_titled()
    return bullets
