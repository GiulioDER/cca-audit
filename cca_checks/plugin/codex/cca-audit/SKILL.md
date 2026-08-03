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
| `.claude/tools/cca_*.py` | The matching checker in this skill's `scripts/` directory |
| Optional `.claude/audits/` trail | Omit unless the user explicitly requests persistent audit files |

Resolve the selected skill directory from this `SKILL.md` path. Use absolute paths when invoking
the bundled checker scripts. Never assume the current repository has a `.claude/` directory.

Stay inside the current Codex task. Use subagents, not user owned tasks, new chats, or external
services. Do not push, open a pull request, merge, deploy, or communicate findings externally unless
the user separately authorizes that action.

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

Launch independent roles concurrently up to the available subagent capacity. If the tier needs more
roles than available slots, queue the remaining roles and dispatch them as soon as slots finish.
Never drop an applicable role because capacity is lower than the canonical eleven role maximum.

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

## Report completion

Return the canonical summary with tier, scope, domains, deterministic coverage and blindness,
finding dispositions, fix to finding mapping, test and lint results, differential verdict, architect
verdict, deferred work, and commit identifier when one was created.

State skipped gates distinctly from passed gates. If execution stops early, name the failed gate and
preserve the workspace state needed for the user to continue.
