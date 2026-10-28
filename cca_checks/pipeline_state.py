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
import re
import subprocess
import sys
from dataclasses import dataclass, field

from cca_checks import deferred, layer_outputs

__all__ = [
    "CommitDecision", "LAYERS", "MODES", "OUTPUT_LAYERS", "PipelineState", "RecordRefused",
    "TIERS", "abort", "close", "current_tree", "deferred_report", "load", "outstanding",
    "pause", "record", "required_layers", "resume", "start", "state_path",
    "verify_commit_allowed", "write_deferred",
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
MODES = core.MODES
OUTPUT_LAYERS = core.OUTPUT_LAYERS
#: A run id names a directory, so only characters every filesystem accepts.
_RUN_ID = re.compile(r"[A-Za-z0-9._-]+")

_PAUSED = (
    "the run is paused; run `pipeline resume` (`python -m cca_checks pipeline resume`) "
    "to continue it, or retire it with `pipeline abort --reason`"
)


class RecordRefused(ValueError):
    """A layer that may not be recorded yet, or not with these numbers."""


class _Unset:
    """Marks a `tree` argument the caller did not pass: None is a real fingerprint answer."""


#: Default for every `tree=` parameter. The fingerprint runs `git add -A` over a copy of
#: the index, the costliest thing these functions do, so a caller answering several
#: questions about one run computes it once with `current_tree` and passes it to each.
_UNSET = _Unset()


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
    base_commit: str | None = None
    parent: str | None = None
    #: The Claude Code session that opened the run (`CLAUDE_CODE_SESSION_ID` at `start`), so
    #: the stop guard holds that session only. None when started outside Claude Code.
    owner_session: str | None = None

    def to_json(self) -> dict:
        return {
            "schema": self.schema, "run_id": self.run_id, "tier": self.tier,
            "mode": self.mode, "no_fix": self.no_fix, "started_utc": self.started_utc,
            "fixes_applied": self.fixes_applied, "base_tree": self.base_tree,
            "seq": self.seq, "stop_refusals": self.stop_refusals,
            "stop_logged": self.stop_logged, "paused": self.paused, "layers": self.layers,
            "base_commit": self.base_commit, "parent": self.parent,
            "owner_session": self.owner_session,
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
            base_commit=raw.get("base_commit") if isinstance(raw.get("base_commit"), str) else None,
            parent=raw.get("parent") if isinstance(raw.get("parent"), str) else None,
            owner_session=(
                raw.get("owner_session") if isinstance(raw.get("owner_session"), str) else None
            ),
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


def _git(root, *args: str) -> subprocess.CompletedProcess[bytes] | None:
    """Run git in `root` with the gate's trusted git; None when it cannot run."""
    git = core._git_exe(core._top_hint(str(pathlib.Path(root).resolve())))
    if git is None:
        return None
    try:
        return subprocess.run([git, "-C", str(root), *args], capture_output=True,
                              timeout=core.FINGERPRINT_TIMEOUT_S, check=False)
    except (OSError, subprocess.SubprocessError):
        return None


def _commit_of(root, rev: str) -> str | None:
    """The full sha `rev` names; None when it names no commit."""
    done = _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}")
    if done is None or done.returncode != 0:
        return None
    return done.stdout.decode("utf-8", "replace").strip() or None


def _is_ancestor(root, sha: str) -> bool:
    """True when `sha` is an ancestor of HEAD. A value starting with `-` is never one: the
    value can come from a summary file, and git would read it as an option."""
    if sha.startswith("-"):
        return False
    done = _git(root, "merge-base", "--is-ancestor", sha, "HEAD")
    return done is not None and done.returncode == 0


_NO_DEFERRAL = (
    "a DEFERRED run cannot defer: its findings are the deferred ones, so each is verified and "
    "then fixed, or left UNCERTAIN for a human"
)
#: What `--from commit:<sha>` may hold: an abbreviated or full object name, never a revision
#: expression. `HEAD`, `-x`, `main~2` and `a..b` are all things `git rev-parse` would accept
#: or read as an option, so the form is checked before git sees it. Up to 64 characters: a
#: SHA-256 repository names its objects with 64.
_COMMIT_SHA = re.compile(r"[0-9A-Fa-f]{7,64}")


def _deferred_parent(tier: str, no_fix: bool, parent: str, root) -> tuple[str, str | None]:
    """Check the run a DEFERRED run follows; return (parent as stored, base_commit).

    A run id is held to every rule in `deferred.why_not_offered`. `commit:<sha>` is for a run
    that left no summary, so a sha that is the commit of a run that did leave one is refused:
    it would otherwise skip those rules (a pool already taken, a hunt, no-fix or deferred
    parent). The inherited base is resolved here, not copied, because the summary is a file a
    person can edit. Unreadable summaries refuse the start (fail closed): one of them might be
    the child that already took the pool.
    """
    if not parent:
        raise RecordRefused(
            "--mode DEFERRED needs --from <run_id>: the completed run whose deferred findings "
            "this run takes (`python -m cca_checks pipeline deferred` names it)"
        )
    if tier == "FAST":
        raise RecordRefused(
            "a DEFERRED run cannot be FAST: FAST verifies P1 findings only, and every "
            "deferred finding is verified before it is edited; start it STANDARD or DEEP"
        )
    if no_fix:
        raise RecordRefused(
            "a DEFERRED run exists to fix; to list the deferred findings without edits, run "
            "`python -m cca_checks pipeline deferred` instead"
        )
    summaries, unreadable = deferred.load_summaries(root)
    if unreadable:
        raise RecordRefused(
            f"{len(unreadable)} run summary file(s) under .claude/audits/runs/ cannot be read "
            f"({', '.join(unreadable)}), so it cannot be known whether a pool was already "
            "taken; fix or move them, then start again"
        )
    if parent.startswith("commit:"):
        rev = parent.removeprefix("commit:")
        if not _COMMIT_SHA.fullmatch(rev):
            raise RecordRefused(
                f"--from {parent} is not of the form commit:<sha>, where <sha> is 7 to 64 "
                "hexadecimal characters"
            )
        sha = _commit_of(root, rev)
        if sha is None or not _is_ancestor(root, sha):
            raise RecordRefused(
                f"--from {parent} does not name a commit that is an ancestor of HEAD"
            )
        owners = [s["run_id"] for s in summaries if s.get("commit") == sha]
        if owners:
            raise RecordRefused(
                f"--from {parent} is the commit of completed run {', '.join(owners)}, which has "
                f"a summary; use --from {owners[0]} so that run's own rules apply"
            )
        stored = f"commit:{sha}"
        child = deferred.consumed_by(stored, summaries)
        if child:
            raise RecordRefused(f"deferred run {child} already took the findings of {stored}")
        return stored, None
    summary = next((s for s in summaries if s["run_id"] == parent), None)
    if summary is None:
        raise RecordRefused(
            f"no completed run {parent!r} has a summary under .claude/audits/runs/; "
            "`pipeline deferred` names the runs that do, and a run that predates summaries "
            "is taken by the commit-message fallback of audit-fix.md § Second Pass"
        )
    why_not = deferred.why_not_offered(summary, summaries)
    if why_not:
        raise RecordRefused(f"run {parent} cannot be followed up: {why_not}")
    inherited = summary.get("base_commit")
    base_commit = _commit_of(root, inherited) if isinstance(inherited, str) else None
    return parent, base_commit


def _capture_pending(state: PipelineState, root) -> None:
    """Save the closed run for the bug ledger; a failure is a warning, never a refusal.

    The ledger records a run only after its pull request merges (`bugs resolve`), and by
    then the worktree may be gone, so the run's files are copied out now.
    """
    try:
        from .bugs import pending
        gate = state.layers.get("L6")
        pending.capture(root, state.run_id,
                        str(gate.get("detail", "")) if isinstance(gate, dict) else "")
    except Exception as exc:  # noqa: BLE001 (see the docstring)
        print(f"warning: could not save this run for the bug ledger "
              f"({exc.__class__.__name__}: {exc})", file=sys.stderr)


def _registry(action: str, *args) -> None:
    """Update the open run registry; a failure is a warning, never a refusal.

    The registry only helps the hooks find a run opened below the session's directory.
    A run it does not list is still found from its own directory, so losing an update
    costs reach, not safety, and must not stop the pipeline.
    """
    try:
        getattr(core, action)(*args)
    except Exception as exc:  # noqa: BLE001 (see the docstring)
        # Not `core.registry_path()` here: it is what may have failed (no home directory).
        print(
            f"warning: could not update the CCA run registry ({exc.__class__.__name__}: "
            f"{exc}); the hooks will find this run only from its own directory",
            file=sys.stderr,
        )


def start(tier: str, mode: str = "DIFF", *, no_fix: bool = False, run_id: str = "",
          base: str = "HEAD", parent: str = "",
          root: str | pathlib.Path = ".") -> PipelineState:
    """Open a run: at Step 0.6, or at Step 0.4 for a hunt.

    Refused while a state file exists, readable or not. Overwriting an open run would
    re-baseline `base_tree` over its edits (so a run with no fixes recorded would stop
    owing L4 onward), drop an L0.4 recorded by a hunt, reset `stop_refusals`, and leave
    no trail. Retiring a run is `abort()`, which is explicit and logged. `base` is the
    revision the audited diff is taken against; its sha is stored as `base_commit`. A run id
    that already names a completed run (one holding a `summary.json`) is refused, so the
    stored work a later deferred pass reads is never overwritten; so is any id an earlier run
    left stored outputs or ledger rows under, whose rows would count toward this run's L2.5.

    A DEFERRED run names the run it follows in `parent` (a run id, or `commit:<sha>` for a
    run that left no summary). `_deferred_parent` checks it; the run then takes that run's
    `base_commit` (None when it no longer resolves) rather than resolving `base`, and `parent`
    is stored as given by that check. `parent` is refused in any other mode, and `base` is
    refused in DEFERRED mode.
    """
    tier = tier.upper()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r} (expected one of {', '.join(TIERS)})")
    mode = mode.upper()
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (expected one of {', '.join(MODES)})")
    if parent and mode != "DEFERRED":
        raise RecordRefused(
            f"--from names the run a DEFERRED run follows up; it is refused with --mode {mode}"
        )
    if mode == "DEFERRED" and base != "HEAD":
        raise RecordRefused(
            f"--base {base} is refused with --mode DEFERRED: the run takes its parent's base, "
            "so there is no diff base to choose"
        )
    if run_id and (not _RUN_ID.fullmatch(run_id) or run_id in (".", "..")):
        raise RecordRefused(
            f"run id {run_id!r} is refused: it names the directory "
            ".claude/audits/runs/<run_id>/, so use only letters, digits, `.`, `_` and `-`"
        )
    if state_path(root).exists():
        raise RecordRefused(
            "a CCA run is already open in this checkout; starting another would replace it "
            "and discard what it recorded. Finish it with `python -m cca_checks pipeline "
            "close`, or retire it with `python -m cca_checks pipeline abort --reason "
            '"<why>"`'
        )
    stamp = core.now()
    new_id = run_id or stamp.replace(":", "").replace("-", "")
    if (deferred.runs_dir(root) / new_id / "summary.json").exists():
        raise RecordRefused(
            f"run id {new_id!r} already names a completed run under .claude/audits/runs/; "
            "pick another --run-id, so that run's stored work is not overwritten"
        )
    if (deferred.runs_dir(root) / new_id).exists() or _ledger_mentions(root, new_id):
        # A reused id would count the earlier run's ledger rows toward this run's L2.5
        # and overwrite its stored outputs.
        raise RecordRefused(
            f"run id {new_id!r} was used by an earlier run (its outputs or ledger rows "
            "exist); start with a new --run-id"
        )
    if mode == "DEFERRED":
        stored_parent, base_commit = _deferred_parent(tier, no_fix, parent, root)
    else:
        stored_parent = None
        base_commit = _commit_of(root, base)
        if base_commit is None and base != "HEAD":
            raise RecordRefused(
                f"--base {base} does not name a commit; pass the revision the audited diff "
                "is taken against (HEAD, or HEAD~N for `commit N`)"
            )
    state = PipelineState(
        run_id=new_id, tier=tier,
        mode=mode, no_fix=no_fix, started_utc=stamp,
        schema=core.SCHEMA, base_tree=core.fingerprint(root), base_commit=base_commit,
        parent=stored_parent,
        owner_session=os.environ.get("CLAUDE_CODE_SESSION_ID") or None,
    )
    _write(state, root)
    _registry("register", root, state.run_id)
    return state


