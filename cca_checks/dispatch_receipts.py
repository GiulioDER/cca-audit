"""Codex prompt preparation receipts, not proof of the eventual tool input.

Some Codex spawn paths expose an encrypted message to PreToolUse. Check the
plaintext prompt before dispatch and bind L1 to those bytes, the brief and tree.
The same orchestrator controls the files and dispatch, so this is a workflow
gate, not an independent attestation that an auditor saw the checked prompt.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from . import deferred


def role_name(role: str) -> str:
    """Normalize named Codex tasks, including the two extra bug-auditor scopes."""
    from .pipeline_state import core

    if not isinstance(role, str):
        raise ValueError("auditor role must be a string")
    name = role.rsplit("/", 1)[-1].replace("_", "-").removeprefix("cca-")
    if name not in core.L1_AUDITOR_TYPES | {"stakes-auditor", "data-auditor"}:
        raise ValueError(f"unknown L1 auditor role {role!r}")
    return name


def scope(client: str | None, mode: str, auditors: list[str], tier: str) -> list[str]:
    if client not in (None, "claude", "codex"):
        raise ValueError(f"unknown pipeline client {client!r}")
    if not isinstance(auditors, list):
        raise ValueError("auditor dispatch list must be a list")
    roles = [role_name(role) for role in auditors]
    if client == "codex" and mode in ("DIFF", "HUNT"):
        if not roles or len(roles) != len(set(roles)):
            raise ValueError("Codex DIFF/HUNT needs a nonempty, unique --auditor dispatch list")
        required = {"security-auditor", "bug-auditor", "code-auditor"}
        if tier != "FAST":
            required |= {"perf-auditor", "doc-auditor", "env-validator"}
        if not required.issubset(roles):
            raise ValueError("Codex --auditor list must include every core auditor for its tier")
    elif roles:
        raise ValueError("--auditor is only for Codex DIFF/HUNT runs")
    return roles


def _brief_hash(run: Path) -> str:
    from .pipeline_state import core

    values = {name: hashlib.sha256((run / name).read_bytes()).hexdigest()
              for name in core.BRIEF_FILES if (run / name).is_file()}
    if not values:
        raise ValueError("Step 1 has not built the prior-bugs brief")
    return hashlib.sha256(json.dumps(values, sort_keys=True).encode()).hexdigest()


def _prompt(root: Path, path: Path) -> bytes:
    if not path.resolve().is_relative_to((root / ".claude/audits").resolve()):
        raise ValueError("prompt-file must stay under this checkout's .claude/audits/")
    return path.read_bytes()


def check(role: str, prompt_file: str, root=".") -> dict:
    from .pipeline_state import _store_run_file, core, current_tree, load

    state = load(root)
    if state is None or state.legacy or state.client != "codex" or state.mode == "DEFERRED":
        raise ValueError("check-dispatch needs an open Codex DIFF/HUNT run")
    if state.paused or "L1" in state.layers:
        raise ValueError("check-dispatch must run before L1 in an active, unpaused run")
    role = role_name(role)
    if role not in state.auditors:
        raise ValueError(f"{role} is not in this run's --auditor list")
    root = Path(root).resolve()
    path = Path(prompt_file)
    path = path if path.is_absolute() else root / path
    try:
        prompt = _prompt(root, path)
        agent_type = "bug-auditor" if role in {"stakes-auditor", "data-auditor"} else role
        reasons = core.dispatch_reasons(root, state.to_json(), agent_type, prompt.decode("utf-8"))
        if reasons:
            raise ValueError("; ".join(reasons))
        brief_hash = _brief_hash(deferred.runs_dir(root) / state.run_id)
    except (OSError, UnicodeError) as exc:
        raise ValueError(f"cannot read dispatch prompt or brief: {exc}") from exc
    tree = current_tree(state, root)
    if tree is None:
        raise ValueError("dispatch tree could not be fingerprinted")
    receipt = {"schema": 1, "run_id": state.run_id, "role": role,
               "prompt_file": path.resolve().relative_to(root).as_posix(),
               "prompt_sha256": hashlib.sha256(prompt).hexdigest(),
               "brief_sha256": brief_hash, "tree": tree, "checked_utc": core.now()}
    _store_run_file(state, f"dispatch-{role}.json", receipt, root)
    return receipt


def require(state, root, tree) -> None:
    if state.client != "codex" or state.mode not in ("DIFF", "HUNT"):
        return
    root = Path(root).resolve()
    try:
        roles = scope(state.client, state.mode, state.auditors, state.tier)
        run = deferred.runs_dir(root) / state.run_id
        brief_hash = _brief_hash(run)
        for role in roles:
            receipt = json.loads((run / f"dispatch-{role}.json").read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError(f"invalid {role} dispatch receipt")
            path = root / receipt.get("prompt_file", "")
            expected = {"schema": 1, "run_id": state.run_id, "role": role,
                        "prompt_sha256": hashlib.sha256(_prompt(root, path)).hexdigest(),
                        "brief_sha256": brief_hash, "tree": tree}
            if tree is None or any(receipt.get(k) != v for k, v in expected.items()):
                raise ValueError(f"stale or mismatched {role} dispatch receipt")
    except (OSError, ValueError, TypeError) as exc:
        raise ValueError(
            f"Codex L1 needs current check-dispatch receipts for every --auditor: {exc}"
        ) from exc
