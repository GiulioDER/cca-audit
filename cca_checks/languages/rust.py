"""The Rust backend.

WHY THE CLAIM VOCABULARY IS NOT THE PYTHON ONE. `definedness`, `type` and
`nullability` are deliberately absent. In Rust those defects do not survive to
review: the code compiled, so the name resolved and the types checked, and `Option`
is not a nullable pointer you can dereference by accident. Porting them would buy
three claim types that refute essentially everything by construction -- a
deterministic layer that looks twice as broad and settles nothing new. The claim
types here are the ones where a Rust verdict carries information.

`clock_leak` is the one this module settles from the syntax tree, mirroring
`cca_checks/clock_check.py` for Python. `panic_path`, `overflow`, `error_swallow`
and `unsafe_op` are settled by clippy in `cca_checks/clippy_check.py`, which is the
analogue of pyright rather than of `ast`. `taint` goes to semgrep, as it does for
Python.

REFUTATION IS HARDER HERE THAN IN PYTHON, AND THE EXTRA BLOCKERS ARE THE REASON.
`ast` sees a whole Python module; tree-sitter sees Rust *text*. Two constructs can
hide a clock read from it entirely:

  * a glob import (`use chrono::prelude::*`) -- the analogue of Python's
    `from x import *`, which `clock_check` already treats as unrefutable;
  * ANY macro invocation in scope. tree-sitter does not expand macros, so a
    `now!()` or a `log::info!("{}", Utc::now())` inside a macro body is invisible.
    Python has no equivalent, so this blocker has no counterpart there.

Both BLOCK FALSE_POSITIVE rather than being ignored. A file must not be able to earn
a refutation carrying an authoritative `source` precisely by being hard to read.
"""

from .. import treesitter as ts
from ..claim import Claim, Verdict, make_verdict
from ..clippy_check import LINTS_BY_CLAIM, run_clippy
from ..clippy_check import verdict_for_claim as verdict_for_clippy
from ..config import CLOCK_STRONG_PARAMS, CLOCK_WEAK_PARAMS
from ..semgrep_check import verdict_for_taint
from ..toolpath import resolve_tool

LANGUAGE = "rust"

#: Node types that own a parameter list and a body. `closure_expression` is included
#: because an async block or a spawned closure is exactly where an injected clock
#: gets dropped, and omitting it would widen every span inside one to the whole
#: enclosing function -- see `treesitter.enclosing_span` on why a wide span
#: over-refutes.
FUNCTION_TYPES = frozenset({"function_item", "closure_expression"})

# Paths that read the real clock and cannot be injected. Both the fully-qualified
# spelling and the bare one are listed: a bare `Utc::now()` only compiles if a `use`
# brought `Utc` into scope, and `_Uses.resolve` maps it back to its crate path when
# that `use` is visible in this file. When it is not (a glob, or a prelude), the bare
# spelling is what remains -- and a glob already blocks refutation, so matching it
# can only raise the question, never settle it wrongly in the refuting direction.
WALL_CLOCK = frozenset({
    "std::time::SystemTime::now",
    "SystemTime::now",
    "chrono::Utc::now",
    "chrono::Local::now",
    "chrono::offset::Utc::now",
    "chrono::offset::Local::now",
    "Utc::now",
    "Local::now",
    "time::OffsetDateTime::now_utc",
    "time::OffsetDateTime::now_local",
    "OffsetDateTime::now_utc",
    "OffsetDateTime::now_local",
    "time::UtcDateTime::now",
    "UtcDateTime::now",
})

# Un-injectable but not wall-clock. A simulated-time run is just as wrong for these,
# but they are overwhelmingly used to measure durations, where they are correct. They
# raise the question; they never settle it. Mirrors clock_check.MONOTONIC.
MONOTONIC = frozenset({
    "std::time::Instant::now",
    "Instant::now",
    "tokio::time::Instant::now",
    "quanta::Instant::now",
})

# Crates whose glob import could supply an unqualified clock name. A
# `use serde::prelude::*` says nothing about time and must not cost us a refutation,
# so the blocker is scoped to time crates rather than firing on every glob.
_TIME_CRATES = frozenset({"chrono", "std", "time", "tokio", "quanta"})

