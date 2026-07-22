"""Settle a `clock_leak` claim: code on injected time that reads the wall clock anyway.

The defect class. A system that must be able to simulate time -- a backtest, a
replay harness, an expiry/supersession rule, a scheduled gate -- threads a clock
through its call graph as a `now=` / `as_of=` / `clock=` parameter. Somewhere in
that graph one function calls `datetime.now()` instead. Both halves read as
correct in isolation, so review passes; the code only misbehaves once simulated
and real time diverge, which is why this survives to production and then surfaces
as "it worked in the test, it's wrong at scale".

Why AST rather than an LLM auditor. The question "does this scope read the wall
clock" is decidable from the syntax tree, including through import aliases, and a
decidable question should not be answered by a model that can be talked out of it.

VERDICT ASYMMETRY -- deliberately unlike pyright's, and for a reason worth stating.

  CONFIRMED       a STRONG injected-time parameter is present, never referenced
                  anywhere in the scope, and the scope reads the wall clock. Both
                  halves are proven from the tree: the parameter is dead, and the
                  clock read is real.
  UNCERTAIN       everything else that is not provable absence -- most importantly
                  the case where the injected clock IS also used. Co-occurrence is
                  NOT a defect: stamping an audit log with the real time while
                  business logic runs on `as_of` is correct code, and common.
  FALSE_POSITIVE  only when no clock read of any kind occurs in the enclosing
                  scope, and only when the file's names resolve well enough for
                  that absence to mean something.

The dead-parameter discriminator is the whole design. A CONFIRMED verdict feeds an
auto-fix path, so "a clock parameter and a datetime.now() are both present" -- the
obvious rule, and the one this checker deliberately does not use -- is far too weak
to license rewriting money-path code. An unused parameter next to a wall-clock read
is a different claim: the caller was promised injectable time and does not have it.

Refutation is held to the same standard the taint checker uses: only an absence the
audited file cannot have hidden may refute. A `from x import *`, or a reference to a
clock function that never appears in call position, blocks FALSE_POSITIVE rather
than being ignored -- otherwise a file could earn a refutation carrying an
authoritative `source` precisely by being hard to read.
"""

import ast
import os

from .claim import Claim, Verdict, make_verdict
from .config import CLOCK_STRONG_PARAMS, CLOCK_WEAK_PARAMS

# Fully-qualified callables that read the real clock and cannot be injected.
# Matched after resolving file-local import aliases, so `dt.datetime.utcnow()`
# and `from datetime import datetime; datetime.utcnow()` both land here.
WALL_CLOCK = frozenset({
    "datetime.datetime.now",
    "datetime.datetime.utcnow",
    "datetime.datetime.today",
    "datetime.date.today",
    "time.time",
    "time.time_ns",
    "time.localtime",
    "time.gmtime",
    "pandas.Timestamp.now",
    "pandas.Timestamp.utcnow",
    "pandas.Timestamp.today",
})

# Un-injectable but not wall-clock: monotonic sources. A simulated-time run is just
# as wrong for these, but they are overwhelmingly used to measure durations, where
# they are correct. They raise the question; they never settle it.
MONOTONIC = frozenset({
    "time.monotonic",
    "time.monotonic_ns",
    "time.perf_counter",
    "time.perf_counter_ns",
    "time.process_time",
    "time.process_time_ns",
    "time.thread_time",
})

# Attribute names that indicate a clock arriving by a route this file cannot fully
# see (`self.clock()`, `self._now`). A signal, never a proof: whether the attribute
# is dead cannot be decided from one method body.
CLOCK_ATTRS = frozenset({"clock", "_clock", "now", "_now", "time_provider", "time_func"})

_TOP_LEVEL = {"datetime", "time", "pandas"}

# Both function flavours, everywhere one is accepted. Spelled once so the checker
# cannot grow a path that handles `def` but silently skips `async def` -- an async
# handler is exactly where an injected clock gets dropped.
FuncDef = ast.FunctionDef | ast.AsyncFunctionDef


def _dotted(node: ast.AST) -> str | None:
    """Dotted source spelling of a Name/Attribute chain, or None if it is not one.

    `a.b.c` -> "a.b.c"; `f().b` -> None, because a call's result is not a name we
    can resolve statically.
    """
    parts = []
    cur = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    if not isinstance(cur, ast.Name):
        return None
    parts.append(cur.id)
    return ".".join(reversed(parts))


