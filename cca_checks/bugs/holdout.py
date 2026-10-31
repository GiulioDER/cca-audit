"""The memory holdout: about one DIFF run in three is audited without the CCA prior-bugs brief.

Design: docs/superpowers/specs/2026-10-28-auditor-memory-attribution-design.md, section
"Revision 2026-10-31". A run is in the holdout arm when `int(sha256(run_id), 16) % 3 == 0`.
The rule is deterministic, so anyone can recompute a run's arm from its id.

Whichever command builds the brief (`bugs brief --out` or `bugs prior --records`) calls
`assign` with the path it was given. When that path lies in a run directory,
`.claude/audits/runs/<run_id>/`, the arm is written to `memory-arm.json` there, in both arms.
A holdout run's brief renders as the empty form the same command prints when the ledger has
nothing for the change; the auditors are not told anything was withheld.

Only a DIFF run is ever withheld. A HUNT run, a deferred pass, and a run whose mode cannot be
read from the open run state get arm `brief`; when the mode could not be read, the arm file
also carries `reason`. Memory never suppresses a finding here: only the brief is withheld.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

#: The holdout runs while this is True; a follow-up turns it off after the pre-registered look.
ACTIVE: bool = True
DENOM = 3
RULE = "int(sha256(run_id), 16) % 3 == 0"
ARM_FILE = "memory-arm.json"
ARMS = ("brief", "holdout")


def withheld(run_id: str) -> bool:
    """True when the rule puts `run_id` in the holdout arm; always False while not ACTIVE."""
    if not ACTIVE:
        return False
    return int(hashlib.sha256(run_id.encode("utf-8")).hexdigest(), 16) % DENOM == 0


def run_dir(path: Path) -> Path | None:
    """The run directory a brief file at `path` belongs to, or None when it is in none."""
    try:
        run = Path(path).resolve().parent
    except (OSError, RuntimeError):
        return None
    parents = run.parents
    if (len(parents) < 4 or not run.name or parents[0].name != "runs"
            or parents[1].name != "audits" or parents[2].name != ".claude"):
        return None
    return run


def _mode_reason(root: Path, run_id: str) -> tuple[str | None, str | None]:
    """(the open run's mode, None), or (None, why the mode could not be read)."""
    from cca_checks.pipeline_state import core

    try:
        state = core.read_state(root)
    except (OSError, ValueError) as exc:
        return None, f"run state unreadable ({exc.__class__.__name__})"
    if state is None:
        return None, "no open run state"
    if state.get("run_id") != run_id:
        return None, "the open run state names another run"
    mode = state.get("mode", "DIFF")
    if not isinstance(mode, str):
        return None, "the open run state has no readable mode"
    return mode.upper(), None


def assign(path: Path, *, never: bool = False) -> bool:
    """Decide the arm of the run `path` belongs to, record it, and say whether to withhold.

    Returns False, and writes nothing, when the holdout is not ACTIVE, `path` is not in a run
    directory, or that run is complete (it holds `summary.json`): a replay of a closed run's
    answer builds no auditor brief, so it must not add an arm the run never had. `never` is
    the HUNT form (`bugs brief --skipped`): arm `brief`. The brief is withheld only when
    `memory-arm.json` was written with arm `holdout`, so a withheld brief is never left
    unrecorded.
    """
    if not ACTIVE:
        return False
    run = run_dir(path)
    if run is None or (run / "summary.json").exists():
        return False
    run_id = run.name
    arm, reason = "brief", None
    if not never:
        mode, reason = _mode_reason(run.parents[3], run_id)
        if mode == "DIFF" and withheld(run_id):
            arm = "holdout"
    record: dict[str, str] = {"run_id": run_id, "arm": arm, "rule": RULE}
    if reason is not None:
        record["reason"] = reason
    try:
        run.mkdir(parents=True, exist_ok=True)
        (run / ARM_FILE).write_text(json.dumps(record, ensure_ascii=True) + "\n",
                                    encoding="utf-8")
    except OSError:
        return False
    return arm == "holdout"
