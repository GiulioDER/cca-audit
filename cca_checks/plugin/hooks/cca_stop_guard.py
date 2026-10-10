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
  L6 recorded BLOCKED              -> allow: the run is waiting for a human
  incomplete, refusals < 3         -> block with what is owed, count the refusal
  complete but still open          -> block: "run pipeline close" (counted too)
  refusals >= 3                    -> allow, log STOPPED_INCOMPLETE once

The cap lives in the run state so it is per run and visible. Claude Code's own
limit (eight consecutive continuations) is a backstop, not the design.
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

    root = find_root(pathlib.Path(payload.get("cwd") or os.getcwd()).resolve())
    if root is None:
        return 0
    try:
        core = _load_core()
    except Exception:  # noqa: BLE001 - see main()
        return 0
    try:
        state = core.read_state(root)
    except ValueError as exc:
        return _block(
            f"The CCA run state is unreadable ({exc}). Retire it with "
            '`python -m cca_checks pipeline abort --reason "<why>"`.'
        )
    if state is None or core.is_legacy(state):
        return 0
    gate = state.get("layers", {}).get("L6")
    if isinstance(gate, dict) and str(gate.get("detail", "")).strip().upper() == "BLOCKED":
        return 0

    reasons = core.outstanding(state, core.fingerprint(root))
    refusals = int(state.get("stop_refusals", 0) or 0)
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
        return 0

    state["stop_refusals"] = refusals + 1
    core.write_state(root, state)
    if reasons:
        return _block(
            "The CCA run in this checkout is not finished:\n  - "
            + "\n  - ".join(reasons)
            + "\nContinue with the first owed layer, re-reading its section of audit-fix.md. "
            "If the run must end here, retire it: "
            'python -m cca_checks pipeline abort --reason "<why>"'
        )
    return _block(
        "The CCA run is complete but still open. Close it: python -m cca_checks pipeline close"
    )


if __name__ == "__main__":
    sys.exit(main())