class _Aliases:
    """File-local import aliases, so a renamed import cannot hide a clock read.

    `import datetime as dt` -> {"dt": "datetime"}
    `from datetime import datetime as d` -> {"d": "datetime.datetime"}
    """

    def __init__(self, tree: ast.AST):
        self.map: dict[str, str] = {}
        self.star_import = False
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.map[a.asname or a.name.split(".")[0]] = a.name
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                for a in node.names:
                    if a.name == "*":
                        # The file's namespace is now unknowable from the file.
                        if mod.split(".")[0] in _TOP_LEVEL:
                            self.star_import = True
                        continue
                    self.map[a.asname or a.name] = f"{mod}.{a.name}" if mod else a.name

    def resolve(self, dotted: str) -> str:
        head, _, rest = dotted.partition(".")
        base = self.map.get(head)
        if base is None:
            return dotted
        return f"{base}.{rest}" if rest else base


def _enclosing_function(tree: ast.AST, line: int) -> FuncDef | None:
    """Innermost function containing `line`, or None when the line is module level."""
    best: FuncDef | None = None
    for node in ast.walk(tree):
        if isinstance(node, FuncDef):
            end = getattr(node, "end_lineno", None) or node.lineno
            if node.lineno <= line <= end and (best is None or node.lineno > best.lineno):
                best = node
    return best


def _param_names(fn: FuncDef) -> list[str]:
    a = fn.args
    everything = [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg]
    return [p.arg for p in everything if p is not None]


