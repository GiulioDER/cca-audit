#!/usr/bin/env python
"""The rules of the CCA commit and stop gates, in one stdlib-only module.

WHY ONE MODULE. 0.10.x kept the required-layer table twice, once in
`cca_checks/pipeline_state.py` and once in the standalone commit guard, pinned by
a drift test. A second hook would have made it three copies. Instead the hooks
and `pipeline_state` all load this file by path from their own directory, so the
rules exist once and the hooks still import nothing outside the stdlib.

WHAT CHANGED FROM 0.10.x, and why each change exists:
  * fixes are detected from the TREE, not from `fixes_applied`. In 0.10.x an
    agent that edited code and never recorded L4 left `fixes_applied == 0`, and
    the guard stopped requiring L5, L5.5 and L5.6.
  * every layer, L0.4 to L6, is required when its condition holds.
  * order is checked by a monotonic `seq`, and every verdict from L5 onward
    carries the fingerprint it judged, so an edit after a verdict makes it stale.
  * only `done` satisfies a required layer; 0.10.x let `--status n/a` pass.
  * L2.6 and L5.6 must be recorded by their tools.

A run whose state lacks `"schema": 2` was started by 0.10.x and is judged by the
0.10.x rules, verbatim (`legacy_commit_reasons`).

THE BOUND, stated as 0.10.x stated it: an agent with a shell can write the state
file. These rules make forgetting a layer impossible and forging one a
deliberate act. They do not prove a layer was performed well.
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone

SCHEMA = 2
LAYERS = ("L0.4", "L1", "L2", "L2.5", "L2.6", "L3", "L4", "L5", "L5.5", "L5.6", "L6")
TIERS = ("FAST", "STANDARD", "DEEP")
APPROVING_VERDICT = "APPROVED"
TOOL_LAYERS = {
    "L2.6": "python .claude/tools/cca_scorecard.py --json --record",
    "L5.6": "python .claude/tools/cca_tautology_check.py verify --proof ... --record",
}
FIX_VERDICT_LAYERS = ("L5", "L5.5", "L5.6", "L6")
HEADINGS = {
    "L0.4": "Step 0.4", "L1": "Step 1", "L2": "Step 2", "L2.5": "Step 2.5",
    "L2.6": "Step 2.6", "L3": "Step 3", "L4": "Step 4", "L5": "Step 5",
    "L5.5": "Step 5.5", "L5.6": "Step 5.6", "L6": "Step 6",
}
STOP_REFUSAL_CAP = 3
#: Layers whose work is written to disk at record time, and what the file holds. A run
#: paused after one of them resumes from these files instead of from a lost context.
OUTPUT_LAYERS = {
    "L1": "the auditors' findings arrays, as returned (one JSON array per auditor, "
          "or one combined array)",
    "L2": "the consolidated unique findings",
    "L2.5": "the verdict for every consolidated finding",
    "L3": "the fix plan (finding ids to fix, deferred ids)",
    "L4": "the fixes applied (fix id, finding id, files)",
}
PAUSED_REASON = "the run is paused; resume it, finish it, or abort it"
EXCLUDED = ".claude/audits"
#: Bound on the whole fingerprint. The hook timeouts in the settings snippet sit
#: above it: a hook that times out is a non-blocking error, which fails OPEN.
FINGERPRINT_TIMEOUT_S = 30

# 0.10.x tables, verbatim, for runs that started under them.
LEGACY_CORE_REQUIRED = ("L1", "L2", "L2.5", "L6")
LEGACY_FIX_REQUIRED = {
    "FAST": ("L5",),
    "STANDARD": ("L5", "L5.5", "L5.6"),
    "DEEP": ("L5", "L5.5", "L5.6"),
}
LEGACY_BAD_STATUS = frozenset({"skipped", "failed"})

_NO_FIX_REASON = (
    "this run was started with `no-fix`, which forbids editing and committing; "
    "abort the run first if this commit is unrelated"
)


# --- state IO ------------------------------------------------------------------

def state_file(root) -> pathlib.Path:
    return pathlib.Path(root) / ".claude" / "audits" / "run-state.json"


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_state(root) -> dict | None:
    path = state_file(root)
    if not path.exists():
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"run state at {path} is unreadable ({exc.__class__.__name__})"
        ) from exc
    if not isinstance(raw, dict):
        raise ValueError(f"run state at {path} is not a JSON object")
    return raw


def write_state(root, state: dict) -> None:
    path = state_file(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def append_log(root, line: str) -> pathlib.Path:
    log = state_file(root).parent / "EXECUTION_LOG.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line if line.endswith("\n") else line + "\n")
    return log


# --- fingerprint -----------------------------------------------------------------

def _inside(path: str, root: str) -> bool:
    """True when `path` is `root` or lies anywhere under it (symlinks resolved)."""
    real_root = os.path.realpath(root)
    try:
        return os.path.commonpath([os.path.realpath(path), real_root]) == real_root
    except ValueError:  # different drives: certainly not inside
        return False


def _git_exe(root: str) -> str | None:
    """The git to run, or None when the only candidate cannot be trusted.

    `root` is the repository top level, i.e. the tree under audit. A git found
    anywhere inside it is refused, not only one at its root: on Windows
    `shutil.which` still prepends the current directory unless
    NoDefaultCurrentDirectoryInExePath is set, and the hook's cwd may be any
    subdirectory of the repository, whose contents the audited code controls.
    """
    # Explicit PATH: without it Windows searches the current directory first, and
    # the current directory is the repository under audit (see cca_checks/toolpath.py).
    found = shutil.which("git", path=os.environ.get("PATH"))
    if not found or not os.path.isabs(found):
        return None
    exe = os.path.abspath(found)
    if _inside(os.path.dirname(exe), root):
        return None
    return exe


def _top_hint(root: str) -> str:
    """Nearest ancestor holding `.git`, found without running git; else `root` itself."""
    for candidate in [pathlib.Path(root), *pathlib.Path(root).parents]:
        if (candidate / ".git").exists():
            return str(candidate)
    return root


def fingerprint(root) -> str | None:
    """Tree id of the working tree content, or None when it cannot be computed.

    A tree id rather than a hash of `git diff HEAD`, so the value survives the
    commit at Step 7: committing the same content moves HEAD but not the tree.
    Built in a COPY of the index so the user's staging area is never touched, and
    the run's own `.claude/audits` is removed from that copy so its state, log and
    ledger cannot perturb it, whatever the repository's ignore rules say.

    The tree covers the WHOLE repository even when `root` is a subdirectory, so an
    edit elsewhere is seen. FINGERPRINT_TIMEOUT_S is one deadline for everything
    here: each git call gets what is left of it, and past it there is no answer.
    """
    return _fingerprint(root, staged=False)[0]


def commit_view(root) -> tuple[str | None, bool | None]:
    """(fingerprint, staged_ok) under ONE deadline, for the commit guard.

    `git commit` ships the INDEX, while the verdicts judged the WORKING TREE. Content
    staged and then changed again in the working tree would be committed while every
    verdict read fresh. `staged_ok` is True when every staged path (index differing
    from HEAD, outside the run's `.claude/audits`) holds exactly its working tree
    content, False when one does not, None when it cannot be told. Staging only some
    of the judged files stays allowed: each staged file is content a verdict saw.
    """
    return _fingerprint(root, staged=True)


def _fingerprint(root, *, staged: bool) -> tuple[str | None, bool | None]:
    root = str(pathlib.Path(root).resolve())
    # Checked against the nearest `.git` ancestor BEFORE any execution, and again
    # against git's own answer below.
    git = _git_exe(_top_hint(root))
    if git is None:
        return None, None
    deadline = time.monotonic() + FINGERPRINT_TIMEOUT_S

    def run(*args, top=None, env=None) -> str:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired([git, *args], FINGERPRINT_TIMEOUT_S)
        return subprocess.run(
            [git, "-C", top or root, *args], capture_output=True, check=True,
            timeout=max(0.1, remaining), env=env,
        ).stdout.decode("utf-8", "replace").strip()

    try:
        top = os.path.realpath(run("rev-parse", "--show-toplevel"))
        if _git_exe(top) is None:
            return None, None
        rel = os.path.relpath(os.path.realpath(root), top).replace(os.sep, "/")
        if rel == ".." or rel.startswith("../"):
            return None, None
        # `:(literal)`: a run directory named with glob characters is not a pattern.
        audits = EXCLUDED if rel == "." else f"{rel}/{EXCLUDED}"
        excluded = ":(literal)" + audits
        index = run("rev-parse", "--path-format=absolute", "--git-path", "index", top=top)
        with tempfile.TemporaryDirectory(prefix="cca-fp-") as tmp:
            tmp_index = os.path.join(tmp, "index")
            if os.path.exists(index):
                # copy2, not copyfile: the copy must keep the real index's mtime. A
                # copy stamped "now" defeats git's racy-clean check, and an edit made
                # in the same filesystem tick as the last index write was then missed
                # (about 1 in 4 on a loaded Windows machine, measured 2026-10-10).
                shutil.copy2(index, tmp_index)
            env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
            staged_ok = None
            if staged:
                # Read from the copy BEFORE `add -A` rewrites it. A diff pathspec may
                # exclude an ignored path (only `git add` refuses that).
                scope = ("--", ".", f":(exclude,literal){audits}")
                cached = run("diff", "--cached", "--name-only", "-z", *scope, top=top, env=env)
                dirty = run("diff", "--name-only", "-z", *scope, top=top, env=env)
                staged_ok = not ({p for p in cached.split("\0") if p}
                                 & {p for p in dirty.split("\0") if p})
            run("add", "-A", "--", ".", top=top, env=env)
            # Dropped from the index afterwards rather than excluded by pathspec: an
            # exclude pathspec naming an ignored path makes `git add` exit 1, and
            # which paths are ignored depends on the user's .gitignore, so only
            # removal is independent of every ignore rule.
            run("rm", "--cached", "-r", "-q", "--ignore-unmatch", "--", excluded,
                top=top, env=env)
            return run("write-tree", top=top, env=env) or None, staged_ok
    except (OSError, ValueError, subprocess.SubprocessError):
        # ValueError: os.path.relpath across drives. Fail closed (fixes become owed).
        return None, None


# --- rules -----------------------------------------------------------------------

def is_legacy(state: dict) -> bool:
    return state.get("schema") != SCHEMA


def _std(state: dict) -> bool:
    return str(state.get("tier", "")).upper() != "FAST"


def _hunt(state: dict) -> bool:
    return str(state.get("mode", "DIFF")).upper() == "HUNT"


def _int(value) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


def fixes_owed(state: dict, tree: str | None) -> bool:
    """True when L4 onward is owed. Unknown fingerprints count as changed.

    An L3 that planned fixes owes them before the tree changes too: otherwise L6 could
    follow L3 directly and the run close COMPLETE with its planned fixes never applied.
    """
    base = state.get("base_tree")
    return (
        _int(state.get("fixes_applied")) > 0 or tree is None or base is None or tree != base
        or _fixes_planned(state)
    )


def required_layers(state: dict, owed: bool) -> tuple[str, ...]:
    req: list[str] = []
    if _hunt(state):
        req.append("L0.4")
    req += ["L1", "L2", "L2.5"]
    if _std(state):
        req.append("L2.6")
    if not state.get("no_fix"):
        req.append("L3")
        if owed:
            req += ["L4", "L5"]
            if _std(state):
                req += ["L5.5", "L5.6"]
        req.append("L6")
    return tuple(req)


def predecessors(layer: str, state: dict, owed: bool) -> tuple[str, ...]:
    std = _std(state)
    if layer == "L6":
        if owed:
            return ("L5", "L5.5", "L5.6") if std else ("L5",)
        return ("L3",)
    table = {
        "L0.4": (),
        "L1": ("L0.4",) if _hunt(state) else (),
        "L2": ("L1",),
        "L2.5": ("L2",),
        "L2.6": ("L2.5",),
        "L3": ("L2.5", "L2.6") if std else ("L2.5",),
        "L4": ("L3",),
        "L5": ("L4",),
        "L5.5": ("L5",),
        "L5.6": ("L5",),
    }
    return table[layer]


def _layers(state: dict) -> dict:
    raw_layers = state.get("layers")
    layers: dict = raw_layers if isinstance(raw_layers, dict) else {}
    return layers


def _layer_reasons(layer: str, state: dict, owed: bool, tree: str | None) -> list[str]:
    """Why one required layer is not satisfied. Empty means satisfied.

    The one per layer check: `outstanding` and `next_owed` both read it, so "complete"
    and "what to do next" cannot disagree about a layer.
    """
    layers = _layers(state)
    entry = layers.get(layer)
    where = HEADINGS[layer]
    if not isinstance(entry, dict):
        return [f"{layer} has no recorded verdict (re-read {where})"]
    reasons: list[str] = []
    status = str(entry.get("status", "")).lower()
    if status != "done":
        reasons.append(
            f"{layer} is recorded as {status or 'empty'}; a required layer must be "
            f"done (re-read {where})"
        )
    else:
        if layer in TOOL_LAYERS and entry.get("source") != "tool":
            reasons.append(f"{layer} must be recorded by its tool: {TOOL_LAYERS[layer]}")
        if layer in FIX_VERDICT_LAYERS and owed and (tree is None or entry.get("tree") is None):
            # Unknown is never current: None == None would read every verdict as fresh.
            reasons.append(
                f"{layer} cannot be shown current: the working tree could not be "
                "fingerprinted (git missing from PATH, older than 2.31, or timed out)"
            )
        elif layer in FIX_VERDICT_LAYERS and owed and entry.get("tree") != tree:
            reasons.append(
                f"{layer} was recorded before the latest edit to the tree; run it again "
                f"(re-read {where})"
            )
        for prior in predecessors(layer, state, owed):
            before = layers.get(prior)
            if isinstance(before, dict) and _int(before.get("seq")) >= _int(entry.get("seq")):
                reasons.append(
                    f"{layer} is older than {prior}, which was recorded again after it; "
                    f"run {layer} again (re-read {where})"
                )
    if layer == "L0.4" and str(entry.get("detail", "")).strip().upper() != "PASS":
        reasons.append(
            "L0.4 did not read PASS; a hunt that fails viability must be aborted, not continued"
        )
    if layer == "L6":
        verdict = str(entry.get("detail", "")).strip().upper()
        if not verdict:
            reasons.append(
                "L6 was recorded without a verdict; record it as "
                "--detail APPROVED, REVISE or BLOCKED"
            )
        elif verdict != APPROVING_VERDICT:
            reasons.append(
                f"the L6 architect gate returned {verdict}, not {APPROVING_VERDICT}"
            )
    return reasons


def outstanding(state: dict, tree: str | None) -> list[str]:
    """Why this schema 2 run is not complete. Empty means complete."""
    owed = fixes_owed(state, tree)
    reasons: list[str] = []
    for layer in required_layers(state, owed):
        reasons += _layer_reasons(layer, state, owed, tree)
    if state.get("no_fix"):
        base = state.get("base_tree")
        if base and tree and tree != base:
            reasons.append(
                "this run was started with `no-fix`, but the working tree changed "
                "after it started"
            )
    return reasons


def next_owed(state: dict, tree: str | None) -> str | None:
    """The first required layer, in pipeline order, that is not satisfied; None when none is.

    A run can still be incomplete when this is None: a `no-fix` run whose tree changed owes
    no layer, only an abort, and `outstanding` says so. An L3 that planned fixes makes L4
    next through `fixes_owed`, so a run paused between L3 and L4 resumes at Step 4.
    """
    owed = fixes_owed(state, tree)
    for layer in required_layers(state, owed):
        if _layer_reasons(layer, state, owed, tree):
            return layer
    return None


def _fixes_planned(state: dict) -> bool:
    """L3 planned at least one fix and no L4 was recorded after it."""
    layers = _layers(state)
    plan, done = layers.get("L3"), layers.get("L4")
    if not isinstance(plan, dict):
        return False
    counts = plan.get("counts")
    if _int(counts.get("to_fix") if isinstance(counts, dict) else 0) <= 0:
        return False
    return not isinstance(done, dict) or _int(done.get("seq")) < _int(plan.get("seq"))


def legacy_commit_reasons(state: dict) -> list[str]:
    """The 0.10.x guard's `evaluate`, verbatim."""
    reasons: list[str] = []
    tier = str(state.get("tier", "")).upper()
    if tier not in LEGACY_FIX_REQUIRED:
        return [f"run state names an unknown tier {tier!r}"]
    layers = state.get("layers")
    if not isinstance(layers, dict):
        return ["run state has no 'layers' object"]
    if state.get("no_fix"):
        reasons.append(_NO_FIX_REASON)
    required = list(LEGACY_CORE_REQUIRED)
    if _int(state.get("fixes_applied")) > 0:
        required.extend(LEGACY_FIX_REQUIRED[tier])
    for layer in required:
        entry = layers.get(layer)
        if not entry:
            reasons.append(f"{layer} has no recorded verdict (tier {tier})")
        elif str(entry.get("status", "")).lower() in LEGACY_BAD_STATUS:
            reasons.append(f"{layer} is recorded as {entry.get('status')}")
    gate = layers.get("L6")
    if isinstance(gate, dict):
        verdict = str(gate.get("detail", "")).strip().upper()
        if not verdict:
            reasons.append(
                "L6 was recorded without a verdict; record it as "
                "--detail APPROVED, REVISE or BLOCKED"
            )
        elif verdict != APPROVING_VERDICT:
            reasons.append(f"the L6 architect gate returned {verdict}, not {APPROVING_VERDICT}")
    return reasons


def commit_reasons(state: dict, tree: str | None) -> list[str]:
    """Why a commit may not proceed against this open run. Empty means allowed."""
    if is_legacy(state):
        return legacy_commit_reasons(state)
    tier = str(state.get("tier", "")).upper()
    if tier not in TIERS:
        return [f"run state names an unknown tier {tier!r}"]
    if not isinstance(state.get("layers"), dict):
        return ["run state has no 'layers' object"]
    reasons = [PAUSED_REASON] if state.get("paused") else []
    if state.get("no_fix"):
        reasons.append(_NO_FIX_REASON)
    return reasons + outstanding(state, tree)