#: Field accesses that indicate a clock arriving by a route this file cannot see
#: (`self.clock.now()`, `self.now`). A signal, never a proof -- whether the field is
#: dead cannot be decided from one method body. Mirrors clock_check.CLOCK_ATTRS.
CLOCK_FIELDS = frozenset({"clock", "now", "time_provider", "time_source", "time_fn"})


class _Uses:
    """File-local `use` declarations, so a renamed import cannot hide a clock read.

    `use chrono::Utc;`                  -> {"Utc": "chrono::Utc"}
    `use chrono::Utc as U;`             -> {"U": "chrono::Utc"}
    `use std::time::{SystemTime, Instant};`
        -> {"SystemTime": "std::time::SystemTime", "Instant": "std::time::Instant"}
    `use chrono::prelude::*;`           -> star_import = True

    The Rust counterpart of `clock_check._Aliases`, and it exists for the same
    reason: without it, `use chrono::Utc as U; U::now()` reads as an unknown call and
    a real leak escapes.
    """

    def __init__(self, root):
        self.map: dict[str, str] = {}
        self.star_import = False
        for node in ts.walk(root):
            if node.type == "use_declaration":
                self._read(node)

    def _read(self, decl) -> None:
        for child in decl.children:
            self._read_clause(child, prefix="")

    def _read_clause(self, node, prefix: str) -> None:
        kind = node.type
        if kind == "use_wildcard":
            # `use chrono::prelude::*` -- this file's namespace is no longer knowable
            # from this file, exactly as a Python `from x import *`.
            path = _text(node).rstrip("*").rstrip(":")
            if _crate_of(_join(prefix, path)) in _TIME_CRATES:
                self.star_import = True
        elif kind == "use_as_clause":
            # children: <path> `as` <alias>
            parts = [c for c in node.children if c.type != "as"]
            if len(parts) >= 2:
                self.map[_text(parts[-1])] = _join(prefix, _text(parts[0]))
        elif kind == "scoped_use_list":
            # `std::time::{SystemTime, Instant}` -- a path, then a brace list.
            path = next((c for c in node.children if c.type in
                         ("identifier", "scoped_identifier", "crate", "self", "super")), None)
            group = next((c for c in node.children if c.type == "use_list"), None)
            if group is not None:
                inner = _join(prefix, _text(path) if path is not None else "")
                for item in group.named_children:
                    self._read_clause(item, prefix=inner)
        elif kind == "use_list":
            for item in node.named_children:
                self._read_clause(item, prefix=prefix)
        elif kind in ("identifier", "scoped_identifier"):
            full = _join(prefix, _text(node))
            self.map[full.rsplit("::", 1)[-1]] = full

    def resolve(self, path: str) -> str:
        """Map a source spelling onto its crate path when this file's `use`s allow it.

        An unmapped head is returned unchanged, which is what lets the bare spellings
        in WALL_CLOCK match a name that arrived through a glob or a prelude.
        """
        head, sep, rest = path.partition("::")
        base = self.map.get(head)
        if base is None:
            return path
        return f"{base}{sep}{rest}" if sep else base


def _text(node) -> str:
    return node.text.decode("utf-8", "replace") if node is not None else ""


def _join(prefix: str, path: str) -> str:
    return f"{prefix}::{path}" if prefix and path else (prefix or path)


def _crate_of(path: str) -> str:
    return path.partition("::")[0]


def _callee_path(call) -> str | None:
    """The dotted callee of a `call_expression`, or None when it is not a path.

    `Utc::now()` -> "Utc::now"; `f().g()` -> None, because a call's result is not a
    name we can resolve statically.
    """
    fn = call.child_by_field_name("function")
    if fn is None or fn.type not in ("identifier", "scoped_identifier"):
        return None
    return _text(fn)


