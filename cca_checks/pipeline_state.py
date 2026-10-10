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
the pipeline's steps are ~49KB of text (measured 2026-10-10:
git show origin/master:cca_checks/plugin/commands/audit-fix.md | wc -c) read once,
at the top of a run, and they then have to survive six to eleven subagents
returning long reports. By Step 2 they sit far behind the most recent tokens, while
the agent harness carries standing instructions that compete with them directly ("do not add further
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

import importlib.util
import json
import pathlib
from dataclasses import dataclass, field

__all__ = [
    "CommitDecision", "LAYERS", "PipelineState", "RecordRefused", "TIERS", "abort",
    "close", "load", "outstanding", "record", "required_layers", "start", "state_path",
    "verify_commit_allowed",
]


def _load_core():
    # By path, not by import: the core also has to load from a user's
    # .claude/hooks/ directory where no package exists, and one loader for both
    # places means one copy of the rules.
    path = pathlib.Path(__file__).resolve().parent / "plugin" / "hooks" / "cca_gate_core.py"
    spec = importlib.util.spec_from_file_location("cca_gate_core", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = _load_core()
LAYERS = core.LAYERS
TIERS = core.TIERS
_COUNT_KEYS = ("unique", "confirmed", "fp", "uncertain", "duplicate", "to_fix")


class RecordRefused(ValueError):
    """A layer that may not be recorded yet, or not with these numbers."""


def state_path(root: str | pathlib.Path = ".") -> pathlib.Path:
    return core.state_file(root)


@dataclass
class PipelineState:
    run_id: str
    tier: str
    mode: str = "DIFF"
    no_fix: bool = False
    started_utc: str = ""
    fixes_applied: int = 0
    layers: dict = field(default_factory=dict)
    schema: int = 1
    base_tree: str | None = None
    seq: int = 0
    stop_refusals: int = 0
    stop_logged: bool = False

    def to_json(self) -> dict:
        return {
            "schema": self.schema, "run_id": self.run_id, "tier": self.tier,
            "mode": self.mode, "no_fix": self.no_fix, "started_utc": self.started_utc,
            "fixes_applied": self.fixes_applied, "base_tree": self.base_tree,
            "seq": self.seq, "stop_refusals": self.stop_refusals,
            "stop_logged": self.stop_logged, "layers": self.layers,
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
            run_id=str(raw.get("run_id", "")), tier=tier,
            mode=str(raw.get("mode", "DIFF")).upper(),
            no_fix=bool(raw.get("no_fix", False)),
            started_utc=str(raw.get("started_utc", "")),
            fixes_applied=int(raw.get("fixes_applied", 0) or 0),
            layers=layers,
            schema=int(raw.get("schema", 1) or 1),
            base_tree=raw.get("base_tree"),
            seq=int(raw.get("seq", 0) or 0),
            stop_refusals=int(raw.get("stop_refusals", 0) or 0),
            stop_logged=bool(raw.get("stop_logged", False)),
        )

    @property
    def legacy(self) -> bool:
        return self.schema != core.SCHEMA


@dataclass
class CommitDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)
    run_id: str = ""


def load(root: str | pathlib.Path = ".") -> PipelineState | None:
    raw = core.read_state(root)
    return None if raw is None else PipelineState.from_json(raw)


def _write(state: PipelineState, root) -> None:
    core.write_state(root, state.to_json())


def start(tier: str, mode: str = "DIFF", *, no_fix: bool = False, run_id: str = "",
          root: str | pathlib.Path = ".") -> PipelineState:
    """Open a run: at Step 0.6, or at Step 0.4 for a hunt."""
    tier = tier.upper()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r} (expected one of {', '.join(TIERS)})")
    stamp = core.now()
    state = PipelineState(
        run_id=run_id or stamp.replace(":", "").replace("-", ""), tier=tier,
        mode=mode.upper(), no_fix=no_fix, started_utc=stamp,
        schema=core.SCHEMA, base_tree=core.fingerprint(root),
    )
    _write(state, root)
    return state


def _ledger_rows(root, run_id: str) -> tuple[int, int]:
    """(disposition rows for run_id, malformed lines). Outcome rows carry no
    `verdict` key and are not dispositions (Review Focus 4)."""
    path = pathlib.Path(root) / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    rows = malformed = 0
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0, 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(row, dict) and "verdict" in row and str(row.get("run_id")) == run_id:
            rows += 1
    return rows, malformed


def _need(counts: dict, key: str, layer: str) -> int:
    value = counts.get(key)
    if value is None:
        raise RecordRefused(f"{layer} needs --{key.replace('_', '-')} <N>")
    if int(value) < 0:
        raise RecordRefused(f"{layer} --{key.replace('_', '-')} cannot be negative")
    return int(value)


def _layer_count(state: PipelineState, layer: str, key: str) -> int | None:
    entry = state.layers.get(layer)
    if not isinstance(entry, dict):
        return None
    value = (entry.get("counts") or {}).get(key)
    return None if value is None else int(value)


