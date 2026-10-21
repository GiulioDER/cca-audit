#!/usr/bin/env python
"""Stop hook: do not let the agent end its turn in the middle of a CCA run.

The commit guard only fires on `git commit`. A `no-fix` run never commits, and a
run that stops to summarise after L5 goes green never reaches the commit either,
so neither ever met a gate. This hook meets them where they stop.

BEHAVIOUR (always exit 0; a block is the JSON decision on stdout)
  background tasks running         -> allow: the orchestrator is waiting on its
                                      own subagents, which is not stopping
  no run, legacy run, core missing -> allow: a stop guard must never trap a
                                      session over its own installation
  any unexpected failure           -> allow: the opposite direction from the
                                      commit guard, on purpose. A commit guard
                                      that fails open ships unverified fixes; a
                                      stop guard that fails closed holds a
                                      session hostage to its own bug
  run state unreadable             -> block ONCE with the abort command; allow when
                                      stop_hook_active is true, so a corrupt file
                                      can cost one continuation, never a trap
  run paused (`pipeline pause`)    -> allow, silently and without counting a refusal
  L6 recorded BLOCKED              -> allow: the run is waiting for a human
  incomplete, refusals < 3         -> block with what is owed, count the refusal
  complete but still open          -> block: "run pipeline close" (counted too)
  refusals >= 3                    -> allow, log STOPPED_INCOMPLETE once

The cap lives in the run state so it is per run and visible. Claude Code's own
limit (eight consecutive continuations) is a backstop, not the design.

WHICH RUNS. The payload's `cwd` is the session's directory, and a run is often
opened in a worktree below it (`.claude/worktrees/<x>`), which the walk up from
`cwd` never reaches. So the candidates are the run found from `cwd` (bounded by the
repository root, as before) plus every run in the open run registry
(`cca_gate_core.registry_path`) whose root is `cwd` or lies under it. Each is judged
by the rules above; the first that should block blocks, naming its root and spending
that run's refusal. A run in a checkout that is neither the session's directory nor
below it is out of scope: this session did not start it, and blocking on it would
trap every session on the machine. A registry that cannot be read costs only the
runs below `cwd`, with a warning on stderr.

WHOSE RUN. Being below `cwd` is not ownership: every worktree lies below the main
checkout, so a session working there was held by any other session's run, and each
of those stops spent that run's refusals, leaving its real owner unguarded. `pipeline
start` (and `resume`) record `owner_session` from `CLAUDE_CODE_SESSION_ID`; a run whose
`owner_session` differs from the payload's `session_id` is allowed silently, before
any refusal is counted, but only when it was found through the registry below `cwd`:
the run in the session's own checkout always holds it, because the recorded owner
comes from an environment variable the agent could set to dodge its own guard. A run
with no recorded owner (older runs, a start outside Claude Code) or a payload with no
`session_id` is judged as before.
"""

from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import sys

_HERE = pathlib.Path(__file__).resolve().parent