def _param_names(fn) -> list[str]:
    """Every binding introduced by the function's parameter list.

    Reads the identifier out of each `parameter`'s pattern, so `now: DateTime<Utc>`
    yields "now" and a destructured or `_`-named parameter yields nothing.
    """
    params = fn.child_by_field_name("parameters")
    if params is None:
        return []
    names = []
    for param in params.named_children:
        pattern = param.child_by_field_name("pattern") or param
        for node in ts.walk(pattern):
            if node.type == "identifier":
                names.append(_text(node))
                break
    return names


def _is_name_read(node) -> bool:
    """True when an `identifier` node is a NAME being read, not part of a path.

    THE DEFECT THIS EXISTS TO PREVENT, measured on `tests/fixtures/rust/src/clock.rs`.
    A naive "every identifier in the body" walk counts the trailing segment of a
    scoped path as a name read. `fn settle(now: i64) { L::now() }` then reports the
    parameter `now` as referenced -- by the METHOD NAME of the very clock call that
    makes it a leak. `dead` comes back empty and CONFIRMED becomes unreachable for
    the single most common Rust shape: a `now`/`clock` parameter beside a `::now()`
    call. It fails safe (a missed confirmation, never a false one), which is exactly
    why it would have gone unnoticed.

    So: in `a::b::c` only the head `a` is a name read, and in `x.field` only `x` is.
    """
    parent = node.parent
    if parent is None:
        return True
    if parent.type == "scoped_identifier":
        # `Utc::now` -- only `Utc` resolves against the local scope; `now` is a path
        # segment. `children[0]` is the head because the grammar puts the path first.
        return bool(parent.children) and parent.children[0].id == node.id
    if parent.type == "field_expression":
        # `stamp.timestamp()` -- `stamp` is read, `timestamp` is a member name.
        field = parent.child_by_field_name("field")
        return field is None or field.id != node.id
    return True


def _referenced_names(fn) -> set[str]:
    """Every bare name READ inside the function BODY.

    Scoped to the body, not the whole node: the parameter list itself contains each
    parameter's identifier, so walking the function would find every parameter
    "referenced" by its own declaration and no parameter could ever be dead.

    Assignment is deliberately not distinguished from reading here, unlike the Python
    checker: Rust has no `now = now.unwrap_or_else(Utc::now)` rebinding idiom that
    discards an injected value while appearing to use it, and `let now = ...` in the
    body shadows rather than overwrites -- which the parameter's own deadness already
    describes.
    """
    body = fn.child_by_field_name("body")
    if body is None:
        return set()
    return {_text(n) for n in ts.walk(body)
            if n.type == "identifier" and _is_name_read(n)}


def _fmt(pairs, file: str) -> str:
    import os
    return ", ".join(f"{name} @ {os.path.basename(file)}:{line}" for line, name in pairs)


def _scan(scope, uses: _Uses) -> dict:
    """Clock evidence inside one scope.

    `handles` matters as much as `wall`: a clock function named but never called
    (`.unwrap_or_else(Utc::now)`) runs at a time this checker cannot determine, so it
    must block refutation rather than count as absence. Mirrors `clock_check._scan`.
    """
    found = {"wall": [], "monotonic": [], "handles": [], "fields": [], "macros": []}
    called: set[int] = set()

    for node in ts.walk(scope):
        if node.type == "call_expression":
            fn = node.child_by_field_name("function")
            if fn is not None:
                called.add(fn.id)
            path = _callee_path(node)
            if path:
                full = uses.resolve(path)
                if full in WALL_CLOCK:
                    found["wall"].append((node.start_point[0] + 1, path))
                elif full in MONOTONIC:
                    found["monotonic"].append((node.start_point[0] + 1, path))
        elif node.type == "scoped_identifier" and node.id not in called:
            # A clock function referenced without being called. Its invocation time
            # is not decidable here, so it blocks refutation without confirming.
            path = _text(node)
            if uses.resolve(path) in (WALL_CLOCK | MONOTONIC):
                found["handles"].append((node.start_point[0] + 1, path))
        elif node.type == "field_expression":
            value = node.child_by_field_name("value")
            field = node.child_by_field_name("field")
            if value is not None and field is not None and _text(value) == "self" \
                    and _text(field) in CLOCK_FIELDS:
                found["fields"].append((node.start_point[0] + 1, f"self.{_text(field)}"))
        elif node.type == "macro_invocation":
            # tree-sitter does not expand macros. A clock read inside one is
            # invisible, so its mere presence makes absence unprovable.
            macro = node.child_by_field_name("macro")
            found["macros"].append((node.start_point[0] + 1, f"{_text(macro)}!"))
    return found


