"""Run state for the CCA audit+fix pipeline, and the commit gate built on it.

WHY THIS EXISTS. Every layer of `/audit-fix` was, until this module, enforced by
prose: the command file says "This is a DETERMINISTIC workflow -- follow every
step exactly" and nothing whatsoever checked that it had been. An orchestrator
that skipped L2.5 (anti-hallucination) or L6 (architect gate) and committed
anyway produced a diff that is *indistinguishable* from a fully verified one:
same commit message shape, same file list, same claimed provenance. That is
worse than not auditing, because the fixes then ship carrying the authority of a
pipeline that did not run.

The failure is structural rather than incidental, and the cause is worth naming:
the pipeline's steps are ~28KB of text read once, at the top of a run, and they
then have to survive six to eleven subagents returning long reports. By Step 2
they sit far behind the most recent tokens, while the agent harness carries
standing instructions that compete with them directly ("do not add further
review passes", "when you have enough information to act, act"). Prose loses
that contest reliably. So the gates stop being prose here.

DESIGN: FAIL CLOSED, BUT ONLY WHEN A RUN IS OPEN. No state file means no audit
is in flight and this module has no opinion about your commit. Once `start()`
has been called, a commit is refused until the tier's required layers each carry
a recorded verdict. The escape hatch is `abort()`, which is explicit, recorded
and dated, unlike the silent skip it replaces.

A NOTE ON WHAT THIS DOES NOT PROVE. `record()` is called by the same orchestrator
the gate constrains, so it cannot show that a layer was performed *well*. It
shows the layer was reached and given a verdict. That bound is worth stating
plainly rather than overselling: this stops a layer being skipped silently, not a
layer being run badly. Catching the second is what L2.5 and L5.6 are for, and
they only get the chance if they are reached at all.
"""

from __future__ import annotations

import json
import os
import pathlib
from dataclasses import dataclass, field
from datetime import datetime, timezone

__all__ = [
    "CommitDecision",
    "LAYERS",
    "PipelineState",
    "TIERS",
    "abort",
    "close",
    "load",
    "record",
    "required_layers",
    "start",
    "state_path",
    "verify_commit_allowed",
]

# Canonical layer ids, in pipeline order. A tuple rather than a free-form string
# so an unknown id is a hard error at `record()`. A typo'd layer name that
# recorded successfully but satisfied no requirement would reintroduce exactly
# the silent skip this module exists to remove.
LAYERS = (
    "L1",     # parallel auditors
    "L2",     # consolidate findings
    "L2.5",   # findings verification (anti-hallucination)
    "L2.6",   # auditor scorecard (advisory; never required)
    "L3",     # fix plan
    "L4",     # implement fixes
    "L5",     # re-verify (tests + lint)
    "L5.5",   # regression diff (anti-regression)
    "L5.6",   # red-state proof (tautological-test detector)
    "L6",     # architect gate
)

# Layers that must carry a verdict before ANY commit, at every tier. L2.6 is
# deliberately absent: it is a measurement over a trailing window, additive-only
# by its own contract, and blocking on a statistic that reports `learning` below
# n=10 would fire on every project that has not yet run ten audits.
_CORE_REQUIRED = ("L1", "L2", "L2.5", "L6")

# Additional layers once fixes have actually been written to the tree, by tier.
# Taken from the tier table at Step 0.6 of the pipeline: FAST runs no regression
# diff and no red-state proof, so requiring them there would block a legitimate
# fast run rather than catch a skipped one.
_FIX_REQUIRED = {
    "FAST": ("L5",),
    "STANDARD": ("L5", "L5.5", "L5.6"),
    "DEEP": ("L5", "L5.5", "L5.6"),
}

TIERS = tuple(_FIX_REQUIRED)

# The only L6 verdict that authorises a commit. REVISE and BLOCKED are recorded
# outcomes, not passes; reading "the gate ran" as "the gate approved" would let a
# BLOCKED run commit, which is the one thing the architect gate exists to stop.
_APPROVING_VERDICT = "APPROVED"

_TERMINAL_BAD_STATUS = frozenset({"skipped", "failed"})


def state_path(root: str | pathlib.Path = ".") -> pathlib.Path:
    """Where the open run lives. One run per checkout, by design.

    Per checkout rather than per branch: worktrees share a stash and a repo but
    not a working tree, and it is the working tree that holds the unverified
    fixes this gate is protecting.
    """
    return pathlib.Path(root) / ".claude" / "audits" / "run-state.json"


@dataclass
class PipelineState:
    run_id: str
    tier: str
    mode: str = "DIFF"
    no_fix: bool = False
    started_utc: str = ""
    fixes_applied: int = 0
    layers: dict = field(default_factory=dict)

    def to_json(self) -> dict:
        return {
            "run_id": self.run_id,
            "tier": self.tier,
            "mode": self.mode,
            "no_fix": self.no_fix,
            "started_utc": self.started_utc,
            "fixes_applied": self.fixes_applied,
            "layers": self.layers,
        }

    @classmethod
    def from_json(cls, raw: dict) -> PipelineState:
        tier = str(raw.get("tier", "")).upper()
        if tier not in TIERS:
            raise ValueError(
                f"unknown tier {tier!r} in run state (expected one of {', '.join(TIERS)})"
            )
        layers = raw.get("layers")
        if not isinstance(layers, dict):
            raise ValueError("run state 'layers' is missing or is not an object")
        return cls(
            run_id=str(raw.get("run_id", "")),
            tier=tier,
            mode=str(raw.get("mode", "DIFF")).upper(),
            no_fix=bool(raw.get("no_fix", False)),
            started_utc=str(raw.get("started_utc", "")),
            fixes_applied=int(raw.get("fixes_applied", 0) or 0),
            layers=layers,
        )