#: Ledger verdict to the L2.5 count it tallies against. DEFERRED is absent on purpose: a
#: finding passed on unverified received no verdict, so it gets no ledger row. STALE is
#: present: a DEFERRED run's verifier gives it, and it gets a row like any other verdict.
_VERDICT_COUNTS = {"CONFIRMED": "confirmed", "FALSE_POSITIVE": "fp",
                   "UNCERTAIN": "uncertain", "DUPLICATE": "duplicate", "STALE": "stale",
                   "PRIOR_FP": "prior_fp"}

#: Why fp-check called a finding FALSE_POSITIVE (Step 2.5b). Only `not_real` (criteria a and c)
#: may become a suppressing record: an out-of-scope finding sits on unchanged code by
#: construction, so its record would suppress a real defect the first time that code is in
#: scope, and a settled one is the settled-decisions block's.
FP_REASONS = ("not_real", "out_of_scope", "settled")
#: Fields Step 2.5b adds to a FALSE_POSITIVE row, beside the common ones.
FP_FIELDS = ("finding_id", "file", "line", "symbol", "code_sha", "claim", "evidence_refs",
             "fp_reason")
#: Run files the false-positive path reads, under .claude/audits/runs/<run_id>/.
FP_ANCHORS = "fp_anchors.json"
FP_RECORDS = "fp-records.json"
L6_DECISIONS = ("ACCEPT", "REJECT")


LEDGER = ".claude/audits/AUDITOR_SCORECARD.jsonl"


def ledger_path(root: str | pathlib.Path = ".") -> pathlib.Path:
    return pathlib.Path(root) / LEDGER


def ledger_rows(root: str | pathlib.Path = ".") -> tuple[list[dict], int]:
    """(rows of the disposition ledger, malformed lines); the one reader every gate uses."""
    return _ledger(root)