def _enclosing_function(root, line: int):
    """Innermost function-like node containing `line`, or None at item level."""
    best = None
    for node in ts.walk(root):
        if node.type not in FUNCTION_TYPES:
            continue
        lo, hi = ts.line_span(node)
        if lo <= line <= hi and (best is None or lo > ts.line_span(best)[0]):
            best = node
    return best


def verdict_for_clock_leak(claim: Claim) -> Verdict:
    """Settle a Rust clock-leak claim from the syntax tree.

    Verdict asymmetry, identical in shape to the Python checker's:

      CONFIRMED       a STRONG injected-time parameter is declared, never referenced
                      in the body, and the body reads the wall clock. Both halves are
                      proven from the tree.
      FALSE_POSITIVE  no clock read of ANY kind in the enclosing scope, and nothing
                      in the file could have hidden one.
      UNCERTAIN       everything else -- most importantly co-occurrence, where the
                      injected clock IS used and the wall clock is read too. That is
                      correct code (stamping a log line with the real time while the
                      logic runs on `as_of`), not a defect.
    """
    import os
    fid, file = claim.finding_id, claim.file

    def uncertain(why: str) -> Verdict:
        return make_verdict(fid, "UNCERTAIN", why, "ast")

    try:
        root = ts.parse(file, LANGUAGE)
    except ts.GrammarUnavailable as exc:
        return uncertain(f"{exc}; escalated")
    except OSError as exc:
        return uncertain(f"could not read {file} ({exc.__class__.__name__}); escalated")

    uses = _Uses(root)
    scope = _enclosing_function(root, claim.line)
    where = f"{os.path.basename(file)}:{claim.line}"
    target = scope if scope is not None else root
    found = _scan(target, uses)

    wall, mono = found["wall"], found["monotonic"]
    handles, fields, macros = found["handles"], found["fields"], found["macros"]

    # --- refutation, held to "an absence the file could not have hidden" -------
    if not (wall or mono or handles):
        blockers = []
        if ts.has_error(root):
            blockers.append("the file did not parse cleanly, so the scan is partial")
        if uses.star_import:
            blockers.append("the file has a glob `use` from a time crate: an "
                            "unqualified now() could resolve through it")
        if macros:
            blockers.append(f"macro invocation(s) in scope ({_fmt(macros, file)}) "
                            f"whose expansion is not visible to a syntactic parser")
        if blockers:
            return uncertain(
                f"no clock read found in the scope @ {where}, but absence is not "
                f"provable here: " + "; ".join(blockers) + "; escalated")
        return make_verdict(
            fid, "FALSE_POSITIVE",
            f"tree-sitter: no wall-clock or monotonic read in the enclosing scope "
            f"@ {where}; the finding's premise does not hold", "ast")

    if not wall:
        detail = []
        if mono:
            detail.append(f"monotonic source(s): {_fmt(mono, file)} -- un-injectable, "
                          f"but normally correct for measuring durations")
        if handles:
            detail.append(f"clock function referenced without being called: "
                          f"{_fmt(handles, file)} -- invocation time is not decidable here")
        return uncertain(f"tree-sitter: no wall-clock CALL in the scope @ {where}; " +
                         "; ".join(detail) + "; adjudicate")

    # --- a wall-clock read is present; is injected time also in play? ---------
    ev_clock = f"wall-clock read: {_fmt(wall, file)}"
    if scope is None:
        return uncertain(f"tree-sitter: {ev_clock}, at item level -- there is no "
                         f"parameter list to carry injected time, so this cannot be "
                         f"settled here; adjudicate")

    params = _param_names(scope)
    strong = [p for p in params if p in CLOCK_STRONG_PARAMS]
    weak = [p for p in params if p in CLOCK_WEAK_PARAMS]

    if not (strong or weak or fields):
        return uncertain(
            f"tree-sitter: {ev_clock}, but the enclosing function takes no recognised "
            f"injected-time parameter. The clock may still arrive via self, a static, "
            f"or a caller -- absence of a parameter is not absence of injected time; "
            f"adjudicate")

    used = _referenced_names(scope)
    dead = [p for p in strong if p not in used]

    if dead:
        return make_verdict(
            fid, "CONFIRMED",
            f"tree-sitter: parameter {', '.join(repr(p) for p in dead)} is declared "
            f"and NEVER referenced in the enclosing function (line "
            f"{ts.line_span(scope)[0]}), while the body performs a {ev_clock}. The "
            f"caller is offered injectable time and does not get it.", "ast")

    live = [p for p in strong if p in used]
    parts = []
    if live:
        parts.append(f"injected-time parameter {', '.join(repr(p) for p in live)} IS "
                     f"used in the scope")
    if weak:
        parts.append(f"weak-signal parameter(s) {', '.join(repr(p) for p in weak)}")
    if fields:
        parts.append(f"clock field(s) {_fmt(fields, file)}")
    return uncertain(
        f"tree-sitter: {ev_clock} co-occurs with injected time ({'; '.join(parts)}). "
        f"Co-occurrence is not a defect -- stamping an audit log with the real time "
        f"while the logic runs on the injected clock is correct; adjudicate")


