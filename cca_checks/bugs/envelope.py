"""Reading a saved `recall_records` answer (RE-call contract C5, as amended 2026-10-22).

The envelope is `{"tenant_id", "generation_id", "truncated", "records": [...]}`. Each record
carries `id`, `kind`, `recall_record` exactly as authored, `name`, `description`, `source`,
`chunk_id`, `chunk_ids`, `valid_from`, `supersedes`, `superseded_by`, `valid_until`, `caused`,
`anchor_matches` (`[{"anchor", "match"}]`) and `anchor_match`, the best of them. A code tenant
returns one `kind: "code"` record per distinct anchor, its `recall_record` the `code_anchor`.
RE-call #1011 (draft) adds `expired`, a boolean it computes; answers saved before that carry none.

A saved answer is untrusted JSON: an MCP client may wrap it as `{"result": "<json>"}`, and any
record whose fields have the wrong type is dropped here rather than read by a caller.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone

MATCHES = ("exact", "symbol_changed", "file_only", "none")


def _unwrap(value: object) -> object:
    if isinstance(value, dict) and set(value) == {"result"} and isinstance(value["result"], str):
        try:
            return json.loads(value["result"].removeprefix("\N{ZERO WIDTH NO-BREAK SPACE}"))
        except (ValueError, RecursionError):
            return value
    return value


def utc_today() -> str:
    """Today's date in UTC (YYYY-MM-DD): RE-call's validity windows are in UTC, not this host's
    time zone, so a host whose local date is already tomorrow must not expire a record early.
    """
    return datetime.now(timezone.utc).date().isoformat()


def expired(valid_until: object, today: str) -> bool:
    """Whether a record whose `valid_until` is this has lapsed by `today` (YYYY-MM-DD, in UTC).

    RE-call's rule (`recall.frontmatter.validity_bounds`): a date-only `valid_until` holds
    through 23:59:59.999999 UTC of that day, so the record is current on the day itself and
    lapsed from the next. None never lapses. Fail closed: a value that does not read as a date
    is not proof the record is current. The brief and the PRIOR_FP matcher share this one rule.

    The value is read exactly as RE-call reads it (`recall.frontmatter._parse_date`, the format
    `%Y-%m-%d` and nothing else), so a time, an offset or trailing text is malformed there and
    lapsed here too. Reading only its first ten characters kept such a record current on the
    day RE-call already reported it unreadable.
    """
    if valid_until is None:
        return False
    try:
        # Parsed, not compared as text: a malformed value would sort after every real date.
        until = datetime.strptime(str(valid_until), "%Y-%m-%d").date()
        return until < date.fromisoformat(today[:10])
    except ValueError:
        return True


def record_expired(record: dict, today: str) -> bool:
    """Whether a `recall_records` entry has lapsed by `today`: RE-call's own `expired` when the
    entry carries one (RE-call #1011 (draft) computes it at its `now` by the rule above), otherwise
    `expired` on the entry's `valid_until`.
    """
    flag = record.get("expired")
    if isinstance(flag, bool):
        return flag
    return expired(record.get("valid_until"), today)


def _record(raw: object) -> dict | None:
    if not isinstance(raw, dict):
        return None
    if not (isinstance(raw.get("kind"), str) and isinstance(raw.get("recall_record"), dict)):
        return None
    matches = raw.get("anchor_matches", [])
    if not isinstance(matches, list) or not all(
            isinstance(m, dict) and isinstance(m.get("anchor"), dict)
            and m.get("match") in MATCHES for m in matches):
        return None
    if raw.get("anchor_match", "none") not in MATCHES:
        return None
    for key in ("superseded_by", "valid_until", "source", "chunk_id", "id"):
        if raw.get(key) is not None and not isinstance(raw.get(key), str):
            return None
    if raw.get("expired") is not None and not isinstance(raw.get("expired"), bool):
        return None
    caused = raw.get("caused", [])
    if not isinstance(caused, list):
        return None
    return raw


def records(value: object) -> tuple[list[dict], dict, str | None]:
    """(the well-formed records, the envelope, why the envelope is unusable or None).

    A truncated answer is unusable: a record cut off the end could be the one that matters.
    """
    value = _unwrap(value)
    if not isinstance(value, dict) or not isinstance(value.get("records"), list):
        return [], {}, "not a recall_records envelope (no records array)"
    if value.get("truncated") is True:
        return [], value, "the answer is truncated"
    kept = [r for r in (_record(raw) for raw in value["records"]) if r is not None]
    return kept, value, None


def load(path) -> tuple[list[dict], dict, str | None]:
    """`records` over a saved file; an unreadable file is a reason, never an exception."""
    try:
        with open(path, "rb") as fh:
            value = json.loads(fh.read())
    except FileNotFoundError:
        return [], {}, "no saved recall_records answer"
    except (OSError, ValueError, RecursionError) as exc:
        return [], {}, f"{path}: {type(exc).__name__}"
    return records(value)