def _ledger(root) -> tuple[list[dict], int]:
    """(rows of the disposition ledger, malformed lines).

    Read as `utf-8-sig`: a ledger created by Windows PowerShell 5.1 starts with a BOM, which
    would make its first row malformed and refuse L2.5.
    """
    path = ledger_path(root)
    rows: list[dict] = []
    malformed = 0
    try:
        lines = path.read_text(encoding="utf-8-sig").splitlines()
    except OSError:
        return [], 0
    for line in lines:
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows, malformed


def _ledger_mentions(root, run_id: str) -> bool:
    return any(str(row.get("run_id")) == run_id for row in _ledger(root)[0])


def _ledger_rows(root, run_id: str) -> tuple[dict[str, int], int]:
    """(disposition rows for run_id tallied by verdict, malformed lines). Outcome rows
    carry no `verdict` key and are not dispositions (Review Focus 4)."""
    rows, malformed = _ledger(root)
    tally: dict[str, int] = {}
    for row in rows:
        if "verdict" in row and str(row.get("run_id")) == run_id:
            verdict = str(row.get("verdict"))
            tally[verdict] = tally.get(verdict, 0) + 1
    return tally, malformed


def _verdict_rows(root, run_id: str, verdict: str) -> list[dict]:
    """The disposition rows of `run_id` that carry `verdict`, in ledger order."""
    return [row for row in _ledger(root)[0]
            if str(row.get("run_id")) == run_id and row.get("verdict") == verdict]


def _check_fp_rows(rows: list[dict]) -> None:
    """Refuse a FALSE_POSITIVE row without the fields a suppressing record is built from.

    The anchor fields are checked against the code by `_fp_anchors`; here only their presence,
    and the reason code, which decides whether the row may ever suppress anything.
    """
    for n, row in enumerate(rows, 1):
        name = row.get("finding_id") if isinstance(row.get("finding_id"), str) else f"#{n}"
        missing = [k for k in FP_FIELDS if k not in row]
        if missing:
            raise RecordRefused(
                f"FALSE_POSITIVE row {name} lacks {', '.join(missing)}; Step 2.5b gives every "
                f"false positive {', '.join(FP_FIELDS)}"
            )
        if row["fp_reason"] not in FP_REASONS:
            raise RecordRefused(
                f"FALSE_POSITIVE row {name} has fp_reason {row['fp_reason']!r}; expected one of "
                f"{', '.join(FP_REASONS)} (Step 2.5b)"
            )
        refs = row["evidence_refs"]
        if not (isinstance(row["finding_id"], str) and row["finding_id"]
                and isinstance(row["claim"], str) and row["claim"].strip()
                and isinstance(refs, list) and all(isinstance(r, str) for r in refs)):
            raise RecordRefused(
                f"FALSE_POSITIVE row {name} needs a non-empty finding_id and claim, and "
                "evidence_refs as a list of \"file:line\" strings"
            )


def _ref(ref: str) -> tuple[str, int]:
    """("file", line) from an evidence ref "file:line"; a ref with no line is file level."""
    file, sep, line = ref.rpartition(":")
    if sep and line.strip().isdigit() and file:
        return file, int(line)
    return ref, 0


def _fp_anchors(state: PipelineState, root) -> dict[str, dict]:
    """Anchor every false positive of this run over the code it was judged on, now, at L2.5.

    Returns finding id to `{"anchors": [...], "errors": [...], "finding": {...}}`: the finding's
    own place first, then each evidence location, so a record built from it lapses when either
    changes, and the finding's `category`, `priority` and `high_stakes` as L2 recorded them,
    which the record takes rather than the row's. A row is refused when it names no finding of
    L2, sits at another place than L2's finding, or gives a `symbol` and `code_sha` that disagree
    with the code: those are copied from `python -m cca_checks bugs anchors --at <file>:<line>`,
    never written by hand, because a record that suppresses must pin the code it was judged on.
    """
    from .bugs.anchors import anchor_for

    try:
        l2 = {f["id"]: f for f in _stored_l2(state, root)}
    except layer_outputs.ShapeError as exc:
        raise RecordRefused(str(exc)) from exc
    out: dict[str, dict] = {}
    for row in _verdict_rows(root, state.run_id, "FALSE_POSITIVE"):
        finding = row["finding_id"]
        found = l2.get(finding)
        if found is None:
            raise RecordRefused(
                f"FALSE_POSITIVE row {finding} names no finding of L2; finding_id is the L2 id"
            )
        if (str(row["file"]).replace("\\", "/"), str(row["line"])) != \
                (str(found.get("file")).replace("\\", "/"), str(found.get("line"))):
            raise RecordRefused(
                f"FALSE_POSITIVE row {finding} is at {row['file']}:{row['line']}, but L2 put "
                f"{finding} at {found.get('file')}:{found.get('line')}; file and line are the "
                "finding's own place, and a mitigation's place goes in evidence_refs"
            )
        facts: dict = {"anchors": [], "errors": [], "finding": {
            "category": found.get("category"), "priority": found.get("priority"),
            "high_stakes": found.get("high_stakes") is not False}}
        places = [(row["file"], row["line"]), *(_ref(r) for r in row["evidence_refs"])]
        for index, (file, line) in enumerate(places):
            try:
                number = max(int(line or 0), 0)
                anchor = anchor_for(pathlib.Path(root), str(file), number).to_json()
            except (OSError, ValueError, TypeError) as exc:
                anchor = None
                facts["errors"].append(f"{file}:{line}: {type(exc).__name__}")
            if index == 0:
                want = (anchor or {}).get("symbol"), (anchor or {}).get("code_sha")
                if (row.get("symbol"), row.get("code_sha")) != want:
                    raise RecordRefused(
                        f"FALSE_POSITIVE row {finding} gives symbol {row.get('symbol')!r} and "
                        f"code_sha {row.get('code_sha')!r}, but {file}:{line} is {want[0]!r} "
                        f"{want[1]!r} now; copy both from `python -m cca_checks bugs anchors "
                        f"--at {file}:{line}`"
                    )
            if anchor is not None and anchor not in facts["anchors"]:
                facts["anchors"].append(anchor)
        out[finding] = facts
    return out


def _prior_fp_matches(state: PipelineState, root) -> dict[str, dict]:
    """Finding id to the record that suppresses it, computed here, never read from a file.

    The matcher runs again over L2 as it was recorded, the saved `recall_records` answer of this
    run (FP_RECORDS) and the checkout, with the ledger's retractions and the switch read now. So
    a match file left from an earlier L2, a hand-written one, or one computed from an edited L2
    file suppresses nothing the gate does not also derive. Without a saved answer nothing is
    suppressed.
    """
    path = deferred.runs_dir(root) / state.run_id / FP_RECORDS
    if state.mode != "DIFF" or not path.exists():
        # Step 2.4 runs in DIFF mode only: a hunt reads no record of the user's own projects,
        # and a deferred run's pool was already through fp-check's verdicts once.
        return {}
    from .bugs import envelope, match_fp, query_anchors
    from .bugs.gitread import GitError, remote_repo
    from .bugs.store import bugs_dir

    _, value, why = envelope.load(path)
    if why:
        raise RecordRefused(
            f"{path.as_posix()} is not a recall_records answer ({why}); save the raw answer "
            "again, or delete the file to suppress nothing"
        )
    try:
        l2 = _stored_l2(state, root)
    except layer_outputs.ShapeError as exc:
        raise RecordRefused(str(exc)) from exc
    try:
        repo = remote_repo(pathlib.Path(root))
    except (OSError, GitError):
        repo = None
    retracted, known, blocked = match_fp.ledger_facts(bugs_dir())
    matches = match_fp.match(query_anchors.locate(pathlib.Path(root), l2), value, repo=repo,
                             retracted=retracted, local=query_anchors.local_sha(pathlib.Path(root)),
                             known=known, blocked=blocked)
    return {m.finding_id: m.to_json() for m in matches if m.action == match_fp.SUPPRESS}