class RustBackend:
    name = "rust"
    extensions = frozenset({".rs"})
    #: `taint` rides semgrep's Rust rules; the four clippy claim types come from
    #: LINTS_BY_CLAIM so the backend and the checker cannot disagree about which
    #: claims are settleable -- a hand-copied list here would let the CLI accept a
    #: claim type the checker has no lints for, which renders no verdict at all.
    claim_types = frozenset({"clock_leak", "taint"}) | frozenset(LINTS_BY_CLAIM)

    def enclosing_span(self, path: str, line_1based: int) -> tuple[int, int]:
        return ts.enclosing_span(path, line_1based, LANGUAGE, FUNCTION_TYPES)

    def semgrep_catalog(self, kind: str) -> str:
        return f"rust_{kind}.yaml"

    def unavailable_claim_types(self) -> dict[str, str]:
        """Claim types whose tool is missing on THIS machine, with the reason.

        The Rust backend has three independent tool dependencies, and they fail
        independently -- which is the whole reason the parser and the diagnostics are
        separate layers. A machine with the grammar but no cargo still settles
        `clock_leak` perfectly well, and reporting that precisely is what stops an
        agent either skipping a check that works or running one that cannot.
        """
        out = {}
        try:
            ts._parser(LANGUAGE)
        except ts.GrammarUnavailable as exc:
            out["clock_leak"] = str(exc)
        if resolve_tool("cargo") is None:
            for claim_type in LINTS_BY_CLAIM:
                out[claim_type] = "cargo is not on PATH, so clippy cannot run"
        if resolve_tool("semgrep") is None:
            out["taint"] = "semgrep is not on PATH"
        return out

    def settle(self, claim: Claim) -> Verdict:
        if claim.claim_type == "clock_leak":
            return verdict_for_clock_leak(claim)
        if claim.claim_type == "taint":
            return verdict_for_taint(claim)
        return verdict_for_clippy_claim(claim)


def verdict_for_clippy_claim(claim: Claim) -> Verdict:
    """Run clippy for the claim's lint set and settle against its diagnostics.

    The enclosing span comes from tree-sitter rather than from clippy, and the split
    matters: the span is what a refutation is scoped to, and computing it from the
    parser means a claim whose span we cannot determine escalates for that reason
    alone -- rather than silently widening to the whole crate, which would make a
    refutation rest on silence across code the claim never referred to.
    """
    lints = LINTS_BY_CLAIM[claim.claim_type]
    diagnostics = run_clippy(claim.file, lints)
    try:
        span = ts.enclosing_span(claim.file, claim.line, LANGUAGE, FUNCTION_TYPES)
    except Exception:
        span = None
    return verdict_for_clippy(claim, diagnostics, lints, span)
