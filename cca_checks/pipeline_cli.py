"""The `pipeline` subcommand: the CLI over `pipeline_state`.

The pipeline's own bookkeeping. Not a checker and never a Verdict: it records which
LAYERS of /audit-fix were reached, so the commit gate in
cca_checks/plugin/hooks/cca_pipeline_guard.py can refuse a commit from a run that
skipped one. Every other subcommand answers a question about the code; this one
answers a question about the audit, which is why it lives apart from them.
"""

from __future__ import annotations

import json
import sys

from . import pipeline_state


def add_parser(sub) -> None:
    """Register `pipeline` and its subcommands on the top-level subparsers."""
    pl = sub.add_parser("pipeline", help="record and enforce /audit-fix layer progress")
    plsub = pl.add_subparsers(dest="pipeline_cmd", required=True)

    pl_start = plsub.add_parser(
        "start", help="open a run (call at Step 0.6, once the tier is known)"
    )
    pl_start.add_argument("--tier", required=True, choices=sorted(pipeline_state.TIERS))
    pl_start.add_argument("--mode", default="DIFF", choices=list(pipeline_state.MODES))
    pl_start.add_argument("--base", default="HEAD",
                          help="the revision the audited diff is taken against (HEAD~N for "
                               "`commit N`); stored as the run's base_commit. Refused with "
                               "--mode DEFERRED, which takes its parent's base")
    pl_start.add_argument("--from", dest="parent", default="",
                          help="--mode DEFERRED only: the completed run (or commit:<sha>) "
                               "whose deferred findings this run takes")
    pl_start.add_argument("--no-fix", action="store_true")
    pl_start.add_argument("--run-id", default="")
    pl_start.add_argument("--root", default=".")

    pl_record = plsub.add_parser(
        "record", help="record that a layer was reached and judged"
    )
    pl_record.add_argument("layer", choices=list(pipeline_state.LAYERS))
    pl_record.add_argument("--status", default="done",
                           choices=["done", "skipped", "failed", "n/a"])
    # Free text, and load-bearing for L6: the gate reads this as the architect
    # verdict, so an empty --detail on L6 is itself a refusal rather than a pass.
    pl_record.add_argument("--detail", default="")
    pl_record.add_argument("--fixes", type=int, default=None,
                           help="number of fixes written to the tree (record at L4)")
    for flag, help_text in (
        ("--unique", "L2: unique findings after consolidation"),
        ("--confirmed", "L2.5: CONFIRMED verdicts"),
        ("--fp", "L2.5: FALSE_POSITIVE verdicts"),
        ("--uncertain", "L2.5: UNCERTAIN verdicts"),
        ("--duplicate", "L2.5: DUPLICATE verdicts"),
        ("--unverified", "L2.5: findings passed on without a verdict (P3; P2 on FAST), "
                         "each marked DEFERRED in the L2.5 file"),
        ("--stale", "L2.5, DEFERRED run only: findings whose code moved on (STALE)"),
        ("--to-fix", "L3: findings the plan will fix"),
        ("--deferred", "L3: confirmed findings deferred, not fixed"),
    ):
        pl_record.add_argument(flag, type=int, default=None, help=help_text)
    pl_record.add_argument(
        "--output", default=None,
        help="L1, L2, L2.5, L3, L4: a JSON file holding the layer's work; it is copied "
             "into .claude/audits/runs/<run_id>/ so a paused run can resume from it",
    )
    pl_record.add_argument("--root", default=".")

    pl_status = plsub.add_parser("status", help="print the open run and what it still owes")
    pl_status.add_argument(
        "--require-complete", action="store_true",
        help="exit 2 while the open run still owes a layer (Codex has no Stop hook)",
    )
    pl_status.add_argument("--root", default=".")

    pl_pause = plsub.add_parser(
        "pause", help="pause the open run right after a record, to resume in a later session"
    )
    pl_pause.add_argument("--reason", required=True)
    pl_pause.add_argument("--root", default=".")

    pl_resume = plsub.add_parser(
        "resume", help="resume a paused run; prints the next layer and its output files"
    )
    pl_resume.add_argument("--root", default=".")

    pl_abort = plsub.add_parser(
        "abort", help="close a run without satisfying its gates, on the record"
    )
    pl_abort.add_argument("--reason", required=True)
    pl_abort.add_argument("--root", default=".")

    pl_close = plsub.add_parser("close", help="retire a run that passed its gates")
    pl_close.add_argument("--root", default=".")

    pl_deferred = plsub.add_parser(
        "deferred", help="after a run closes: what it deferred, and whether a second pass "
                         "over it is offered (RUN or SKIP, with reasons)"
    )
    pl_deferred.add_argument("--run", default="",
                             help="a completed run id; default the newest completed run")
    pl_deferred.add_argument("--write", default=None,
                             help="write the pool's findings to this file under "
                                  ".claude/audits/, as a deferred run's L1 input")
    pl_deferred.add_argument("--root", default=".")


