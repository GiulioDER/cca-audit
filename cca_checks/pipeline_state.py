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
import os
import pathlib
from dataclasses import dataclass, field

__all__ = [
    "CommitDecision", "LAYERS", "OUTPUT_LAYERS", "PipelineState", "RecordRefused", "TIERS",
    "abort", "close", "load", "outstanding", "pause", "record", "required_layers", "resume",
    "start", "state_path", "verify_commit_allowed",
]


def _load_core():
    # By path, not by import: the core also has to load from a user's
    # .claude/hooks/ directory where no package exists, and one loader for both
    # places means one copy of the rules.
    path = pathlib.Path(__file__).resolve().parent / "plugin" / "hooks" / "cca_gate_core.py"
    spec = importlib.util.spec_from_file_location("cca_gate_core", path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load the gate core from {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


core = _load_core()
LAYERS = core.LAYERS
TIERS = core.TIERS
OUTPUT_LAYERS = core.OUTPUT_LAYERS

_PAUSED = (
    "the run is paused; run `pipeline resume` (`python -m cca_checks pipeline resume`) "
    "to continue it, or retire it with `pipeline abort --reason`"
)


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
    paused: dict | None = None

    def to_json(self) -> dict:
        return {
            "schema": self.schema, "run_id": self.run_id, "tier": self.tier,
            "mode": self.mode, "no_fix": self.no_fix, "started_utc": self.started_utc,
            "fixes_applied": self.fixes_applied, "base_tree": self.base_tree,
            "seq": self.seq, "stop_refusals": self.stop_refusals,
            "stop_logged": self.stop_logged, "paused": self.paused, "layers": self.layers,
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
            paused=raw.get("paused") if isinstance(raw.get("paused"), dict) else None,
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
    """Open a run: at Step 0.6, or at Step 0.4 for a hunt.

    Refused while a state file exists, readable or not. Overwriting an open run would
    re-baseline `base_tree` over its edits (so a run with no fixes recorded would stop
    owing L4 onward), drop an L0.4 recorded by a hunt, reset `stop_refusals`, and leave
    no trail. Retiring a run is `abort()`, which is explicit and logged.
    """
    tier = tier.upper()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r} (expected one of {', '.join(TIERS)})")
    if state_path(root).exists():
        raise RecordRefused(
            "a CCA run is already open in this checkout; starting another would replace it "
            "and discard what it recorded. Finish it with `python -m cca_checks pipeline "
            "close`, or retire it with `python -m cca_checks pipeline abort --reason "
            '"<why>"`'
        )
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


def _read_output(layer: str, output) -> bytes:
    """The bytes of a layer's output file, refused unless the file exists and is JSON."""
    what = OUTPUT_LAYERS[layer]
    if output is None or not str(output).strip():
        raise RecordRefused(
            f"{layer} needs --output <file>: a JSON file holding {what}. Write it under "
            ".claude/audits/ (a file elsewhere in the repository counts as an edit), then "
            "record again"
        )
    path = pathlib.Path(output)
    if not path.is_file():
        raise RecordRefused(
            f"{layer} --output {output} does not exist; write {what} to it as JSON, then "
            "record again"
        )
    try:
        data = path.read_bytes()
        json.loads(data)
    except (OSError, ValueError) as exc:
        raise RecordRefused(
            f"{layer} --output {output} is not readable JSON ({exc.__class__.__name__}: "
            f"{exc}); it must hold {what}"
        ) from exc
    return data


def _store_output(state: PipelineState, layer: str, data: bytes, root) -> str:
    """Copy a layer's output into the run's directory; return its posix path under root."""
    run_dir = state.run_id
    if not run_dir or run_dir in (".", "..") or pathlib.PurePath(run_dir).name != run_dir:
        raise RecordRefused(
            f"run id {state.run_id!r} cannot name a directory under .claude/audits/runs/; "
            "abort this run and start one with a plain --run-id"
        )
    rel = f".claude/audits/runs/{run_dir}/{layer}.json"
    dest = pathlib.Path(root) / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_name(dest.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, dest)
    return rel


def record(layer: str, *, status: str = "done", detail: str = "", fixes: int | None = None,
           counts: dict | None = None, source: str = "cli", output=None,
           root: str | pathlib.Path = ".") -> PipelineState:
    """Record that `layer` was reached and given a verdict.

    Raises RecordRefused when the run is paused, the layer is out of order, is a tool
    layer recorded by hand, carries counts that do not agree with the run, or (schema 2)
    is one of OUTPUT_LAYERS without a readable JSON `output` file. A layer outside
    OUTPUT_LAYERS given an `output` is refused too, so the prose cannot drift from the
    code by passing a flag that would be silently ignored.
    """
    if layer not in LAYERS:
        raise ValueError(f"unknown layer {layer!r} (expected one of {', '.join(LAYERS)})")
    state = load(root)
    if state is None:
        raise ValueError(
            "no CCA run is open; run `python -m cca_checks pipeline start --tier <TIER>` "
            "at Step 0.6 (Step 0.4 for a hunt) before recording a layer"
        )
    if state.paused:
        raise RecordRefused(_PAUSED)
    if output is not None and layer not in OUTPUT_LAYERS:
        raise RecordRefused(
            f"{layer} does not take --output; only {', '.join(OUTPUT_LAYERS)} store their "
            "work on disk"
        )
    entry = {"status": status, "detail": detail, "at": core.now()}
    data = None
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
        if layer in OUTPUT_LAYERS:
            data = _read_output(layer, output)
        state.seq += 1
        entry.update({"seq": state.seq, "source": source})
        if counts:
            entry["counts"] = counts
        if layer in core.FIX_VERDICT_LAYERS:
            entry["tree"] = tree
    if data is None and output is not None:
        # A 0.10.x run never requires the file, but one it is given is kept, not ignored.
        data = _read_output(layer, output)
    if data is not None:
        entry["output"] = _store_output(state, layer, data, root)
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
    """Retire a run that is complete. An incomplete or paused run is refused, not logged."""
    path = state_path(root)
    if not path.exists():
        return None
    state = load(root)
    if state is None:
        return None
    if state.paused:
        raise RecordRefused(_PAUSED)
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


def pause(reason: str, root: str | pathlib.Path = ".") -> PipelineState:
    """Pause the open run between two records, so a later session can resume it.

    The state learns about a layer only when it is recorded, so it cannot see auditors
    or edits still in flight. A pause is therefore allowed between any two records, and
    the prose says to pause only right after a `record` returned; by then every recorded
    layer that owns work (OUTPUT_LAYERS) has that work on disk.
    """
    state = load(root)
    if state is None:
        raise RecordRefused("no CCA run is open; there is nothing to pause")
    if state.legacy:
        raise RecordRefused(
            "this run was started by cca_checks 0.10.x, which cannot pause; finish it, or "
            "retire it with `pipeline abort --reason`"
        )
    if state.paused:
        raise RecordRefused(_PAUSED)
    state.paused = {"at": core.now(), "reason": reason, "tree": core.fingerprint(root)}
    _write(state, root)
    core.append_log(root, f"| {state.paused['at']} | run {state.run_id} | PAUSED | {reason} |")
    return state


def resume(root: str | pathlib.Path = ".") -> dict:
    """Resume a paused run and say where it stands.

    Returns the run id; whether the tree changed while paused (an unknown fingerprint
    counts as changed); the first required layer still owed and its heading; every
    recorded layer's output file in pipeline order; and what the run still owes by the
    usual rules, so a verdict made stale by an edit made while paused is listed.
    """
    state = load(root)
    if state is None:
        raise RecordRefused("no CCA run is open; there is nothing to resume")
    if not state.paused:
        raise RecordRefused("the run is not paused; there is nothing to resume")
    stored = state.paused.get("tree")
    tree = _tree(state, root)
    state.paused = None
    raw = state.to_json()
    nxt = None if state.legacy else core.next_owed(raw, tree)
    result = {
        "resumed": state.run_id,
        "tree_changed": tree is None or stored is None or tree != stored,
        "next": nxt,
        "heading": core.HEADINGS[nxt] if nxt else None,
        "outputs": {
            layer: state.layers[layer]["output"] for layer in LAYERS
            if isinstance(state.layers.get(layer), dict) and state.layers[layer].get("output")
        },
        "outstanding": [] if state.legacy else core.outstanding(raw, tree),
    }
    _write(state, root)
    changed = ", tree changed while paused" if result["tree_changed"] else ""
    core.append_log(
        root, f"| {core.now()} | run {state.run_id} | RESUMED | next {nxt or 'none'}{changed} |"
    )
    return result