@dataclass
class CommitDecision:
    """Why a commit was allowed or refused, in terms the reader can act on."""

    allowed: bool
    reasons: list[str] = field(default_factory=list)
    run_id: str = ""


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write(state: PipelineState, root: str | pathlib.Path) -> pathlib.Path:
    path = state_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Write-then-replace. A crash mid-write must not leave a truncated state
    # file, because an unparseable state blocks every commit in the checkout
    # until somebody aborts by hand.
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state.to_json(), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def load(root: str | pathlib.Path = ".") -> PipelineState | None:
    """Return the open run, or None when no audit is in flight.

    Raises ValueError on a state file that exists but cannot be understood. The
    caller must treat that as a refusal rather than as "no run": a state file we
    cannot parse is evidence that a run started, and assuming it finished is the
    optimistic guess this module exists to remove.
    """
    path = state_path(root)
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
    return PipelineState.from_json(raw)


def start(
    tier: str,
    mode: str = "DIFF",
    *,
    no_fix: bool = False,
    run_id: str = "",
    root: str | pathlib.Path = ".",
) -> PipelineState:
    """Open a run at Step 0.6, once the tier is known."""
    tier = tier.upper()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r} (expected one of {', '.join(TIERS)})")
    state = PipelineState(
        run_id=run_id or _now().replace(":", "").replace("-", ""),
        tier=tier,
        mode=mode.upper(),
        no_fix=no_fix,
        started_utc=_now(),
    )
    _write(state, root)
    return state


def record(
    layer: str,
    *,
    status: str = "done",
    detail: str = "",
    fixes: int | None = None,
    root: str | pathlib.Path = ".",
) -> PipelineState:
    """Record that `layer` was reached and given a verdict."""
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r} (expected one of {', '.join(LAYERS)})")
    state = load(root)
    if state is None:
        raise ValueError(
            "no CCA run is open; run `python -m cca_checks pipeline start --tier <TIER>` "
            "at Step 0.6 before recording a layer"
        )
    state.layers[layer] = {"status": status, "detail": detail, "at": _now()}
    if fixes is not None:
        state.fixes_applied = int(fixes)
    _write(state, root)
    return state


def _append_log(root: str | pathlib.Path, line: str) -> pathlib.Path:
    log = state_path(root).parent / "EXECUTION_LOG.md"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("a", encoding="utf-8") as fh:
        fh.write(line if line.endswith("\n") else line + "\n")
    return log


def abort(reason: str, root: str | pathlib.Path = ".") -> pathlib.Path | None:
    """Close an open run WITHOUT satisfying its gates, leaving a trail.

    The trail is the whole point. A skip that has to be declared, named and dated
    is a different object from a skip that happens by forgetting, even when the
    code that ships is identical: the first one can be found later and counted.
    """
    path = state_path(root)
    if not path.exists():
        return None
    try:
        state = load(root)
        run_id = state.run_id if state else "unknown"
    except ValueError:
        # An unparseable state is exactly when an abort is most needed, so this
        # path must not raise. Record what we can and clear the block.
        run_id = "unparseable"
    log = _append_log(root, f"| {_now()} | run {run_id} | ABORTED | {reason} |")
    path.unlink()
    return log


def close(root: str | pathlib.Path = ".") -> pathlib.Path | None:
    """Retire a run that passed its gates, appending the outcome to the log."""
    path = state_path(root)
    if not path.exists():
        return None
    state = load(root)
    recorded = ", ".join(sorted(state.layers)) if state else ""
    log = _append_log(
        root,
        f"| {_now()} | run {state.run_id if state else '?'} | COMPLETE | "
        f"tier {state.tier if state else '?'} | layers: {recorded} |",
    )
    path.unlink()
    return log


def required_layers(state: PipelineState) -> tuple[str, ...]:
    """The layers this run must have recorded before it may commit."""
    required = list(_CORE_REQUIRED)
    if state.fixes_applied > 0:
        required.extend(_FIX_REQUIRED[state.tier])
    return tuple(required)


def verify_commit_allowed(state: PipelineState | None) -> CommitDecision:
    """Decide whether a commit may proceed against the open run."""
    if state is None:
        return CommitDecision(allowed=True, reasons=["no CCA run is open"])

    reasons: list[str] = []

    if state.no_fix:
        # `no-fix` means report, never edit. A commit during such a run is either
        # an unrelated change that should land after an abort, or the argument
        # being ignored, and the second is a correctness failure of the pipeline
        # rather than a nuisance.
        reasons.append(
            "this run was started with `no-fix`, which forbids editing and committing; "
            "abort the run first if this commit is unrelated"
        )

    for layer in required_layers(state):
        entry = state.layers.get(layer)
        if not entry:
            reasons.append(f"{layer} has no recorded verdict (tier {state.tier})")
        elif str(entry.get("status", "")).lower() in _TERMINAL_BAD_STATUS:
            reasons.append(f"{layer} is recorded as {entry.get('status')}")

    gate = state.layers.get("L6")
    if gate:
        verdict = str(gate.get("detail", "")).strip().upper()
        if not verdict:
            reasons.append(
                "L6 was recorded without a verdict; record it as "
                "--detail APPROVED, REVISE or BLOCKED"
            )
        elif verdict != _APPROVING_VERDICT:
            reasons.append(
                f"the L6 architect gate returned {verdict}, not {_APPROVING_VERDICT}"
            )

    return CommitDecision(allowed=not reasons, reasons=reasons, run_id=state.run_id)