def _check_counts(layer: str, state: PipelineState, counts: dict, root) -> None:
    if layer == "L2":
        _need(counts, "unique", layer)
    elif layer == "L2.5":
        total = sum(
            _need(counts, k, layer) for k in ("confirmed", "fp", "uncertain", "duplicate")
        )
        unique = _layer_count(state, "L2", "unique")
        if unique is not None and total != unique:
            raise RecordRefused(
                f"L2.5 accounts for {total} findings but L2 recorded {unique} unique; "
                "every consolidated finding needs exactly one verdict"
            )
        if state.tier != "FAST":
            rows, malformed = _ledger_rows(root, state.run_id)
            if rows != total:
                note = f" ({malformed} malformed line(s) skipped)" if malformed else ""
                raise RecordRefused(
                    f"the disposition ledger holds {rows} row(s) for run {state.run_id} but "
                    f"L2.5 recorded {total} verdicts{note}; append one row per verified "
                    "finding to .claude/audits/AUDITOR_SCORECARD.jsonl (Step 2.5b) with "
                    f'"run_id": "{state.run_id}"'
                )
    elif layer == "L3":
        to_fix = _need(counts, "to_fix", layer)
        confirmed = _layer_count(state, "L2.5", "confirmed")
        if confirmed is not None and to_fix > confirmed:
            raise RecordRefused(
                f"L3 --to-fix {to_fix} exceeds the {confirmed} findings L2.5 confirmed"
            )


def record(layer: str, *, status: str = "done", detail: str = "", fixes: int | None = None,
           counts: dict | None = None, source: str = "cli",
           root: str | pathlib.Path = ".") -> PipelineState:
    """Record that `layer` was reached and given a verdict.

    Raises RecordRefused when the layer is out of order, is a tool layer recorded
    by hand, or carries counts that do not agree with the run.
    """
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r} (expected one of {', '.join(LAYERS)})")
    state = load(root)
    if state is None:
        raise ValueError(
            "no CCA run is open; run `python -m cca_checks pipeline start --tier <TIER>` "
            "at Step 0.6 (Step 0.4 for a hunt) before recording a layer"
        )
    entry = {"status": status, "detail": detail, "at": core.now()}
    if not state.legacy:
        if layer in core.TOOL_LAYERS and source != "tool":
            raise RecordRefused(
                f"{layer} is recorded by its tool, not by hand: {core.TOOL_LAYERS[layer]}"
            )
        tree = core.fingerprint(root)
        raw = state.to_json()
        owed = core.fixes_owed(raw, tree) or (fixes or 0) > 0
        missing = [p for p in core.predecessors(layer, raw, owed) if p not in state.layers]
        if missing:
            raise RecordRefused(
                f"{layer} cannot be recorded before {', '.join(missing)}; re-read "
                f"{core.HEADINGS[missing[0]]} of audit-fix.md and run it first"
            )
        gate04 = state.layers.get("L0.4")
        if "L0.4" in core.predecessors(layer, raw, owed) and \
                str((gate04 or {}).get("detail", "")).strip().upper() != "PASS":
            raise RecordRefused(
                "L0.4 did not read PASS; a hunt that fails viability is aborted with "
                "`pipeline abort --reason`, never continued"
            )
        counts = {k: v for k, v in (counts or {}).items() if v is not None}
        _check_counts(layer, state, counts, root)
        state.seq += 1
        entry.update({"seq": state.seq, "source": source})
        if counts:
            entry["counts"] = counts
        if layer in core.FIX_VERDICT_LAYERS:
            entry["tree"] = tree
    state.layers[layer] = entry
    if fixes is not None:
        state.fixes_applied = int(fixes)
    _write(state, root)
    return state


def _tree(state: PipelineState, root) -> str | None:
    return None if state.legacy else core.fingerprint(root)


def required_layers(state: PipelineState, root: str | pathlib.Path = ".") -> tuple[str, ...]:
    if state.legacy:
        required = list(core.LEGACY_CORE_REQUIRED)
        if state.fixes_applied > 0:
            required.extend(core.LEGACY_FIX_REQUIRED[state.tier])
        return tuple(required)
    raw = state.to_json()
    return core.required_layers(raw, core.fixes_owed(raw, _tree(state, root)))


def outstanding(state: PipelineState, root: str | pathlib.Path = ".") -> list[str]:
    """What this run still owes before it may close. Legacy runs owe nothing here."""
    if state.legacy:
        return []
    return core.outstanding(state.to_json(), _tree(state, root))


def verify_commit_allowed(state: PipelineState | None,
                          root: str | pathlib.Path = ".") -> CommitDecision:
    if state is None:
        return CommitDecision(allowed=True, reasons=["no CCA run is open"])
    reasons = core.commit_reasons(state.to_json(), _tree(state, root))
    return CommitDecision(allowed=not reasons, reasons=reasons, run_id=state.run_id)


def abort(reason: str, root: str | pathlib.Path = ".") -> pathlib.Path | None:
    """Close an open run WITHOUT satisfying its gates, leaving a trail."""
    path = state_path(root)
    if not path.exists():
        return None
    try:
        state = load(root)
        run_id = state.run_id if state else "unknown"
    except ValueError:
        run_id = "unparseable"
    log = core.append_log(root, f"| {core.now()} | run {run_id} | ABORTED | {reason} |")
    path.unlink()
    return log


def close(root: str | pathlib.Path = ".") -> pathlib.Path | None:
    """Retire a run that is complete. An incomplete run is refused, not logged."""
    path = state_path(root)
    if not path.exists():
        return None
    state = load(root)
    missing = outstanding(state, root)
    if missing:
        raise RecordRefused(
            "this run is not complete: " + "; ".join(missing)
            + ". Finish it, or retire it with `python -m cca_checks pipeline abort --reason`."
        )
    recorded = ", ".join(sorted(state.layers))
    log = core.append_log(
        root,
        f"| {core.now()} | run {state.run_id} | COMPLETE | tier {state.tier} | "
        f"layers: {recorded} |",
    )
    path.unlink()
    return log
