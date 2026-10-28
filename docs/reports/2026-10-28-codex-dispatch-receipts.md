# Codex auditor prompt preparation, 2026-10-28

The owner approved the receipt substitute after a live hook probe established that the
current collaboration spawn payload does not expose its plaintext message. The new gate
checks each saved prompt through the shared `dispatch_reasons` function and binds L1 to
the checked prompt bytes, run, role, saved brief and source tree. It does not attest what
the eventual tool call delivered. The orchestrator still must send the exact checked text.

## Hook observations

The local runtime identified itself as `codex-cli 0.165.0-alpha.1.2`. A harmless child
launched after a `SubagentStart` handler returned `continue: false`. Its actual payload
had the following fields, with private path and identity values omitted here:

```json
{
  "hook_event_name": "SubagentStart",
  "agent_type": "default",
  "agent_id": "<child identity>",
  "session_id": "<parent identity>",
  "turn_id": "<turn identity>",
  "transcript_path": "<child transcript>",
  "cwd": "<workspace>",
  "model": "gpt-6.1-sol",
  "permission_mode": "default"
}
```

A fresh invocation with a broad `PreToolUse` matcher observed both a shell positive
control and the collaboration spawn. The spawn's tool-specific fields were:

```json
{
  "tool_name": "collaborationspawn_agent",
  "tool_input": {
    "task_name": "probe",
    "message": "<ciphertext, not the plaintext test prompt>"
  }
}
```

The narrower `Agent|spawn_agent` matcher did not catch this tool name. A separate
harmless launch was successfully denied by matching its visible `task_name`, with
`permissionDecision: deny`. Therefore launch blocking exists on this path, but the
hook cannot apply the shared plaintext brief check. No decryption was attempted.
The temporary global hook definition and bridge edits were restored byte for byte.

These are observations of the tested runtime, not a claim about every Codex host or
tool path. [OpenAI's hook documentation](https://learn.chatgpt.com/docs/hooks) describes
the generic spawn alias and warns that specialized paths can opt out. It also states
that `SubagentStart` does not support stopping the launch.

## Use

Declare every applicable L1 dispatch at start, including domain scopes. FAST needs the
three core roles; STANDARD and DEEP need the six core roles. The separate
`stakes_auditor` and `data_auditor` scopes use the shared bug-auditor guard rules.

```text
python -P -m cca_checks pipeline start --tier FAST --base HEAD --client codex --auditor code_auditor --auditor bug_auditor --auditor security_auditor
python -P -m cca_checks pipeline check-dispatch --role bug_auditor --prompt-file .claude/audits/prompts/bug-auditor.txt
```

Repeat the second command for every planned role, then dispatch that file's exact text.
`record L1` refuses missing receipts, changed prompt bytes, a changed brief or source
tree, and a receipt from another run or role. Deferred runs dispatch no L1 auditors and
need no dispatch receipts. Runs without the Codex client marker retain their existing
contract, so an older client is not silently labelled as receipt-enforced.

The Codex Step 1 instructions require the complete brief in every L1 prompt, including
every listed bug and lesson id. They name `prior_block`, `lessons_block` and `prior_brief`
in the run summary and distinguish preparation receipts from launch enforcement.

## Environment inventory

Local Python imported CCA from the Python 3.14 user site. Its distribution was
`cca-audit` 0.13.0, installed from the wheel built at `801c527`. The normalized source
hashes of `pipeline_state.py`, `cca_gate_core.py` and `cca_pipeline_guard.py` matched that
revision before this work.

On VPS2, the default `python3 -P` could not import CCA and no Codex executable was found
on the inspected SSH PATH. The existing Codex anchor-parity verification environment
reported 0.13.0. Its `pipeline_state.py` hash was
`6a5668e8892ee2bd52302f73cf4fbfb9be47cd714b4bcf6728d45ecd58e035e6`, and its gate core hash was
`7937fe0658d7b59b3d47fe6b145b1bcbf5a25bd45f8092f7dae28db47ba58475`.
Those files predate #102. The shared test-run environment also reported 0.13.0, with an
editable source named `claude-auditor-instructions` and different hashes. Neither the
version label nor these test environments identifies the interpreter every historical
Codex audit used. A harvested run with host `sentiment-vps2` proves that remote CCA runs
occurred, but its metadata does not record the Python executable or package revision.

No VPS2 package, service, serving checkout, database or schema was changed by this work.
The hosted CCA stats reported 1,617 chunks, newest index time
`2026-10-28T19:35:53.456066+00:00`, and `stale: false`. That establishes corpus freshness
as reported by the server, not package parity on another interpreter.

## Validation record

Behavioral tests and their exact production mutations are documented in
`tests/test_codex_dispatch_receipts.py`. The private raw hook payloads and command
receipts are retained outside the public repository. Raw prompts, private paths and
memory text are not included in this report.

All 14 focused cases passed. Six deliberate production mutations made the corresponding
cases fail at their intended assertions, and the same cases passed after restoration.
An earlier mutation attempt changed CRLF bytes incorrectly and caused a syntax error;
it was rejected as evidence, restored, and rerun with byte-preserving mutations.
Ruff and Pyright passed. These commands repeat the focused validation from the checkout:

```text
python -m pytest tests/test_codex_dispatch_receipts.py -q -o addopts=
python -m ruff check cca_checks tests
python -m pyright
```

The installed local package completed a real FAST DIFF no-fix run named
`codex-memory-smoke-20261028`, on a one-line docstring clarification in `assert_bounded`.
Three independent Codex auditors received the complete local brief, acknowledged its
three exact record ids and returned no findings. L1, L2 and L2.5 were recorded; the run
closed and `pipeline status --require-complete` reported no open run. Its summary held:

```json
{
  "prior_block": {"exact": 0, "symbol_changed": 3, "file_only": 0},
  "lessons_block": 0,
  "prior_brief": {
    "source": "local",
    "status": "ok",
    "ids": [
      "cca-audit:33e1c3a5c59d:NUM-001",
      "cca-audit:33e1c3a5c59d:NUM-004",
      "cca-audit:33e1c3a5c59d:NUM-007"
    ]
  }
}
```

This smoke run used the installed `801c527` package, so it proves the shared data and
local summary path from Codex. The new preparation gate is separately covered by the
focused CLI test that starts a Codex run, checks every role and closes with summary ids.
The smoke run changed no executable behavior and therefore ran no runtime test suite.
Its capability check reported Python taint unavailable because Semgrep did not answer
its version probe. Hosted `recall_records` was unavailable, so no hosted record envelope
or prior false-positive suppression was used. A no-fix FAST run owes no fix, differential
or architect layers and offers no deferred pass.

The full suite is required in CI. A local focused run is not a substitute for that gate.
The first CI run failed only the existing
`test_codex_builds_the_brief_once_before_the_role_list` contract: inserting the checklist
inside the policy split its paragraph before the hosted instructions. The checklist was
moved after the brief policy, leaving the existing test unchanged. Python 3.12 reported
1,726 passed, five skipped and that single failure before the correction.

An independent bug review found no introduced correctness defect. It noted the existing
hosted-only guard checks a heading rather than every hosted record id. This change reuses
that shared rule and does not extend hosted validation. The local brief path checks ids.
Installation remains pending the owner's PR merge, under the shared deployment checklist.
