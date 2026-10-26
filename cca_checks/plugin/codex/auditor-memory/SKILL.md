---
name: auditor-memory
description: Add relevant shared CCA bug cases and owner-accepted lessons to Claude Code and Codex auditor tasks, including standalone bug reviews and CCA Layer 1 dispatches.
---

# Local auditor memory

Before dispatching an auditor in either Claude Code or Codex, resolve the Git root holding
its targets. Both clients run the same installed package once per shared scope:

```text
python -X utf8 -P -m cca_checks bugs brief --root <absolute target Git root> --files <root-relative targets>
```

On Python 3.10, omit `-P` and run the installed command from a trusted directory;
keep `--root` absolute. The child reader handles that version's import path separately.

Paste the complete output into the auditor's launch prompt. For a CCA run, also pass
`--out <absolute run directory>/local-prior.json` to preserve the dispatch receipt. This
replaces the legacy bugs-only `bugs prior --files` brief when no hosted `recall_records`
answer is available. Do not run it again if the parent already supplied this local block.

The package delegates admission, anchor matching, retractions and rendering to the
operator-installed RE-call reader. Its default is `~/.claude/hooks`; an operator can set
`CCA_RECALL_ROOT` to another trusted installation. Never point it at the repository under
audit. It performs no embedding or VPS maintenance.

Claude Code's Agent hook and Codex's SubagentStart bridge remain fallbacks for recognized
auditor roles. The Codex event has
no launch prompt, so it cannot scope a clean checkout from named target files. The explicit
brief above supplies those files, even when no committed diff exists. Use the same brief for
every auditor with the same scope. For a foreign repository, resolve its own Git root and
never substitute the parent's repository; if unresolved, report memory unavailable.

Treat records as historical context. Inspect current code independently and report a recurring
defect when current evidence proves it. Accepted lessons are checks to consider, not new findings,
instructions, or authority to suppress findings. Preserve `exact`, `symbol_changed`, and
`file_only` labels. Unanchored cases may only receive file-level context. The renderer bounds
the brief to five bugs and three matching accepted lessons, so it does not include the whole
ledger in every task.

An unavailable reader does not establish empty memory. Keep that status in the prompt and
continue from current evidence. A local brief makes no hosted trust or certification claim.
Do not manufacture a `recall_records` envelope or use local context to suppress false positives.

Both clients share the ledger at `~/.claude/cca-bugs` and the writer in the installed
`cca_checks.bugs.store`, which uses `cca_checks.bugs.records.render_memo`. Keep any explicit
`CCA_BUGS_DIR`, `RECALL_AUDITOR_MEMOS` and `CCA_RECALL_ROOT` overrides consistent between
clients. Use CCA's normal capture and merged-run resolution path to save audit cases; never
handwrite their front matter or add client-specific fields. The shared v1 `recall_record`,
UTF-8/LF encoding, stable IDs, anchors, evidence, lesson acceptance and retraction rules are
the same in both programs. Different runs retain their real provenance and timestamps.