def run(a) -> int:
    """Run the `pipeline` subcommand. Prints JSON; returns a process exit code.

    Every path prints an object rather than a line of prose, because the caller
    is usually an orchestrator reading the result back, and a status a model has
    to parse out of English is a status it will eventually parse wrong.

    `record` and `status` fingerprint the tree once and judge everything against
    that one value: each fingerprint is a `git add -A` over the whole repository.
    """
    try:
        if a.pipeline_cmd == "start":
            try:
                state = pipeline_state.start(
                    a.tier, a.mode, no_fix=a.no_fix, run_id=a.run_id, base=a.base,
                    parent=a.parent, root=a.root,
                )
            except pipeline_state.RecordRefused as exc:
                print(json.dumps({"refused": "start", "reason": str(exc)}), file=sys.stderr)
                return 2
            print(json.dumps({"started": state.to_json()}))
            return 0

        if a.pipeline_cmd == "record":
            counts = {"unique": a.unique, "confirmed": a.confirmed, "fp": a.fp,
                      "uncertain": a.uncertain, "duplicate": a.duplicate,
                      "unverified": a.unverified, "stale": a.stale, "to_fix": a.to_fix,
                      "deferred": a.deferred}
            # Recording writes only under .claude/audits/, which the fingerprint
            # excludes, so the tree taken before it is the tree after it.
            run_state = pipeline_state.load(a.root)
            tree = pipeline_state.current_tree(run_state, a.root) if run_state else None
            try:
                state = pipeline_state.record(
                    a.layer, status=a.status, detail=a.detail, fixes=a.fixes,
                    counts=counts, output=a.output, root=a.root, tree=tree,
                )
            except pipeline_state.RecordRefused as exc:
                print(json.dumps({"refused": a.layer, "reason": str(exc)}), file=sys.stderr)
                return 2
            decision = pipeline_state.verify_commit_allowed(state, root=a.root, tree=tree)
            print(json.dumps({
                "recorded": a.layer,
                "run_id": state.run_id,
                "output": state.layers[a.layer].get("output"),
                "commit_allowed": decision.allowed,
                "outstanding": decision.reasons if not decision.allowed else [],
            }))
            return 0

        if a.pipeline_cmd == "status":
            state = pipeline_state.load(a.root)
            tree = pipeline_state.current_tree(state, a.root) if state else None
            decision = pipeline_state.verify_commit_allowed(state, root=a.root, tree=tree)
            owed = pipeline_state.outstanding(state, a.root, tree=tree) if state else []
            if state is not None and state.paused:
                # A paused run cannot close until it is resumed, whatever else it owes.
                owed = ["the run is paused; run `python -m cca_checks pipeline resume` to "
                        "continue it, or `pipeline abort --reason` to retire it", *owed]
            if a.require_complete and state is not None and not owed:
                # Nothing is missing, but the run is still open: the Stop hook and the
                # commit guard keep treating it as in flight until it is retired.
                owed = ["every layer is recorded but the run is still open: run "
                        "`python -m cca_checks pipeline close` to retire it"]
            required = pipeline_state.required_layers(state, a.root, tree=tree) if state else []
            print(json.dumps({
                "open": state is not None,
                "run": state.to_json() if state else None,
                "paused": state.paused if state else None,
                "required": list(required),
                "commit_allowed": decision.allowed,
                "outstanding": decision.reasons if not decision.allowed else [],
                "outstanding_to_close": owed,
            }))
            return 2 if (a.require_complete and owed) else 0

        if a.pipeline_cmd in ("pause", "resume"):
            try:
                if a.pipeline_cmd == "pause":
                    state = pipeline_state.pause(a.reason, root=a.root)
                    result = {"paused": state.run_id, **(state.paused or {})}
                else:
                    result = pipeline_state.resume(root=a.root)
            except pipeline_state.RecordRefused as exc:
                print(json.dumps({"refused": a.pipeline_cmd, "reason": str(exc)}),
                      file=sys.stderr)
                return 2
            print(json.dumps(result))
            return 0

        if a.pipeline_cmd == "deferred":
            try:
                if a.write is not None:
                    offer, path = pipeline_state.write_deferred(a.write, a.run, root=a.root)
                    print(json.dumps({**offer.to_json(), "written": path}))
                    return 0
                offer = pipeline_state.deferred_report(a.run, root=a.root)
            except pipeline_state.RecordRefused as exc:
                print(json.dumps({"refused": "deferred", "reason": str(exc)}), file=sys.stderr)
                return 2
            print(json.dumps(offer.to_json()))
            return 0

        if a.pipeline_cmd == "abort":
            log = pipeline_state.abort(a.reason, root=a.root)
            print(json.dumps({"aborted": log is not None, "logged_to": str(log) if log else None}))
            return 0

        try:
            log = pipeline_state.close(root=a.root)
        except pipeline_state.RecordRefused as exc:
            print(json.dumps({"refused": "close", "reason": str(exc)}), file=sys.stderr)
            return 2
        print(json.dumps({"closed": log is not None, "logged_to": str(log) if log else None}))
        return 0
    except ValueError as exc:
        # A bad tier, an unknown layer, or an unparseable state file. Exit
        # non-zero so a shell chain stops here: a recording step that silently
        # failed would leave the run looking less complete than it is, or more.
        print(json.dumps({"error": str(exc)}), file=sys.stderr)
        return 1

