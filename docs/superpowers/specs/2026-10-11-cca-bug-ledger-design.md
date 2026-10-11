# CCA bug ledger: counting the real bugs CCA fixed, across projects

Status: design approved section by section on 2026-10-11; this document awaits review.

## 1. Goal

Answer one question with data rather than recollection: **how many real bugs has CCA fixed?**

A bug is **real** when four things hold:

1. its finding was CONFIRMED at L2.5 (fp-check);
2. it was fixed, and the fix landed on the project's default branch;
3. the L6 architect APPROVED the fix;
4. its test was proved RED against the pre-fix code at L5.6.

Each bug gets a row (date, project, files, category, severity, fix commit) and a memo (what the bug
was, the fix, the evidence). The memos are indexed into a RE-call tenant named `cca`, so a later
session can ask "has CCA fixed a bug like this before?".

## 2. What exists today, and why it is not enough

Measured 2026-10-11 on the author's workstation.

* The only ledger is per project: `.claude/audits/AUDITOR_SCORECARD.jsonl`, disposition rows keyed
  by `verdict` and outcome rows keyed by `result`, joined on `run_id` + `fix_id`. Seven distinct
  ledgers exist, covering ten runs between 2026-08-26 and 2026-09-30; the other copies found are the
  same file inside worktrees.
* Ledger rows carry **no file, no fix commit and no description**. The outcome vocabulary drifts
  (`fixed`, `FIXED`, `DEFERRED`, `PARTIAL`). In one run every outcome row says `fixed` or
  `deferred` while every architect verdict reads `BLOCKED`.
* L5.6 records only per-run totals, even after GiulioDER/cca-audit#60 (`"3 RED, 0 tautological,
  0 inconclusive"`). The per-finding proof is printed by `cca_tautology_check.py verify --json` and
  then lost. One run saved it by hand.
* `.claude/audits/` is gitignored and lives per worktree, so a deleted worktree takes its ledger
  with it.
* Git holds far more than the ledgers. CCA fix commits ("N audit fixes from CCA (TIER tier)") list
  each finding as a bullet with its ID, a description and its test, under P1 and P2 headings. Most
  such runs have no ledger at all. Six repositories carry them, two of them private.

Re-measure the commit side, per clone (clones of one remote repeat the same count):

```bash
for d in */; do git -C "$d" log --all --oneline -i -E --grep='audit fixes? (from|for) |from CCA|CCA (audit|self-audit)' 2>/dev/null | wc -l; done
```

So the strict definition cannot be met by most of the history: L5.6 is newer than most runs, and
L6 was rarely written down per finding.

## 3. Decision: two tiers

* **`proved`**: all four conditions are `evidenced` (see §4). This is the number to cite.
* **`historical`**: everything else that was fixed and landed, rebuilt from git and the ledgers, with
  each condition marked for what can and cannot be shown.

A fix that was parked, reverted or deferred gets no row. A fix that landed under an L6 BLOCKED does
get a row, with `l6_approved: "absent"`, so it is visible rather than silently dropped.

Rejected alternatives: extending each project's ledger and gathering them on demand (loses bugs with
every deleted worktree), and counting by querying the tenant (a vector search ranking is not a
count, and cannot be reproduced).

## 4. Data model

One append-only file, `~/.claude/cca-bugs/bugs.jsonl` (directory overridable with `CCA_BUGS_DIR`).
It lives outside every repository: rows name private projects and their file paths, and this
repository is public.

```json
{"bug_id": "recall:6aa7ff5bd:STAKES-001", "tier": "proved|historical",
 "date": "2026-10-07", "project": "recall", "repo": "GiulioDER/RE-call",
 "files": ["recall/lite/store.py"], "finding_id": "STAKES-001",
 "category": "atomicity", "severity": "P1", "auditor": "STAKES",
 "fix_commit": "6aa7ff5bd", "run_id": null,
 "evidence": {"confirmed_l25": "evidenced", "fixed": "evidenced",
              "l6_approved": "claimed", "red_proof": "claimed"},
 "sources": ["git:6aa7ff5bd", "ledger:AUDITOR_SCORECARD.jsonl#L41"],
 "summary": "<the commit's own bullet text, verbatim>",
 "supersedes": null}
```

Rules:

* **Identity** is `project:fix_commit:finding_id`. Finding IDs restart in every run, so the commit is
  part of the key. The same run copied into several worktrees collapses to one row.