def _load_core():
    path = _HERE / "cca_gate_core.py"
    spec = importlib.util.spec_from_file_location("cca_gate_core", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def claimed_session(root: pathlib.Path) -> str | None:
    """The session named by the checkout's `claude-session-claim`, or None.

    Workspace tooling writes `session=<id>` into a worktree's own git directory when a
    session claims it. A run opened before `owner_session` existed has no recorded owner;
    this is the fallback for those. Anything unreadable is None (judged as before).
    """
    try:
        dot_git = root / ".git"
        if dot_git.is_file():
            line = dot_git.read_text(encoding="utf-8").strip()
            if not line.startswith("gitdir:"):
                return None
            git_dir = pathlib.Path(line.split(":", 1)[1].strip())
            if not git_dir.is_absolute():
                git_dir = root / git_dir
        else:
            git_dir = dot_git
        for raw in (git_dir / "claude-session-claim").read_text(encoding="utf-8").splitlines():
            if raw.startswith("session="):
                return raw.split("=", 1)[1].strip() or None
    except (OSError, UnicodeDecodeError):
        return None
    return None


def find_root(start: pathlib.Path) -> pathlib.Path | None:
    """Same boundary rule as the commit guard: stop at the repository root."""
    for candidate in [start, *start.parents]:
        if (candidate / ".claude" / "audits" / "run-state.json").exists():
            return candidate
        if (candidate / ".git").exists():
            return None
    return None


def _block(reason: str) -> int:
    print(json.dumps({"decision": "block", "reason": reason}))
    return 0


def main() -> int:
    try:
        raw = sys.stdin.read()
    except (OSError, UnicodeDecodeError):
        return 0
    try:
        return _decide(raw)
    except Exception:  # noqa: BLE001 - a stop guard must never trap a session
        return 0


def _decide(raw: str) -> int:
    try:
        payload = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    if payload.get("background_tasks"):
        return 0

    cwd = pathlib.Path(payload.get("cwd") or os.getcwd()).resolve()
    candidates = []
    root = find_root(cwd)
    if root is not None:
        candidates.append(root)
    try:
        core = _load_core()
    except Exception:  # noqa: BLE001 (see main())
        return 0
    try:
        candidates += [r for r in core.registered_roots() if core._inside(str(r), str(cwd))]
    except Exception as exc:  # noqa: BLE001 (the registry is a lookup aid, never a gate)
        print(
            f"CCA stop guard: the run registry could not be read ({exc.__class__.__name__}: "
            f"{exc}); runs below {cwd} were not checked",
            file=sys.stderr,
        )
    own = os.path.normcase(os.path.realpath(root)) if root is not None else None
    seen: set[str] = set()
    for candidate in candidates:
        key = os.path.normcase(os.path.realpath(candidate))
        if key in seen:
            continue
        seen.add(key)
        reason = _judge(core, pathlib.Path(candidate), payload, below=key != own)
        if reason is not None:
            return _block(reason)
    return 0


def _judge(core, root: pathlib.Path, payload: dict, *, below: bool = False) -> str | None:
    """Why the turn may not end because of the run at `root`; None to allow.

    Spends one of that run's refusals when it returns a reason (an unreadable state
    excepted, which has no counter to spend). `below` is true for a run found only
    through the registry under `cwd`; only such a run can be another session's.
    """
    abort = f'python -m cca_checks pipeline abort --root "{root}" --reason "<why>"'
    try:
        state = core.read_state(root)
    except ValueError as exc:
        if payload.get("stop_hook_active"):
            return None
        return f"The CCA run state in {root} is unreadable ({exc}). Retire it with `{abort}`."
    if state is None or core.is_legacy(state):
        return None
    owner = state.get("owner_session")
    if below and not owner:
        owner = claimed_session(root)
    session = payload.get("session_id")
    theirs = isinstance(owner, str) and isinstance(session, str) and owner and session
    if below and theirs and owner != session:
        # Another session's run below this one: allowed before the refusal counter, so
        # its owner keeps every refusal it is due. Never for the run in the session's own
        # checkout, which holds whoever works there whatever its recorded owner says (the
        # owner comes from the environment at `start`, which the agent itself controls).
        return None
    if state.get("paused"):
        # Paused on the record so the session can end: allowed before the refusal
        # counter, so a pause never spends one.
        return None
    gate = state.get("layers", {}).get("L6")
    if isinstance(gate, dict) and str(gate.get("detail", "")).strip().upper() == "BLOCKED":
        return None

    refusals = int(state.get("stop_refusals", 0) or 0)
    if refusals >= core.STOP_REFUSAL_CAP and state.get("stop_logged"):
        # Cap reached and already logged: the answer is "allow" whatever is owed, so
        # skip the fingerprint (up to FINGERPRINT_TIMEOUT_S on every turn end).
        return None
    reasons = core.outstanding(state, core.fingerprint(root))
    if refusals >= core.STOP_REFUSAL_CAP:
        if not state.get("stop_logged"):
            summary = "; ".join(reasons) or "complete but not closed"
            core.append_log(
                root,
                f"| {core.now()} | run {state.get('run_id', '?')} | STOPPED_INCOMPLETE | "
                f"{summary} |",
            )
            state["stop_logged"] = True
            core.write_state(root, state)
        return None

    state["stop_refusals"] = refusals + 1
    core.write_state(root, state)
    if reasons:
        return (
            f"The CCA run in {root} is not finished:\n  - "
            + "\n  - ".join(reasons)
            + "\nContinue with the first owed layer, re-reading its section of audit-fix.md. "
            f"If the run must end here, retire it: {abort}"
        )
    return (
        f"The CCA run in {root} is complete but still open. Close it: "
        f'python -m cca_checks pipeline close --root "{root}"'
    )


if __name__ == "__main__":
    sys.exit(main())