def _check_prior_fp(state: PipelineState, l25: list, root) -> None:
    """PRIOR_FP is the matcher's verdict, never the orchestrator's: refuse any disagreement.

    Each PRIOR_FP in the L2.5 file must be a suppression `_prior_fp_matches` derives, carrying
    the same `record_id`, and each such suppression must be a PRIOR_FP: a suppression is kept
    visible, and no finding is suppressed by hand. On STANDARD and DEEP each PRIOR_FP ledger row
    names the same record in `prior_fp_record`.
    """
    suppress = _prior_fp_matches(state, root)
    given = {r["id"]: r for r in l25 if isinstance(r, dict) and r.get("verdict") == "PRIOR_FP"}
    unmatched = sorted(set(given) - set(suppress))
    dropped = sorted(set(suppress) - set(given))
    wrong = sorted(i for i in set(given) & set(suppress)
                   if given[i].get("record_id") != suppress[i].get("record_id"))
    problems = ([f"{', '.join(unmatched)} is PRIOR_FP but the matcher did not suppress it"]
                if unmatched else []) + \
               ([f"the matcher suppressed {', '.join(dropped)}, which L2.5 does not mark "
                 "PRIOR_FP"] if dropped else []) + \
               ([f"{', '.join(wrong)} names a record_id other than the matcher's"]
                if wrong else [])
    if not problems and state.tier != "FAST":
        rows = {str(r.get("finding_id")): r
                for r in _verdict_rows(root, state.run_id, "PRIOR_FP")}
        bad = sorted(i for i in given if (rows.get(i) or {}).get("prior_fp_record")
                     != suppress[i].get("record_id"))
        if bad:
            problems.append(f"the PRIOR_FP ledger rows of {', '.join(bad)} need finding_id "
                            "and prior_fp_record equal to the matcher's record_id")
    if problems:
        raise RecordRefused(
            "; ".join(problems) + f". PRIOR_FP comes only from the matcher, which record "
            f"L2.5 runs again over L2 and .claude/audits/runs/{state.run_id}/{FP_RECORDS}; "
            f"`python -m cca_checks bugs match-fp` shows what it decides"
        )


def _check_l6(state: PipelineState, detail: str, value: object | None, root) -> None:
    """Refuse an L6 whose false-positive acceptance is missing or malformed.

    The architect returns ACCEPT or REJECT with a reason for every FALSE_POSITIVE of L2.5, in
    `{"fp_acceptance": [{"id", "decision", "reason"}]}`. An APPROVED verdict needs one entry per
    false positive: only an accepted one, in an APPROVED run, can become a suppressing record.
    """
    l25 = _stored_json(state, "L2.5", root)
    fps = [r["id"] for r in l25 if isinstance(r, dict) and r.get("verdict") == "FALSE_POSITIVE"
           and isinstance(r.get("id"), str)] if isinstance(l25, list) else []
    approved = detail.strip().upper() == core.APPROVING_VERDICT
    if value is None:
        if approved and fps:
            raise RecordRefused(
                f"L6 APPROVED needs --output with the architect's ACCEPT or REJECT for each "
                f"false positive ({', '.join(fps)}): a JSON object "
                '{"fp_acceptance": [{"id", "decision", "reason"}]} under .claude/audits/'
            )
        return
    entries = value.get("fp_acceptance") if isinstance(value, dict) else None
    if not isinstance(entries, list):
        raise RecordRefused('L6\'s file must be a JSON object with an "fp_acceptance" array')
    seen: list[str] = []
    for n, entry in enumerate(entries):
        if not (isinstance(entry, dict) and isinstance(entry.get("id"), str)
                and entry.get("decision") in L6_DECISIONS
                and isinstance(entry.get("reason"), str) and entry["reason"].strip()):
            raise RecordRefused(
                f"L6 fp_acceptance entry {n} needs an id, a decision of ACCEPT or REJECT and a "
                "non-empty reason"
            )
        if entry["id"] not in fps:
            raise RecordRefused(
                f"L6 accepts or rejects {entry['id']}, which L2.5 did not mark FALSE_POSITIVE"
            )
        if entry["id"] in seen:
            raise RecordRefused(f"L6 decides {entry['id']} more than once")
        seen.append(entry["id"])
    missing = [i for i in fps if i not in seen]
    if approved and missing:
        raise RecordRefused(
            f"L6 APPROVED has no ACCEPT or REJECT for {', '.join(missing)}; the architect "
            "decides every false positive"
        )


def disputed_fps(state: PipelineState, root) -> list[str]:
    """The false positives the architect rejected: UNCERTAIN, for a human."""
    value = _stored_json(state, "L6", root)
    entries = value.get("fp_acceptance") if isinstance(value, dict) else None
    return [e["id"] for e in entries or [] if isinstance(e, dict)
            and e.get("decision") == "REJECT" and isinstance(e.get("id"), str)]


def _need(counts: dict, key: str, layer: str) -> int:
    value = counts.get(key)
    if value is None:
        raise RecordRefused(f"{layer} needs --{key.replace('_', '-')} <N>")
    if int(value) < 0:
        raise RecordRefused(f"{layer} --{key.replace('_', '-')} cannot be negative")
    return int(value)


