---
name: cca-audit
description: Run the verified CCA Audit + Fix pipeline inside the current Codex task using parallel subagents, deterministic claim settlers, finding verification, repairs, regression review, and a final architect gate. Use whenever the user says `audit+fix`, `audit and fix`, `cca audit`, `run CCA`, or asks for CCA hunt, fast, deep, no-fix, p1-only, commit, files, or deferred modes.
---

# CCA Audit + Fix

Run the canonical CCA pipeline in the current task. Keep the primary agent as orchestrator and use
Codex subagents for independent audit and review roles.

## Load the contract

Before taking repository actions, read `references/pipeline.md` completely. It is the canonical
pipeline, including argument parsing, tier selection, findings schema, verification policy, repair
gates, and final report. Treat this file as authoritative except for the Codex adaptations below.

Read every applicable role prompt under `references/agents/` completely before dispatching that
role. Do not ask a subagent to interpret a role file that the primary agent has not read.

Interpret text following the trigger as the canonical arguments. Examples:

```text
audit+fix
audit+fix no-fix
audit+fix deep commit 2
audit+fix files src/orders.py
audit+fix hunt src/payments
audit+fix deferred
```

## Apply the Codex adapter

Map Claude specific terms in the canonical pipeline as follows:

| Canonical term | Codex execution |
| --- | --- |
| `/audit-fix <args>` | Invocation of this skill with `<args>` |
| Agent or `subagent_type` call | Spawn a Codex subagent with the matching role prompt |
| Agent return value | The subagent final response returned to the primary agent |
| Launch in a SINGLE message, or in ONE message (parallel) | Dispatch through the six slot queue under "Respect the six subagent limit" |
| `.claude/tools/cca_*.py` | The matching checker in this skill's `scripts/` directory |
| Optional `.claude/audits/` trail | Omit unless the user explicitly requests persistent audit files |

`python -m cca_checks pipeline pause --reason "<why>"` and `pipeline resume` exist here too, and
Codex must still write the `--output` file that every L1, L2, L2.5, L3 and L4 record requires,
under `.claude/audits/`, even when it omits the rest of the trail. On STANDARD and DEEP it must
also append the disposition ledger rows of Step 2.5b to `.claude/audits/AUDITOR_SCORECARD.jsonl`,
carrying the `run_id` that `pipeline start` printed: `record L2.5` refuses without them, and the
Step 2.6 scorecard reads them. Neither is optional trail.

Resolve the selected skill directory from this `SKILL.md` path. Use absolute paths when invoking
the bundled checker scripts. Never assume the current repository has a `.claude/` directory.

Stay inside the current Codex task. Use subagents, not user owned tasks, new chats, or external
services. Do not push, open a pull request, merge, deploy, or communicate findings externally unless
the user separately authorizes that action.

## Guard untrusted repository execution

Treat every HUNT target as untrusted. Obtain explicit authorization before running any repository
supplied command, including tests, linters, builds, package scripts, install hooks, or generated
executables. Name the command and working directory. Explain whether isolation excludes user secrets,
host credentials, and unrestricted network access.

Without that authorization, use static analysis only. Do not install target dependencies or execute
target code merely because the repository declares a command. Mark any finding or fix that requires
execution as `UNCERTAIN` or blocked, and report the skipped execution gate distinctly from a pass.

## Orchestrate subagents

Perform target detection, tier selection, deterministic coverage checks, consolidation, repair,
and final reporting in the primary agent. Delegate the independent audit and review roles.

For each dispatched role:

1. Build the prompt from the complete matching file in `references/agents/`, the shared target list,
   diff command or hunt scope, detected languages, project context, role scope, prefix, and canonical
   findings schema.
2. Require the subagent to inspect scoped repository instructions before analysis.
3. Require read only behavior. Audit, verification, differential review, and architect agents must
   not edit the workspace or create audit trail files.
4. Require the JSON result before optional prose exactly as the canonical pipeline specifies.
5. Tell the subagent not to spawn more agents. The primary agent owns capacity and independence.

### Respect the six subagent limit

Codex refuses to open a seventh subagent: `agents.max_threads` defaults to 6, and a spawn past it
fails with `agent thread limit reached`. A subagent that has returned its final response still holds
its slot until it is closed. The canonical pipeline asks for up to eleven auditors at once, and for
three skeptics per finding in the DEEP panel; in Codex, every such launch goes through this queue:

1. Keep at most six subagents open at once, counted across every role and layer.
2. Spawn the first six roles and queue the rest in canonical order.
3. Wait for any open subagent to finish. Save its final response, then call `close_agent` on it.
   A slot is free only after the close succeeds.
4. Spawn the next queued role into the freed slot. Repeat until the queue is empty and every result
   is saved.
5. If a spawn still fails on the thread limit, this host caps lower than six. Do not retry blindly:
   wait for an open subagent to finish, save and close it, then spawn again.

Never drop an applicable role because of the limit. A queued role runs later; it is never skipped,
merged into another role, or reported as run before its result is saved.

Keep verification independent:

1. Give each findings verifier the raw finding and code scope, not another verifier's conclusion.
2. For the DEEP high stakes panel, run three independent `fp-check` tasks and apply the canonical
   two of three rule.
3. Run mechanical settlers in the primary task when the pipeline selects them. An unavailable tool
   produces `UNCERTAIN`, never a clean result.
4. Preserve every `CONFIRMED`, `FALSE_POSITIVE`, and `UNCERTAIN` artifact for consolidation and the
   final gate.

## Apply fixes safely

Do not edit during audit or verification. After consolidation, implement only findings eligible
under the selected tier and arguments.

Keep workspace writes under primary agent control. Delegate a repair only when its files are
disjoint from every other active repair, give it exactly one confirmed finding, and review its diff
before accepting it. Use the canonical red then green regression proof for every P1.

Run the detected tests and linters after repairs. Then dispatch the read only differential reviewer
and architect reviewer. Repeat the canonical revise loop when required. Respect `no-fix` as an
absolute prohibition on edits and commits.

The canonical final commit is authorized only by an `audit+fix` invocation that permits fixes and
only after the architect returns `APPROVED`. Never include unrelated preexisting changes.

## Gate before reporting

Codex has no hooks, so nothing stops a run from ending half done except this check. Before any final
summary, run:

```bash
python -m cca_checks pipeline status --require-complete
```

Exit 2 means the run still owes the layers listed under `outstanding_to_close`. Run them, or retire
the run on the record with `python -m cca_checks pipeline abort --reason "<why>"`. When the only item
is the instruction to close the run, every layer is done: run `python -m cca_checks pipeline close`,
then run the status check again. Never report a run this command refuses as complete. This is weaker than the Claude Code Stop hook, which enforces the
same check without relying on the orchestrator to call it.

The commit guard does not run in Codex either, so the Step 7 refusals in `references/pipeline.md`
are yours to apply: stage by path in one command and run `git commit` alone in the next, with no
other command, substitution or index override in it, and before committing confirm that
`git diff --cached --name-only` and `git diff --name-only` share no path and that no staged deletion
still has its file on disk. `pipeline status` does not make those checks.

## Report completion

Return the canonical summary with tier, scope, domains, deterministic coverage and blindness,
finding dispositions, fix to finding mapping, test and lint results, differential verdict, architect
verdict, deferred work, and commit identifier when one was created.

State skipped gates distinctly from passed gates. If execution stops early, name the failed gate and
preserve the workspace state needed for the user to continue.
