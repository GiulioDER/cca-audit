# Local auditor memory

`python -P -m cca_checks bugs brief --root <target Git root> --files <targets>` uses the
operator-installed `recall_hooks.auditor_brief` reader for local CCA bugs and accepted lessons.
This supports installations whose hosted RE-call server does not expose `recall_records`.
It requires that shared reader and its RE-call Python dependencies. It does not upgrade them.

The default reader lives at `~/.claude/hooks`. `CCA_RECALL_ROOT` can name another trusted
installation, containing `recall_hooks/auditor_brief.py`. The child interpreter runs outside
the target checkout with `PYTHONPATH` removed. Do not use a target repository as the reader.
The shared reader controls `CCA_BUGS_DIR`, `RECALL_AUDITOR_MEMOS`, admission, temporal
validity, retractions, repository matching, anchors and formatting.

Explicit filenames travel as structured file anchors, including names with spaces or Unicode.
CCA uses its existing bounded file reader and canonical hash, then the shared RE-call record
reader, matcher and renderer. The shared diff scope adds any changed-symbol context. Missing
or unreadable files carry `unavailable` as their hash and cannot establish an exact code match.
Scopes over the shared 200-anchor limit return unavailable rather than silently dropping files.

`--json` reports `ok`, `empty`, or `unavailable`, with the rendered block. `--out FILE`
saves that same result while ordinary stdout remains ready to paste into an auditor prompt.
The operation is bounded and fail open. An unavailable reader remains explicit and never
becomes a claim that no memory exists. Local status is separate from hosted trust.

CCA's DIFF path uses this brief when no hosted record tool is available. HUNT uses the
target repository's local brief, with its existing foreign-root checks. A hosted refusal
remains a refusal and is not rewritten as a successful local envelope. Local receipts use
`local-prior.json`; hosted replies keep their separate `prior-records.json` contract and
measurement counters. No local record authorizes false-positive suppression.

For standalone auditors, copy the same packaged `codex/auditor-memory/SKILL.md` to both
`~/.codex/skills/auditor-memory/SKILL.md` and `~/.claude/skills/auditor-memory/SKILL.md`.
This is one portable skill resource despite its package directory name. Reference it in
each client's auditor dispatch guidance. CCA's regular installer continues to install its own CCA skill.
The explicit handoff is necessary for clean-checkout reviews because SubagentStart has
no target-file prompt. The existing native hook can still add context for committed diffs.

Claude Code and Codex use the same `cca_checks.bugs.store.write` and
`cca_checks.bugs.records.render_memo`, not separate client serializers. The default shared
ledger and memo root is `~/.claude/cca-bugs`. Agree any environment override in both clients.
All record kinds use the pinned v1 contract and golden front matter; do not add client tags
to those records or replace their actual run provenance. The same repository, target files,
corpus and validity instant must yield the same brief in both clients. Hook event envelopes
remain client-specific, outside the stored record and shared brief.