def _optional(counts: dict, key: str, layer: str) -> int:
    value = counts.get(key)
    if value is None:
        return 0
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
        given = {
            k: _need(counts, k, layer) for k in ("confirmed", "fp", "uncertain", "duplicate")
        }
        # Findings passed on without a verdict (P3, and P2 on FAST): Step 2.5 verifies only
        # P1/P2, so without this count every run with a P3 was refused here. The L2.5 file
        # marks each of them DEFERRED, and the ledger holds no row for them.
        unverified = _optional(counts, "unverified", layer)
        given["stale"] = _optional(counts, "stale", layer)
        given["prior_fp"] = _optional(counts, "prior_fp", layer)
        if unverified and state.mode == "DEFERRED":
            raise RecordRefused(_NO_DEFERRAL)
        if given["stale"] and state.mode != "DEFERRED":
            raise RecordRefused(
                "--stale is for a DEFERRED run only: STALE means the code moved on since the "
                "run that deferred the finding, and a first run has no earlier run"
            )
        judged = sum(given.values())
        total = judged + unverified
        unique = _layer_count(state, "L2", "unique")
        if unique is not None and total != unique:
            raise RecordRefused(
                f"L2.5 accounts for {total} findings ({judged} verdicts, {unverified} "
                f"unverified) but L2 recorded {unique} unique; every consolidated finding "
                "needs exactly one verdict, or is counted with --unverified (and marked "
                "DEFERRED in the L2.5 file) when Step 2.5 passes it on unverified"
            )
        if state.tier != "FAST":
            tally, malformed = _ledger_rows(root, state.run_id)
            rows = sum(tally.values())
            note = f" ({malformed} malformed line(s) skipped)" if malformed else ""
            if rows != judged:
                raise RecordRefused(
                    f"the disposition ledger holds {rows} row(s) for run {state.run_id} but "
                    f"L2.5 recorded {judged} verdicts{note}; append one row per verified "
                    "finding (a DEFERRED finding gets none) to "
                    ".claude/audits/AUDITOR_SCORECARD.jsonl (Step 2.5b) with "
                    f'"run_id": "{state.run_id}"'
                )
            unknown = sorted(set(tally) - set(_VERDICT_COUNTS))
            mismatched = [
                f"{verdict}: ledger {tally.get(verdict, 0)}, --{key} {given[key]}"
                for verdict, key in _VERDICT_COUNTS.items()
                if tally.get(verdict, 0) != given[key]
            ]
            if unknown or mismatched:
                detail = "; ".join(
                    mismatched + [f"unknown verdict {v!r}" for v in unknown]
                )
                raise RecordRefused(
                    f"the disposition ledger for run {state.run_id} disagrees with the L2.5 "
                    f"counts ({detail}){note}; each ledger row's verdict must be one of "
                    f"{', '.join(_VERDICT_COUNTS)}"
                )
            _check_fp_rows(_verdict_rows(root, state.run_id, "FALSE_POSITIVE"))
    elif layer == "L3":
        to_fix = _need(counts, "to_fix", layer)
        # `n_deferred`, not `deferred`: that name is kept free for the deferred-pass module
        # this file imports.
        n_deferred = _optional(counts, "deferred", layer)
        if n_deferred and state.mode == "DEFERRED":
            raise RecordRefused(_NO_DEFERRAL)
        confirmed = _layer_count(state, "L2.5", "confirmed")
        if confirmed is not None and to_fix + n_deferred != confirmed:
            raise RecordRefused(
                f"L3 plans {to_fix} (--to-fix) and defers {n_deferred} (--deferred) but L2.5 "
                f"confirmed {confirmed}; every confirmed finding is either planned or deferred"
            )


def _read_output(layer: str, output, root) -> bytes:
    """The bytes of a layer's output file.

    Refused unless the file lies inside `<root>/.claude/audits/` (the one place the tree
    fingerprint ignores: a file anywhere else in the working tree is an edit, which makes
    L4 onward owed), exists, and is JSON.
    """
    what = OUTPUT_LAYERS.get(layer, "the architect's ACCEPT or REJECT per false positive")
    where = f".claude/audits/out/{layer}.json"
    if output is None or not str(output).strip():
        raise RecordRefused(
            f"{layer} needs --output <file>: a JSON file holding {what}. Write it under "
            f".claude/audits/ (for example {where}), then record again"
        )
    path = pathlib.Path(output)
    if not path.is_absolute():
        # Relative to the run root, like every other path the run names, not to the cwd.
        path = pathlib.Path(root) / path
    audits = pathlib.Path(root) / core.EXCLUDED
    if not core._inside(str(path), str(audits)):
        raise RecordRefused(
            f"{layer} --output {output} is outside .claude/audits/; write it there (for "
            f"example {where}) and record again. A file anywhere else in the working tree "
            "changes the tree fingerprint, which makes L4 onward owed"
        )
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


def _stored_json(state: PipelineState, layer: str, root) -> object | None:
    """The work file `layer` stored in this run, parsed; None when absent or unreadable.

    Parsed from bytes, as `_read_output` parsed it: `json.loads` on bytes accepts a UTF-8 BOM
    (Windows PowerShell 5.1 writes one) and on text it does not, so reading the stored copy
    as text would refuse a file `record` had accepted.
    """
    entry = state.layers.get(layer)
    rel = entry.get("output") if isinstance(entry, dict) else None
    if not rel:
        return None
    try:
        return json.loads((pathlib.Path(root) / rel).read_bytes())
    except (OSError, ValueError):
        return None


def _stored_l2(state: PipelineState, root) -> list:
    """L2's stored findings, held to the shape L2 is now recorded in.

    A run recorded by an earlier version stored whatever it was given, and a later layer
    reads these ids and priorities back, so the refusal names the stored file.
    """
    l2 = _stored_json(state, "L2", root)
    if not isinstance(l2, list):
        raise layer_outputs.ShapeError("L2's stored file is missing or unreadable; record L2 again")
    try:
        return layer_outputs.l2_findings(l2, len(l2))
    except layer_outputs.ShapeError as exc:
        raise layer_outputs.ShapeError(
            f"L2's stored file does not have the finding shape ({exc}); record L2 again"
        ) from exc


def _stored_l25(state: PipelineState, root, l2_ids: list) -> dict[str, str]:
    """L2.5's stored verdicts (id to verdict), held to the shape and counts it was recorded with."""
    l25 = _stored_json(state, "L2.5", root)
    if not isinstance(l25, list):
        raise layer_outputs.ShapeError(
            "L2.5's stored file is missing or unreadable; record L2.5 again"
        )
    entry = state.layers.get("L2.5")
    recorded = entry.get("counts") if isinstance(entry, dict) else None
    try:
        return layer_outputs.l25_verdicts(l25, recorded or {}, l2_ids)
    except layer_outputs.ShapeError as exc:
        raise layer_outputs.ShapeError(
            f"L2.5's stored file does not have the verdict shape ({exc}); record L2.5 again"
        ) from exc


def _held_to_pool(state: PipelineState, findings: list, root) -> None:
    """Refuse a DEFERRED run's L2 unless it holds exactly its parent's pool, at its priorities.

    The parent's pool is consumed when this run closes, so an L2 that dropped a pool finding
    would lose it for good, and one that added a finding, or lowered a priority, would carry
    into the run what the parent never deferred. The pool and the priorities are read from the
    parent's summary and its stored L2 file, not from this run's L1. Duplicates are therefore
    not merged here: each keeps its id and gets the DUPLICATE verdict at L2.5.
    """
    summaries, _ = deferred.load_summaries(root)
    summary = next((s for s in summaries if s["run_id"] == state.parent), None)
    if summary is None:
        raise layer_outputs.ShapeError(
            f"the parent run {state.parent!r} has no readable summary under "
            ".claude/audits/runs/, so this deferred run's L2 cannot be checked against its pool; "
            "restore that summary, or abort this run"
        )
    try:
        pool = {f["id"]: f.get("priority") for f in deferred.findings_for(root, summary)}
    except LookupError as exc:
        raise layer_outputs.ShapeError(
            f"the pool of parent run {state.parent} cannot be loaded ({exc})"
        ) from exc
    ids = [f["id"] for f in findings]
    missing = [i for i in pool if i not in ids]
    extra = [i for i in ids if i not in pool]
    if missing or extra:
        wrong = ([f"lacks {', '.join(missing)}"] if missing else []) + \
                ([f"adds {', '.join(extra)}"] if extra else [])
        raise layer_outputs.ShapeError(
            f"a deferred run's L2 holds exactly the pool of run {state.parent}, but it "
            f"{' and '.join(wrong)}; re-anchor every pool finding under its own id, merging "
            "none (a duplicate gets the DUPLICATE verdict at L2.5) and adding none"
        )
    changed = [f"{f['id']} ({f['priority']}, the parent's {pool.get(f['id'])})"
               for f in findings if f["priority"] != pool.get(f["id"])]
    if changed:
        raise layer_outputs.ShapeError(
            "a deferred run's L2 keeps the parent's priorities, but it changes "
            f"{', '.join(changed)}"
        )