def _bound_locally(scope: ast.AST) -> set[str]:
    """Names the scope binds itself, which therefore are NOT the imported module.

    `def f(now, time): return time.time()` does not read the stdlib clock -- `time`
    is a parameter. Without this, an import alias would be matched against a name the
    function had rebound, and the checker would CONFIRM a defect in correct code.
    Since CONFIRMED feeds an auto-fix path, that is the most expensive mistake this
    module can make.

    An `import` inside the scope is deliberately NOT counted: that binding IS the
    real module, and `_Aliases` (which walks the whole tree, nested functions
    included) has already recorded it.
    """
    bound: set[str] = set()
    if isinstance(scope, FuncDef):
        bound.update(_param_names(scope))
    for node in ast.walk(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
    return bound


def _scan(scope: ast.AST, aliases: _Aliases, shadowed: frozenset[str] = frozenset()) -> dict:
    """Clock evidence inside one scope.

    `handles` matters as much as `wall`: a clock function named but never called
    (`default_factory=datetime.now`) is reachable at a time this checker cannot
    determine, so it must block refutation rather than be counted as absence.
    """
    found = {"wall": [], "monotonic": [], "handles": [], "attrs": [], "shadowed": []}
    called: set[int] = set()

    def head_is_shadowed(dotted: str) -> bool:
        return dotted.partition(".")[0] in shadowed

    for node in ast.walk(scope):
        if isinstance(node, ast.Call):
            called.add(id(node.func))
            dotted = _dotted(node.func)
            if dotted:
                full = aliases.resolve(dotted)
                if full in (WALL_CLOCK | MONOTONIC) and head_is_shadowed(dotted):
                    # Spells like a clock read, but the scope rebound the name. We
                    # cannot say what it is -- so it neither confirms nor refutes.
                    found["shadowed"].append((node.lineno, dotted))
                elif full in WALL_CLOCK:
                    found["wall"].append((node.lineno, dotted))
                elif full in MONOTONIC:
                    found["monotonic"].append((node.lineno, dotted))
        if isinstance(node, ast.Attribute):
            # self.clock() / self._now -- a clock arriving by another route.
            if isinstance(node.value, ast.Name) and node.value.id == "self" \
                    and node.attr in CLOCK_ATTRS:
                found["attrs"].append((node.lineno, f"self.{node.attr}"))
            if id(node) not in called:
                dotted = _dotted(node)
                if dotted and aliases.resolve(dotted) in (WALL_CLOCK | MONOTONIC):
                    bucket = "shadowed" if head_is_shadowed(dotted) else "handles"
                    found[bucket].append((node.lineno, dotted))
    return found


def _referenced_names(scope: ast.AST, skip_args: bool = True) -> set[str]:
    """Every bare name LOADED in the scope, excluding the parameter list itself.

    A parameter is "used" when its name is read somewhere in the body. Writing to it
    (`now = now or datetime.now()`) does not count as use for our purposes -- that
    idiom is precisely the leak, since the injected value is discarded.
    """
    names = set()
    for node in ast.walk(scope):
        if isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
            names.add(node.id)
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            names.add(node.value.id)
    if skip_args and isinstance(scope, FuncDef):
        # Defaults are evaluated in the ENCLOSING scope, but ast.walk sees them
        # under the function node; a name appearing only in a default is not a use.
        for d in [*scope.args.defaults, *[k for k in scope.args.kw_defaults if k]]:
            for sub in ast.walk(d):
                if isinstance(sub, ast.Name):
                    names.discard(sub.id)
    return names


def _fmt(pairs, file: str) -> str:
    return ", ".join(f"{name} @ {os.path.basename(file)}:{ln}" for ln, name in pairs)


def verdict_for_clock_leak(claim: Claim) -> Verdict:
    """Settle a clock-leak claim from the syntax tree. See module docstring."""
    fid, file = claim.finding_id, claim.file

    def uncertain(why: str) -> Verdict:
        return make_verdict(fid, "UNCERTAIN", why, "ast")

    if not file.endswith(".py"):
        return uncertain(f"{os.path.basename(file)} is not Python; clock-leak analysis "
                         f"does not cover it; escalated")
    try:
        with open(file, encoding="utf-8-sig") as fh:
            tree = ast.parse(fh.read())
    except (OSError, SyntaxError, ValueError) as exc:
        return uncertain(f"could not parse {file} ({exc.__class__.__name__}); escalated")

    aliases = _Aliases(tree)
    scope = _enclosing_function(tree, claim.line)
    where = f"{os.path.basename(file)}:{claim.line}"
    target = scope if scope is not None else tree
    found = _scan(target, aliases, frozenset(_bound_locally(target)))

    wall, mono, handles, attrs = (found["wall"], found["monotonic"],
                                  found["handles"], found["attrs"])
    shadowed = found["shadowed"]

    # --- refutation, held to "an absence the file could not have hidden" -------
    if not (wall or mono or handles or shadowed):
        if aliases.star_import:
            return uncertain(
                f"no clock read found in the scope @ {where}, but the file has a "
                f"`from ... import *` from a time module: an unqualified now()/time() "
                f"could resolve to it, so absence is not provable here; escalated")
        return make_verdict(
            fid, "FALSE_POSITIVE",
            f"ast: no wall-clock or monotonic read in the enclosing scope @ {where}; "
            f"the finding's premise does not hold", "ast")

    if not wall:
        detail = []
        if shadowed:
            detail.append(f"name(s) spelled like a clock read but rebound in this scope: "
                          f"{_fmt(shadowed, file)} -- what they hold is not decidable here")
        if mono:
            detail.append(f"monotonic source(s): {_fmt(mono, file)} -- un-injectable, "
                          f"but normally correct for measuring durations")
        if handles:
            detail.append(f"clock function referenced without being called: "
                          f"{_fmt(handles, file)} -- invocation time is not decidable here")
        return uncertain(f"ast: no wall-clock CALL in the scope @ {where}; " +
                         "; ".join(detail) + "; adjudicate")

    # --- a wall-clock read is present; is injected time also in play? ---------
    ev_clock = f"wall-clock read: {_fmt(wall, file)}"
    if scope is None:
        return uncertain(f"ast: {ev_clock}, at module level -- there is no parameter list "
                         f"to carry injected time, so this cannot be settled here; adjudicate")

    params = _param_names(scope)
    strong = [p for p in params if p in CLOCK_STRONG_PARAMS]
    weak = [p for p in params if p in CLOCK_WEAK_PARAMS]

    if not (strong or weak or attrs):
        return uncertain(
            f"ast: {ev_clock}, but the enclosing function takes no recognised "
            f"injected-time parameter. The clock may still arrive via self, a global, "
            f"or a caller -- absence of a parameter is not absence of injected time; "
            f"adjudicate")

    used = _referenced_names(scope)
    dead = [p for p in strong if p not in used]

    if dead:
        return make_verdict(
            fid, "CONFIRMED",
            f"ast: parameter {', '.join(repr(p) for p in dead)} is declared and NEVER "
            f"referenced in the enclosing function (line {scope.lineno}), while the "
            f"body performs a {ev_clock}. The caller is offered injectable time and "
            f"does not get it.", "ast")

    live = [p for p in strong if p in used]
    parts = []
    if live:
        parts.append(f"injected-time parameter {', '.join(repr(p) for p in live)} IS "
                     f"used in the scope")
    if weak:
        parts.append(f"weak-signal parameter(s) {', '.join(repr(p) for p in weak)}")
    if attrs:
        parts.append(f"clock attribute(s) {_fmt(attrs, file)}")
    return uncertain(
        f"ast: {ev_clock} co-occurs with injected time ({'; '.join(parts)}). "
        f"Co-occurrence is not a defect -- stamping an audit log with the real time "
        f"while the logic runs on the injected clock is correct; adjudicate")