* **Evidence values**:
  * `evidenced`: an artifact on record (a ledger verdict row, an outcome row with APPROVED, a proof
    file with RED, a commit reachable from the default branch);
  * `claimed`: text says so (a commit message's "red at PR head", "+test") and nothing backs it;
  * `absent`: neither.
* **`tier` is computed, never written by hand**: `proved` if and only if all four are `evidenced`.
* **`summary` is quoted, never generated.** No model writes a bug description into the ledger.
* **`category`** comes from the ledger row when one matches; otherwise it is `null`. **`auditor`**
  comes from the ledger or from the finding ID prefix (`STAKES-001` → `STAKES`). **`severity`** comes
  from the ledger or from the P1/P2 heading the bullet sits under.
* **Corrections** append a new row whose `supersedes` names the old `bug_id`; nothing is edited.

Each row has one memo, `~/.claude/cca-bugs/memos/<bug_id with ':' replaced by '__'>.md`, in the
shared front-matter format (`name`, `description`, `valid_from`, `metadata.type: project`), with
three sections: **The bug** (the quoted summary), **The fix** (commit, files, diffstat), **The
evidence** (each condition, its value and its source). A memo is generated from the row by a
template; it adds no text the row does not hold.

## 5. Backfill (historical tier)

`python -m cca_checks bugs backfill --repo <path> [--repo <path> ...] [--ledger <path> ...] [--write]`

Read-only against every repository and ledger.

1. **One pass per repository, not per clone.** Paths are grouped by `remote.origin.url`; the first
   clone of each remote is used.
2. **Fixed means landed.** Only commits reachable from the remote's default branch
   (`refs/remotes/origin/HEAD`, else `origin/master`, else `origin/main`) are read. A fix only on an
   unmerged branch gets no row.
3. **Commit selection** by subject, the pattern in §2.
4. **Bullet parsing is deterministic.** A line `- <ID>: text` or `- <ID> text`, where `<ID>` is
   `[A-Z]+-\d+` optionally joined by `/` or `, ` (as in `CODE-001/002`, `CODE-003, DOC-003`), under a
   heading `P1...` or `P2...`, becomes one bug per ID with continuation lines joined into the
   summary. Sections headed `Deferred`, `Owner decision`, `not fixed`, `P3` are skipped. `files` is
   the commit's changed paths, because per-bug attribution was never recorded.
5. **Squash duplicates collapse.** Within one repository, the same finding ID with the same
   normalised summary in two commits (an original and its squash merge) is one bug; the row keeps the
   commit reachable from the default branch.
6. **Ledger join.** A ledger run matches a commit when its date is within 3 days before the commit
   and at least half of its CONFIRMED finding IDs appear in the commit's bullets. A match upgrades
   `confirmed_l25` to `evidenced` for the matched IDs, and `l6_approved` where the outcome row says
   `APPROVED`. A saved proof file (`redproof-*.json`) beside the ledger upgrades `red_proof` for its
   findings when its verdict is `RED`. Ambiguous matches (two runs qualify) upgrade nothing and are
   reported.
7. **Unparsed commits are reported, not guessed.** A selected commit that yields no bullet goes to
   `unparsed.txt` with its sha and subject. The total is printed as a lower bound with that count
   next to it.

Without `--write` it prints a dry-run summary: rows per repository, per tier, per evidence value,
unparsed count. With `--write` it appends rows not already present (by `bug_id`) and writes memos.
Running it twice writes nothing the second time.

## 6. Forward path (proved tier)

**Built after GiulioDER/cca-audit#60 merges**, because it touches files that PR changes
(`cca_tautology_check.py`, `audit-fix.md`).

1. `cca_tautology_check.py verify` writes its per-finding results to
   `.claude/audits/redproof-<run_id>.json` as well as wherever it reports today.
2. The Step 2.5b disposition row gains `file`, `line` and `title`, copied from the finding schema,
   which already has them. `cca_scorecard.py` ignores keys it does not read.
3. Step 7 runs `python -m cca_checks bugs record --commit HEAD` after the fix commit and **before**
   `pipeline close`, while the run state still exists. It joins the run state, the run's ledger rows
   and the proof file with the commit's diff, and writes one row and memo per fixed finding:
   `proved` where all four conditions are evidenced, `historical` with `absent` marks otherwise.
4. It is bookkeeping, not a gate. On any error it prints the reason and exits 0; the run still
   closes. Gates belong to #60's rules module and this design adds none.

Accepted consequence: L5.6 requires a proof only for P1 fixes, so a P2 fix without a written proof
stays `historical` even when everything else is evidenced. Requiring proofs for P2 would change the
pipeline and is a separate decision.

Runs on other hosts write their own ledger under their own `~/.claude/cca-bugs/`. Gathering those is
out of scope.

## 7. The `cca` tenant

Operational scripts live in the operator's private configuration repository, not here, because they
name a host, a user and server paths. This section fixes only the design.

1. **Separate staging.** The memos ship to their own staging folder on the indexing host. They are
   never placed in the folder that feeds the `memory` tenant, which would index them twice.
   Shipping adds and updates; it never deletes.
2. **Same pipeline as the existing tenants**: manifest, generation build, validate, calibrate,
   promote, generation gc. The build waits in the host's embedding queue (one embedding process at a
   time), runs under a cgroup memory and CPU cap so the host cannot run out of memory, and a
   read-only `--check` reports state before every build. Embedder `voyage:voyage-4`, as for
   `memory`, since the memos are prose.