def _check_shape(layer: str, state: PipelineState, counts: dict, data: bytes, root) -> None:
    """Refuse an L2, L2.5 or L3 file that disagrees with its counts or with the earlier files,
    and an L4 file the ledger could not read back.

    Also the one place a deferral is held to its priority: a P1 is never passed on
    unverified (L2.5) or left unfixed (L3), and a P2 may only be DEFERRED on FAST, the tier
    that does not verify its P2s. A DEFERRED run's L2 is held to its parent's pool (see
    `_held_to_pool`), unless the parent is a `commit:<sha>`, which left no stored pool.
    """
    value = json.loads(data)
    try:
        if layer == "L2":
            findings = layer_outputs.l2_findings(value, counts["unique"])
            if state.mode == "DEFERRED" and not (state.parent or "").startswith("commit:"):
                _held_to_pool(state, findings, root)
        elif layer == "L2.5":
            l2 = _stored_l2(state, root)
            verdicts = layer_outputs.l25_verdicts(value, counts, [r["id"] for r in l2])
            priority = {r["id"]: r["priority"] for r in l2}
            allowed = ("P2", "P3") if state.tier == "FAST" else ("P3",)
            barred = [
                f"{i} ({priority[i]})" for i, v in verdicts.items()
                if v == "DEFERRED" and priority[i] not in allowed
            ]
            if barred:
                raise layer_outputs.ShapeError(
                    f"L2.5 defers {', '.join(barred)}; only P3 findings (P2 too on FAST) may "
                    "be passed on unverified"
                )
            _check_prior_fp(state, value, root)
        elif layer == "L3":
            l2 = _stored_l2(state, root)
            l25 = _stored_l25(state, root, [r["id"] for r in l2])
            plan = layer_outputs.l3_plan(value, counts["to_fix"], counts.get("deferred", 0), l25)
            priority = {r["id"]: r["priority"] for r in l2}
            barred = [i for i in plan["deferred"] if priority[i] == "P1"]
            if barred:
                raise layer_outputs.ShapeError(
                    f"L3 defers {', '.join(barred)} (P1); a confirmed P1 is always fixed"
                )
        elif layer == "L4":
            # The ledger reads this file back at resolve; a shape it cannot read lost every
            # fix of 12 runs as "no ledger rows could be built" (2026-10-21 to 2026-10-27).
            layer_outputs.l4_fixes(value)
    except layer_outputs.ShapeError as exc:
        raise RecordRefused(str(exc)) from exc


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
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, dest)
    except OSError as exc:
        raise RecordRefused(
            f"{layer} output could not be stored at {rel} ({exc.__class__.__name__}: {exc}); "
            "abort this run and start one with a plain --run-id"
        ) from exc
    return rel


def _store_run_file(state: PipelineState, name: str, value: object, root) -> None:
    """Write `value` as JSON to `name` in this run's directory, atomically."""
    dest = pathlib.Path(root) / ".claude" / "audits" / "runs" / state.run_id / name
    try:
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_name(dest.name + ".tmp")
        tmp.write_text(json.dumps(value, indent=1) + "\n", encoding="utf-8")
        os.replace(tmp, dest)
    except OSError as exc:
        raise RecordRefused(
            f"{name} could not be written under .claude/audits/runs/{state.run_id}/ "
            f"({exc.__class__.__name__}: {exc}); fix the cause and record again"
        ) from exc