3. **Nothing vanishes from the source**, so re-indexing prunes nothing: the ledger is append-only and
   a correction is a new memo with `supersedes:`.
4. **A labelled query set before the first promote**, following the existing memory tenant's
   draft-then-verify pattern. Answerable queries are phrased as a person would ask, never copied
   from a memo's own text. Most queries are unanswerable, and each is checked against the index
   before use. Until calibration is certified the generation is built and not served. The expected
   separability is pre-registered before the calibration run.
5. **Serving** starts with the CLI (`--tenant cca search`). An MCP server entry is a later step.

## 8. Testing

Every behaviour test is shown red before it is trusted green, against a named mutation, and the
receipt goes in its docstring.

| Test | Invariant | Mutation that must turn it red |
|---|---|---|
| parse a P1/P2 commit fixture (both message styles seen in real history) | one bug per ID, summary verbatim | drop the continuation-line join |
| skip Deferred, Owner decision, P3 sections | no row for an unfixed finding | remove `Deferred` from the skip set |
| multi-ID bullets (`CODE-001/002`, `CODE-003, DOC-003`) | one row per ID | split on `/` only |
| squash duplicate in a temp git repo | one row, the default-branch commit | key dedup on commit instead of (ID, summary) |
| unmerged fix commit in a temp git repo | no row | read `--all` instead of the default branch |
| commit text "red at PR head" with no proof file | `red_proof: claimed`, tier `historical` | treat claimed as evidenced |
| BLOCKED architect with `fixed` outcome | row present, `l6_approved: absent` | read `result` instead of `architect_verdict` |
| ambiguous ledger match | nothing upgraded, reported | take the first match |
| backfill run twice | second run writes nothing | append without the `bug_id` check |
| `bugs record` with all four on record | tier `proved` | ignore the proof file |
| `bugs record` raising internally | exit 0, message printed | let the exception propagate |

Fixture commit messages are written for the tests in the shape of the real ones; no private commit
text enters this repository.

## 9. Build order

1. Backfill, dry run; the author reviews the summary and `unparsed.txt`.
2. `--write`.
3. Sync to the indexing host, write and verify the query set, pre-register, first build and
   calibration, promote.
4. Forward path, after #60 merges.

## 10. Out of scope

* Gathering ledgers from other hosts.
* Requiring L5.6 proofs for P2 fixes.
* An MCP server for the `cca` tenant.
* Any change to gates or to what the commit guard enforces.

## 11. Corrections found while planning (2026-10-11)

These were found by reading the real data while writing the plan, after the sections above were
approved. The text above is left as approved; where it disagrees with this section, this section
wins.

1. **§5.6, the ledger join, cannot work as written.** Ledger rows key findings by a run-local
   `fix_id` (`FIX-1`, `FIX-01`, `FIX-001`), not by the auditor-style ID a commit bullet names
   (`STAKES-001`); only one ledger also carries a `finding` key. A run is therefore matched on the
   keys both sides actually share: `FIX-n` IDs named in commit bullets (normalised, so `FIX-01` and
   `FIX-1` are equal), and `finding` values where a ledger has them. The window is from 3 days
   before the commit to 1 day after it (timestamps mix time zones). A bullet such as `STAKES-001`
   in a run whose ledger has no `finding` key gets no per-bug upgrade and stays `claimed`, because
   nothing on record says which `FIX-n` it was. Measured: recall `0cfa72199` names FIX-1 to FIX-6
   of run `pr805a`, the one run that saved its proofs.
2. **§7.2, the embedder.** The `memory` tenant's pipeline defaults to
   `voyage-context:voyage-context-4` (provider `voyage-context`), not `voyage:voyage-4`. The `cca`
   tenant uses whatever that pipeline uses, read from it at build time rather than restated.
3. **§7.4, the first calibration.** The existing scripts only carry a calibration forward or refit
   it on a stored set, and a new tenant has neither. The first one is `recall calibration calibrate
   --generation <gen> --queries <file> --publish`; later rebuilds use `calibration auto`.
4. **§4, the memo.** It lists files, not a diffstat: the row holds no diffstat, and §4 says a memo
   adds no text the row does not hold.

Also settled while planning, where the spec was silent:

* Only clones whose `origin` is a GitHub URL are read; a clone of a local bundle would otherwise
  count the same bugs under a second name. Skipped clones are listed in the report.
* Bullets of the form `- FIX-1, text` (comma after the ID) are parsed, as found in real history.
* When two reachable commits carry the same finding with the same summary, the earlier committer
  date wins.
* `red_proof` starts as `claimed` only when the bullet mentions a test (`+test`, `test_name`,
  "red"); otherwise it starts `absent`.