def record(layer: str, *, status: str = "done", detail: str = "", fixes: int | None = None,
           counts: dict | None = None, source: str = "cli", output=None,
           root: str | pathlib.Path = ".",
           tree: str | None | _Unset = _UNSET) -> PipelineState:
    """Record that `layer` was reached and given a verdict.

    Raises RecordRefused when the run is paused, the layer is out of order, is a tool
    layer recorded by hand, carries counts that do not agree with the run, or (schema 2)
    is one of OUTPUT_LAYERS without a readable JSON `output` file, or is an L2, L2.5 or L3 file
    that disagrees with its counts or with the earlier files (see `_check_shape`). A layer outside
    OUTPUT_LAYERS given an `output` is refused too, so the prose cannot drift from the
    code by passing a flag that would be silently ignored.

    `tree`, when passed, must be a fingerprint taken in this same call chain with
    nothing but `.claude/audits/` written since; omitted, it is computed here.
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
    if output is not None and layer not in OUTPUT_LAYERS and layer != "L6":
        raise RecordRefused(
            f"{layer} does not take --output; only {', '.join(OUTPUT_LAYERS)} and L6 store "
            "their work on disk"
        )
    entry = {"status": status, "detail": detail, "at": core.now()}
    data = None
    fp_anchors: dict | None = None
    if not state.legacy:
        if layer in core.TOOL_LAYERS and source != "tool":
            raise RecordRefused(
                f"{layer} is recorded by its tool, not by hand: {core.TOOL_LAYERS[layer]}"
            )
        if isinstance(tree, _Unset):
            tree = core.fingerprint(root)
        raw = state.to_json()
        owed = core.fixes_owed(raw, tree) or (fixes or 0) > 0
        missing = [p for p in core.predecessors(layer, raw, owed) if p not in state.layers]
        if missing:
            raise RecordRefused(
                f"{layer} cannot be recorded before {', '.join(missing)}; re-read "
                f"{core.HEADINGS[missing[0]]} of audit-fix.md and run it first"
            )
        if layer in core.FIX_VERDICT_LAYERS and tree is None:
            raise RecordRefused(
                f"{layer} cannot be recorded: the working tree could not be fingerprinted "
                "(git missing from PATH, older than 2.31, or timed out), so the verdict "
                "could never be shown current. Fix git and record again, or retire the run "
                "with `pipeline abort --reason`"
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
            data = _read_output(layer, output, root)
            _check_shape(layer, state, counts, data, root)
            if layer == "L1":
                _check_prior_brief(state, root)
        elif layer == "L6":
            # Optional: only an APPROVED verdict over false positives requires the file.
            data = _read_output(layer, output, root) if output is not None else None
            _check_l6(state, detail, None if data is None else json.loads(data), root)
        if layer == "L2.5" and state.tier != "FAST":
            fp_anchors = _fp_anchors(state, root)
        state.seq += 1
        entry.update({"seq": state.seq, "source": source})
        if counts:
            entry["counts"] = counts
        if layer in core.FIX_VERDICT_LAYERS:
            entry["tree"] = tree
    if data is None and output is not None:
        # A 0.10.x run never requires the file, but one it is given is kept, not ignored.
        data = _read_output(layer, output, root)
    if data is not None:
        entry["output"] = _store_output(state, layer, data, root)
    if fp_anchors is not None:
        _store_run_file(state, FP_ANCHORS, fp_anchors, root)
    state.layers[layer] = entry
    if fixes is not None:
        state.fixes_applied = int(fixes)
    _write(state, root)
    return state


def current_tree(state: PipelineState, root: str | pathlib.Path = ".") -> str | None:
    """The fingerprint this run is judged against; a 0.10.x run is never judged by one."""
    return None if state.legacy else core.fingerprint(root)


def _tree(state: PipelineState, root, tree: str | None | _Unset) -> str | None:
    return current_tree(state, root) if isinstance(tree, _Unset) else tree


def required_layers(state: PipelineState, root: str | pathlib.Path = ".", *,
                    tree: str | None | _Unset = _UNSET) -> tuple[str, ...]:
    if state.legacy:
        required = list(core.LEGACY_CORE_REQUIRED)
        if state.fixes_applied > 0:
            required.extend(core.LEGACY_FIX_REQUIRED[state.tier])
        return tuple(required)
    raw = state.to_json()
    return core.required_layers(raw, core.fixes_owed(raw, _tree(state, root, tree)))


def outstanding(state: PipelineState, root: str | pathlib.Path = ".", *,
                tree: str | None | _Unset = _UNSET) -> list[str]:
    """What this run still owes before it may close. Legacy runs owe nothing here."""
    if state.legacy:
        return []
    return core.outstanding(state.to_json(), _tree(state, root, tree))


def verify_commit_allowed(state: PipelineState | None, root: str | pathlib.Path = ".", *,
                          tree: str | None | _Unset = _UNSET) -> CommitDecision:
    if state is None:
        return CommitDecision(allowed=True, reasons=["no CCA run is open"])
    reasons = core.commit_reasons(state.to_json(), _tree(state, root, tree))
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
    _registry("unregister", root)
    return log


#: The saved `recall_records` answer Step 1 renders the prior-bugs block from.
PRIOR_RECORDS = "prior-records.json"
#: The receipt `bugs brief --out` writes: Step 1's local reader path.
LOCAL_PRIOR = "local-prior.json"


def _check_prior_brief(state: PipelineState, root) -> None:
    """Refuse a DIFF or HUNT run's L1 unless Step 1 left a prior-bugs brief in its run directory.

    Step 1 was prose with no gate, and measured 2026-10-27 over the previous 48 hours, 13
    Claude Code runs dispatched their auditors without building the brief while every Codex
    run built it. Either file shows the step ran: the hosted answer or the local receipt,
    which `bugs brief` writes even when the reader is unavailable. A deferred pass dispatches
    no L1 auditors, so it is not asked for one.
    """
    if state.mode not in ("DIFF", "HUNT"):
        return
    run = deferred.runs_dir(root) / state.run_id
    if (run / LOCAL_PRIOR).is_file() or (run / PRIOR_RECORDS).is_file():
        return
    where = f".claude/audits/runs/{state.run_id}"
    raise RecordRefused(
        f"L1 needs the prior-bugs brief first, and {where}/ holds neither {LOCAL_PRIOR} nor "
        f"{PRIOR_RECORDS}. Run `python -P -m cca_checks bugs brief --root <target Git root> "
        f"--out {where}/{LOCAL_PRIOR} --files <FILES>` (or save the hosted recall_records "
        f"answer as {where}/{PRIOR_RECORDS}; a hunt that must read no memory passes "
        "`--skipped <unresolved-root|own-checkout>` instead of --root and --files), paste its "
        "output into every auditor prompt as Step 1 of audit-fix.md says, then record again"
    )


def _saved_brief_answer(state: PipelineState, root) -> tuple[list[dict], str | None] | None:
    """(the records of the answer this DIFF run saved, its repository), or None without one.

    The records are empty, and the repository None, when the answer or the origin cannot be
    read: the brief then listed nothing, which is what the counts must say.
    """
    path = deferred.runs_dir(root) / state.run_id / PRIOR_RECORDS
    if state.mode != "DIFF" or not path.is_file():
        return None
    from .bugs import envelope
    from .bugs.gitread import GitError, remote_repo

    records, _, why = envelope.load(path)
    try:
        repo = remote_repo(pathlib.Path(root))
    except (OSError, GitError):
        repo = None
    if why or repo is None:
        return [], None
    return records, repo


_BRIEF_LINE = re.compile(r"^(BUG|LESSON) (\S+) \[(\w+)\]", re.MULTILINE)


def _local_brief(state: PipelineState, root) -> tuple[str, list[tuple[str, str, str]]] | None:
    """(status, [(kind, id, match)]) from this run's `bugs brief` receipt, or None without one.

    The lines are the reader block's own BUG and LESSON lines, so they are the records the
    auditors were given. A receipt that cannot be read lists nothing, under status
    `unreadable`, which is what the counts must then say.
    """
    path = deferred.runs_dir(root) / state.run_id / LOCAL_PRIOR
    if not path.is_file():
        return None
    try:
        receipt = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return "unreadable", []
    if not isinstance(receipt, dict):
        return "unreadable", []
    block = receipt.get("block")
    lines = _BRIEF_LINE.findall(block) if isinstance(block, str) else []
    return str(receipt.get("status") or "unreadable"), lines


def prior_block_counts(state: PipelineState, root) -> dict[str, int] | None:
    """The anchored prior-bugs block's record count by anchor match, or None without one.

    Counted with the block's own selection from the answer this run saved, so the counts are
    the block the auditors were given. They are what the measurement trigger reads: twelve
    DIFF runs whose block held an exact or symbol_changed record, across two repositories.
    Without a hosted answer they are counted from the local receipt's BUG lines: until
    2026-10-28 they were not, so every run on the local path reported no block at all.
    """
    from .bugs import prior

    saved = _saved_brief_answer(state, root)
    if saved is None:
        local = _local_brief(state, root) if state.mode == "DIFF" else None
        if local is None:
            return None
        return {m: sum(1 for k, _, got in local[1] if k == "BUG" and got == m)
                for m in prior.MATCH_ORDER}
    records, repo = saved
    return prior.block_counts(prior.select_records(records, repo) if repo else [])


def lessons_block_count(state: PipelineState, root) -> int | None:
    """How many lessons the LESSONS block gave the auditors, or None without a saved answer.

    Selected from the same answer as `prior_block_counts`, so a later replay can tell the runs
    whose brief held lessons from those whose brief did not, and ablate the block.
    """
    from .bugs import prior

    saved = _saved_brief_answer(state, root)
    if saved is None:
        local = _local_brief(state, root) if state.mode == "DIFF" else None
        return None if local is None else sum(1 for k, _, _ in local[1] if k == "LESSON")
    records, repo = saved
    return len(prior.select_lessons(records, repo)) if repo else 0


def prior_brief(state: PipelineState, root) -> dict | None:
    """Which Step 1 brief this run built, its status and the record ids it listed.

    Recorded for every mode, so the ledger can later say which prior bugs and lessons each
    run's auditors were shown, and a recurrence replay can join a new finding to them.
    """
    run = deferred.runs_dir(root) / state.run_id
    if (run / PRIOR_RECORDS).is_file():
        return {"source": "hosted"}
    local = _local_brief(state, root)
    if local is None:
        return None
    status, lines = local
    return {"source": "local", "status": status, "ids": [i for _, i, _ in lines]}


def _write_summary(state: PipelineState, root) -> None:
    """Write runs/<run_id>/summary.json: what a later deferred pass reads about this run.

    `base_tree` is the start fingerprint, the tree this run's auditors read. A deferred pass
    judges STALE against it: `commit` already holds this run's own fixes, so a finding those
    fixes removed would read there as never present. Readers use `.get`, so the key is an
    addition within schema 1.
    """
    summary = {
        "schema": deferred.SUMMARY_SCHEMA, "run_id": state.run_id, "tier": state.tier,
        "mode": state.mode, "no_fix": state.no_fix, "base_commit": state.base_commit,
        "base_tree": state.base_tree,
        "commit": _commit_of(root, "HEAD"), "parent": state.parent,
        "completed_utc": core.now(),
        "pool": deferred.pool_ids(_stored_json(state, "L2.5", root),
                                  _stored_json(state, "L3", root)),
        "human_pending": (_layer_count(state, "L2.5", "uncertain") or 0)
        + deferred.parked_count(root, state.run_id) + len(disputed_fps(state, root)),
        # False positives the L6 architect rejected: reported as disputed, UNCERTAIN → human.
        "disputed_fp": disputed_fps(state, root),
        "prior_block": prior_block_counts(state, root),
        "lessons_block": lessons_block_count(state, root),
        "prior_brief": prior_brief(state, root),
    }
    path = deferred.runs_dir(root) / state.run_id / "summary.json"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except OSError as exc:
        raise RecordRefused(
            f"the run summary could not be written to {path.as_posix()} "
            f"({exc.__class__.__name__}: {exc}); without it no deferred pass can find this "
            "run. Fix the cause and close again"
        ) from exc


def close(root: str | pathlib.Path = ".") -> pathlib.Path | None:
    """Retire a run that is complete. An incomplete or paused run is refused, not logged.

    A schema 2 run first writes its summary (see `_write_summary`); `abort` writes none, and
    neither does a 0.10.x run.
    """
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
    if not state.legacy:
        _write_summary(state, root)
        _capture_pending(state, root)
    recorded = ", ".join(sorted(state.layers))
    log = core.append_log(
        root,
        f"| {core.now()} | run {state.run_id} | COMPLETE | tier {state.tier} | "
        f"layers: {recorded} |",
    )
    path.unlink()
    _registry("unregister", root)
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
    tree = current_tree(state, root)
    state.paused = None
    # A resumed session is a new session: it gets the Stop hook's three refusals again,
    # and it owns the run from here, so the stop guard holds it rather than the opener.
    state.stop_refusals = 0
    state.owner_session = os.environ.get("CLAUDE_CODE_SESSION_ID") or state.owner_session
    state.stop_logged = False
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


def _deferred_context(run_id: str, root) -> tuple[dict, list[dict], deferred.Offer]:
    """(summary, findings, Offer) for the named run, or for the newest completed one."""
    summaries, unreadable = deferred.load_summaries(root)
    if run_id:
        summary = next((s for s in summaries if s["run_id"] == run_id), None)
        if summary is None:
            # Worded apart from the no-summary refusal below: audit-fix.md D.1 takes the
            # commit-message fallback exactly when a refusal contains `--from commit:<sha>`.
            known = ", ".join(s["run_id"] for s in summaries) or "none"
            raise RecordRefused(
                f"--run {run_id!r} is not a completed run with a readable summary (completed "
                f"runs: {known}); omit --run for the newest, or name one of those"
            )
    elif summaries:
        summary = summaries[-1]
    elif unreadable:
        # No `commit:<sha>` fallback here: `start` refuses while any summary is unreadable, so
        # offering it would send the caller to a start that refuses.
        raise RecordRefused(
            "every run summary under .claude/audits/runs/ is unreadable; "
            + _unreadable_block(unreadable)
        )
    else:
        raise RecordRefused(
            "no completed run has a summary under .claude/audits/runs/; for a "
            "run that predates summaries, parse the Deferred section of its CCA commit into "
            "the L1 file and start with --from commit:<sha> (audit-fix.md § Second Pass)"
        )
    try:
        findings = deferred.findings_for(root, summary)
    except LookupError as exc:
        raise RecordRefused(str(exc)) from exc
    commit = summary.get("commit")
    reachable = bool(commit) and _is_ancestor(root, str(commit))
    offer = deferred.assess(summary, summaries, findings, commit_reachable=reachable,
                            unreadable=unreadable)
    return summary, findings, offer


def _unreadable_block(unreadable: list[str]) -> str:
    return (
        f"{len(unreadable)} run summary file(s) under .claude/audits/runs/ cannot be read "
        f"({', '.join(unreadable)}); they block a deferred start until they are fixed or "
        "moved aside"
    )


def deferred_report(run_id: str = "", root: str | pathlib.Path = ".") -> deferred.Offer:
    """What a completed run deferred, and whether a second pass is offered and advised.

    Read-only, so unreadable summaries do not stop the report. They do stop the pass:
    `start --mode DEFERRED` refuses while any exists, so the report says `offered: false`, with
    no recommendation, and gives that as its first reason. The offer must not advise a pass
    the gate will refuse.
    """
    offer = _deferred_context(run_id, root)[2]
    if offer.unreadable:
        offer.offered = False
        offer.recommendation = None
        offer.reasons.insert(0, _unreadable_block(offer.unreadable))
    return offer


#: The one folder `pipeline deferred --write` may write to: where the layer work files go
#: (audit-fix.md writes `.claude/audits/out/L1.json`), inside the fingerprint's exclusion.
_WRITE_DIR = f"{core.EXCLUDED}/out"


def write_deferred(output, run_id: str = "",
                   root: str | pathlib.Path = ".") -> tuple[deferred.Offer, str]:
    """Write the offered pool's full finding objects to `output`: a deferred run's L1 input.

    Refused when unreadable summaries exist (`start --mode DEFERRED` would refuse the run this
    file is for), when the pass is not offered (naming any other pools that still are, so an
    empty newest run does not dead-end), and when `output`, resolved, lies outside
    `.claude/audits/out/`.
    """
    _, findings, offer = _deferred_context(run_id, root)
    if offer.unreadable:
        raise RecordRefused(_unreadable_block(offer.unreadable))
    if not offer.offered:
        older = ""
        if offer.other_pools:
            older = (f"; other pools still on offer: {', '.join(offer.other_pools)}; "
                     f"pass --run {offer.other_pools[-1]}")
        raise RecordRefused(
            f"run {offer.run_id}'s deferred findings are not offered: {offer.reasons[0]}" + older
        )
    path = pathlib.Path(output)
    if not path.is_absolute():
        path = pathlib.Path(root) / path
    # An allowlist, not a deny list: `.claude/audits/` also holds the run state, the stored
    # runs, the disposition ledger and the execution log, and an L1 file written over any of
    # them destroys the record. `_inside` resolves both paths, so `out/../x` is not inside.
    if not core._inside(str(path), str(pathlib.Path(root) / _WRITE_DIR)):
        raise RecordRefused(
            f"--write {output} is outside {_WRITE_DIR}/, the one folder it may write to; "
            f"write the L1 file there, for example {_WRITE_DIR}/L1.json"
        )
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        raise RecordRefused(
            f"--write {output} could not be written ({exc.__class__.__name__}: {exc}); "
            f"name a file under {_WRITE_DIR}/"
        ) from exc
    return offer, str(path)
