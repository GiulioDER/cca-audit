# Deferred Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After a CCA run commits and closes, offer a RUN or SKIP recommended second pass over everything it deferred, and make that pass a fully gated CCA run that verifies every deferred finding before editing it and can never defer again.

**Architecture:** Three new pieces around the existing run state machine. `cca_checks/layer_outputs.py` checks the shape of the L2, L2.5 and L3 work files so they agree with the counts recorded beside them. `cca_checks/deferred.py` holds the pure rules: the pool, the run summaries, and the offer. `cca_checks/pipeline_state.py` gains the `DEFERRED` mode, the base commit, the summary written at `close`, and two facade functions the CLI calls; the gate core gains only the `MODES` tuple. The prose of `/audit-fix` and the Codex skill then tells the orchestrator to use them.

**Tech Stack:** Python 3.10+, stdlib only in `cca_checks`; pytest; ruff; pyright; git (called through the gate core's trusted git lookup).

**Spec:** `docs/superpowers/specs/2026-10-11-deferred-pass-design.md` (read it before Task 1; this plan argues from it).

## Global Constraints

- Python 3.10 to 3.13; nothing outside the stdlib in `cca_checks/` (the hooks load `cca_gate_core.py` by path and must stay stdlib only).
- The run state schema stays `2` (`core.SCHEMA == 2`). New state fields (`base_commit`, `parent`) are optional with default `None`.
- `DEFERRED_MIN_P3 = 3`, a named constant, documented as an unmeasured starting value.
- Every `pipeline` subcommand prints one JSON object. A refusal prints `{"refused": <subcommand>, "reason": ...}` to stderr and exits 2; a `ValueError` prints `{"error": ...}` and exits 1.
- Layer work files and the `--write` file must lie under `<root>/.claude/audits/`.
- The summary path is `.claude/audits/runs/<run_id>/summary.json`, `"schema": 1`, with exactly the keys in spec § 2.5.
- Verdicts in an L2.5 file: `CONFIRMED`, `FALSE_POSITIVE`, `UNCERTAIN`, `DUPLICATE`, `DEFERRED`, `STALE`.
- **Red proof (standing user rule).** A new or changed behaviour test counts only after it was seen to fail, in its intended assertion, against the pre-change code or a named plausible mutation of the production line it protects. ImportError, collection errors and a missing symbol are not red proof: mutate the new implementation instead. Record in the test docstring: `Red <YYYY-MM-DD>: mutation <what changed> in <symbol>; failed at <assertion>.` Restore the code and run green.
- New prose (docs, prompts, docstrings): no em dash or en dash as punctuation; use commas, colons, semicolons, parentheses or full stops.
- Before claiming any task done: the task's tests green, then `python -m pytest -q`, `ruff check`, `python -m pyright`. CI runs pyright before pytest.
- Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.
- Work only in the worktree `.claude/worktrees/deferred-pass` (branch `feat/deferred-pass`). Never touch the user-wide CCA install or any `.claude/commands`, `.claude/agents`, `.claude/hooks` copy.

## Review Focus

Inputs the spec implies but its own test list does not exercise, most likely to bite first. Each has a test in the task named.

1. **A parent run directory without its `L2.json`** (the user deleted `.claude/audits/runs/<id>/L2.json`, or copied only the summary): `pipeline deferred` must exit 2 naming the missing file, never crash with a traceback. Task 5 (`findings_for`) and Task 7 (CLI).
2. **A corrupt `summary.json`**: it must be listed under `unreadable` in the report, not skipped silently, because a skipped summary hides a pool. Task 5.
3. **An aborted deferred run**: the parent's pool must stay on offer, since only a completed deferred run consumes it. Task 6.
4. **A new run whose `--run-id` names a completed run**: `start` must refuse, or the new run's records would overwrite the parent's stored work the deferred pass reads. Task 5.
5. **A parent whose commit was rewritten away** (reset or rebase after the run closed): the report must say `commit_reachable: false` with a reason, because `fp-check` then cannot tell STALE from FALSE_POSITIVE. Task 7.

---

### Task 1: Gate tests write layer files in the shapes `record` will check

A test harness change with no production change. **Nonbehavioral control, exempt from red proof:** it adds fixture helpers and makes existing tests write well-shaped files; every test it touches must stay green before and after, and no assertion changes.

**Files:**
- Modify: `tests/conftest.py` (add helpers after `write_output`, add one import)
- Modify: `tests/test_pipeline_state.py`, `tests/test_pipeline_cli.py`, `tests/test_pipeline_guard.py`, `tests/test_stop_guard.py`

**Interfaces:**
- Produces (in `tests/conftest.py`): `finding_id(i: int) -> str` (returns `"F-<i>"`); `findings_output(root, n=0, *, priorities=None) -> str`; `verdicts_output(root, **counts) -> str`; `plan_output(root, *, to_fix=0, deferred=0) -> str`; `append_ledger(repo, run_id, n, *, outcome_rows=0, outcome_result="fixed") -> None`. Ids are handed out in the order CONFIRMED, FALSE_POSITIVE, UNCERTAIN, DUPLICATE, DEFERRED, STALE, so `plan_output` over the first `to_fix + deferred` ids names only CONFIRMED findings.

- [ ] **Step 1: Run the suite to record the green baseline**

Run: `python -m pytest -q`
Expected: all pass. Note the count.

- [ ] **Step 2: Add the helpers to `tests/conftest.py`**

Insert directly after `write_output`:

```python
#: The order verdicts_output hands ids out in. CONFIRMED come first, so a plan_output over
#: the first `to_fix + deferred` ids names only findings L2.5 confirmed.
_VERDICT_ORDER = (("confirmed", "CONFIRMED"), ("fp", "FALSE_POSITIVE"),
                  ("uncertain", "UNCERTAIN"), ("duplicate", "DUPLICATE"),
                  ("deferred", "DEFERRED"), ("stale", "STALE"))


def finding_id(i: int) -> str:
    return f"F-{i}"


def findings_output(root, n=0, *, priorities=None) -> str:
    """An L2 file of `n` consolidated findings, F-1 to F-n, each P2 unless `priorities`
    (a dict of id to P1, P2 or P3) says otherwise."""
    priorities = priorities or {}
    rows = [{"id": finding_id(i), "auditor": "cca-bug-auditor", "category": "x",
             "priority": priorities.get(finding_id(i), "P2"), "file": "app.py", "line": i,
             "claim": f"claim {i}", "high_stakes": False} for i in range(1, n + 1)]
    return write_output(root, rows)


def verdicts_output(root, **counts) -> str:
    """An L2.5 file whose verdicts match `counts`, keyed like the record flags
    (confirmed, fp, uncertain, duplicate, deferred, stale)."""
    rows, i = [], 1
    for key, verdict in _VERDICT_ORDER:
        for _ in range(counts.get(key, 0)):
            rows.append({"id": finding_id(i), "verdict": verdict})
            i += 1
    return write_output(root, rows)


def plan_output(root, *, to_fix=0, deferred=0) -> str:
    """An L3 file planning the first `to_fix` CONFIRMED ids and deferring the next `deferred`."""
    ids = [finding_id(i) for i in range(1, to_fix + deferred + 1)]
    return write_output(root, {"to_fix": ids[:to_fix], "deferred": ids[to_fix:]})


def append_ledger(repo, run_id, n, *, outcome_rows=0, outcome_result="fixed") -> None:
    """`n` verdict rows and `outcome_rows` outcome rows for `run_id` in the disposition ledger."""
    path = pathlib.Path(repo) / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        for i in range(n):
            fh.write(json.dumps({"run_id": run_id, "auditor": "cca-bug-auditor",
                                 "category": "x", "verdict": "CONFIRMED",
                                 "fix_id": f"FIX-{i}"}) + "\n")
        for i in range(outcome_rows):
            fh.write(json.dumps({"run_id": run_id, "fix_id": f"FIX-{i}",
                                 "result": outcome_result,
                                 "architect_verdict": "APPROVED"}) + "\n")
```

- [ ] **Step 3: Move `_ledger` out of `tests/test_pipeline_state.py`**

Delete the local `def _ledger(...)` (lines 23 to 33), change the conftest import line to:

```python
from conftest import (append_ledger, finding_id, findings_output, plan_output, tree_of,
                      verdicts_output, write_output)
```

and replace every `_ledger(` call in that file with `append_ledger(`.

- [ ] **Step 4: Give every L2, L2.5 and L3 record a well-shaped file**

Find every site:

```bash
grep -n -E 'record\("(L2|L2\.5|L3)"' tests/test_pipeline_state.py tests/test_pipeline_guard.py tests/test_stop_guard.py
grep -n -E '"record", "(L2|L2\.5|L3)"' tests/test_pipeline_cli.py
```

Replace the `output=` argument by this rule, using the counts already on that line:

| Record | Replace `write_output(X)` with |
|---|---|
| `"L2"` with `unique: N` (or `--unique N`) | `findings_output(X, N)` |
| `"L2.5"` with `confirmed: C, fp: F, uncertain: U, duplicate: D` | `verdicts_output(X, confirmed=C, fp=F, uncertain=U, duplicate=D)` |
| `"L3"` with `to_fix: K` | `plan_output(X, to_fix=K)` |

Import the helpers you use from `conftest` in each file. Leave these sites alone, because the file itself is what they test: `test_record_l2_without_an_output_file_is_refused`, `test_record_l2_with_an_output_that_is_not_json_is_refused`, `test_an_output_outside_claude_audits_is_refused_and_one_inside_is_kept`, `test_dot_dot_inside_claude_audits_is_accepted_and_escaping_it_is_refused`. In `test_record_l2_copies_a_valid_output_into_the_run_and_names_it`, change the findings literal to `[{"id": "BUG-1", "priority": "P2", "file": "app.py", "line": 1}]`.

Two helpers need an explicit edit. `_through_l3` becomes:

```python
def _through_l3(repo, tier, *, confirmed=1, no_fix=False):
    state = ps.start(tier, no_fix=no_fix, root=repo)
    ps.record("L1", detail="6 auditors", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": confirmed}, output=findings_output(repo, confirmed),
              root=repo)
    if tier != "FAST":
        append_ledger(repo, state.run_id, confirmed)
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": 0,
                              "duplicate": 0},
              output=verdicts_output(repo, confirmed=confirmed), root=repo)
    if tier != "FAST":
        ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    if not no_fix:
        ps.record("L3", counts={"to_fix": confirmed},
                  output=plan_output(repo, to_fix=confirmed), root=repo)
    return state
```

`_through_l3_fast` plans `to_fix` of one confirmed finding, so the rest is deferred (Task 3 will require that every confirmed finding is planned or deferred; today the extra count is stored and ignored):

```python
def _through_l3_fast(repo, to_fix):
    ps.start("FAST", root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": 1}, output=findings_output(repo, 1), root=repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo, confirmed=1), root=repo)
    ps.record("L3", counts={"to_fix": to_fix, "deferred": 1 - to_fix},
              output=plan_output(repo, to_fix=to_fix, deferred=1 - to_fix), root=repo)
```

- [ ] **Step 5: Run the suite; it must be exactly as green as Step 1**

Run: `python -m pytest -q`
Expected: same count, all pass. `ruff check` clean.

- [ ] **Step 6: Commit**

```bash
git add tests/conftest.py tests/test_pipeline_state.py tests/test_pipeline_cli.py tests/test_pipeline_guard.py tests/test_stop_guard.py
git commit -F - <<'EOF'
test: gate tests write L2, L2.5 and L3 files in the shapes record will check

Nonbehavioral harness change: shared helpers in conftest, no assertion changed.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 2: `layer_outputs`, the shape checks for L2, L2.5 and L3 files

**Files:**
- Create: `cca_checks/layer_outputs.py`
- Test: `tests/test_layer_outputs.py`

**Interfaces:**
- Produces: `class ShapeError(ValueError)`; `PRIORITIES = ("P1", "P2", "P3")`; `COUNT_OF: dict[str, str]` (verdict to count key); `VERDICTS: tuple[str, ...]`; `l2_findings(data, unique: int) -> list`; `l25_verdicts(data, counts: dict, l2_ids) -> dict[str, str]`; `l3_plan(data, to_fix: int, deferred: int, l25: dict[str, str]) -> dict[str, list[str]]`.

- [ ] **Step 1: Write the tests**

```python
"""The L2, L2.5 and L3 work files must agree with the counts recorded beside them.

A later run reads these files back: the deferred pass finds its findings by id in L2 and its
pool in L2.5 and L3. A file that disagreed with its counts would hand that run a different pool
from the one the gate counted.
"""

from __future__ import annotations

import pytest

from cca_checks import layer_outputs as lo


def _f(i, priority="P2"):
    return {"id": f"F-{i}", "priority": priority}


def test_l2_accepts_findings_that_match_unique():
    assert lo.l2_findings([_f(1), _f(2, "P3")], 2) == [_f(1), _f(2, "P3")]


def test_l2_refuses_a_count_that_disagrees_with_the_file():
    """Red: mutation `if len(data) != unique:` became `if False:` in l2_findings;
    failed at pytest.raises (DID NOT RAISE ShapeError)."""
    with pytest.raises(lo.ShapeError, match="holds 1 findings but --unique is 2"):
        lo.l2_findings([_f(1)], 2)


def test_l2_refuses_a_finding_without_a_priority():
    """Red: mutation the priority loop in l2_findings deleted; failed at pytest.raises."""
    with pytest.raises(lo.ShapeError, match="priority"):
        lo.l2_findings([{"id": "F-1"}], 1)


def test_l2_refuses_an_id_named_twice():
    """Red: mutation `if dupes:` became `if False:` in _ids; failed at pytest.raises."""
    with pytest.raises(lo.ShapeError, match="more than once"):
        lo.l2_findings([_f(1), _f(1)], 2)


def test_l2_refuses_a_file_that_is_not_an_array():
    with pytest.raises(lo.ShapeError, match="JSON array"):
        lo.l2_findings({"F-1": {}}, 1)


def test_l2_5_maps_ids_to_verdicts_when_the_tally_matches():
    data = [{"id": "F-1", "verdict": "CONFIRMED"}, {"id": "F-2", "verdict": "DEFERRED"}]
    got = lo.l25_verdicts(data, {"confirmed": 1, "deferred": 1}, ["F-1", "F-2"])
    assert got == {"F-1": "CONFIRMED", "F-2": "DEFERRED"}


def test_l2_5_refuses_a_tally_that_disagrees_with_the_counts():
    """Red: mutation `wrong = [...]` became `wrong = []` in l25_verdicts; failed at
    pytest.raises (DID NOT RAISE ShapeError)."""
    data = [{"id": "F-1", "verdict": "CONFIRMED"}, {"id": "F-2", "verdict": "CONFIRMED"}]
    with pytest.raises(lo.ShapeError, match="disagrees with its counts"):
        lo.l25_verdicts(data, {"confirmed": 1, "fp": 1}, ["F-1", "F-2"])


def test_l2_5_refuses_an_id_l2_did_not_record():
    """Red: mutation the `row["id"] not in known` check deleted; failed at pytest.raises."""
    with pytest.raises(lo.ShapeError, match="L2 did not record"):
        lo.l25_verdicts([{"id": "X-9", "verdict": "CONFIRMED"}], {"confirmed": 1}, ["F-1"])


def test_l2_5_refuses_an_unknown_verdict():
    with pytest.raises(lo.ShapeError, match="expected one of"):
        lo.l25_verdicts([{"id": "F-1", "verdict": "MAYBE"}], {}, ["F-1"])


def test_l3_accepts_a_plan_over_confirmed_ids():
    l25 = {"F-1": "CONFIRMED", "F-2": "CONFIRMED", "F-3": "DEFERRED"}
    plan = lo.l3_plan({"to_fix": ["F-1"], "deferred": ["F-2"]}, 1, 1, l25)
    assert plan == {"to_fix": ["F-1"], "deferred": ["F-2"]}


def test_l3_refuses_an_id_l2_5_did_not_confirm():
    """Red: mutation `unconfirmed = [...]` became `unconfirmed = []` in l3_plan; failed at
    pytest.raises (DID NOT RAISE ShapeError)."""
    with pytest.raises(lo.ShapeError, match="did not confirm"):
        lo.l3_plan({"to_fix": ["F-3"], "deferred": []}, 1, 0, {"F-3": "DEFERRED"})


def test_l3_refuses_an_id_both_fixed_and_deferred():
    """Red: mutation `if both:` became `if False:` in l3_plan; failed at pytest.raises."""
    l25 = {"F-1": "CONFIRMED"}
    with pytest.raises(lo.ShapeError, match="both fixes and defers"):
        lo.l3_plan({"to_fix": ["F-1"], "deferred": ["F-1"]}, 1, 1, l25)


def test_l3_refuses_a_list_length_that_disagrees_with_its_count():
    """Red: mutation `if len(ids) != want:` became `if False:`; failed at pytest.raises."""
    with pytest.raises(lo.ShapeError, match="lists 1 ids but --deferred is 2"):
        lo.l3_plan({"to_fix": [], "deferred": ["F-1"]}, 0, 2, {"F-1": "CONFIRMED"})


def test_l3_refuses_a_file_that_is_not_the_plan_object():
    with pytest.raises(lo.ShapeError, match='"to_fix" and "deferred"'):
        lo.l3_plan([], 0, 0, {})
```

- [ ] **Step 2: Run the tests; they fail on the missing module**

Run: `python -m pytest tests/test_layer_outputs.py -q`
Expected: collection error, `ModuleNotFoundError: cca_checks.layer_outputs`. This is not red proof (see Step 5).

- [ ] **Step 3: Write `cca_checks/layer_outputs.py`**

```python
"""The shape of the work files L2, L2.5 and L3 store, checked when they are recorded.

`record` used to accept any JSON for these layers. A later run reads them back: the deferred
pass finds its findings by id in L2, and its pool in L2.5 and L3. A file that disagreed with the
counts recorded beside it would hand that run a different pool from the one the gate counted,
so here the file and the counts are made to agree.
"""

from __future__ import annotations

PRIORITIES = ("P1", "P2", "P3")
#: Verdict in an L2.5 file, and the count flag that tallies it.
COUNT_OF = {
    "CONFIRMED": "confirmed", "FALSE_POSITIVE": "fp", "UNCERTAIN": "uncertain",
    "DUPLICATE": "duplicate", "DEFERRED": "deferred", "STALE": "stale",
}
VERDICTS = tuple(COUNT_OF)


class ShapeError(ValueError):
    """A layer file whose shape, or whose agreement with its counts, is wrong."""


def _ids(rows: list, layer: str) -> list[str]:
    ids: list[str] = []
    for n, row in enumerate(rows):
        if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
            raise ShapeError(f'{layer} entry {n} needs a non-empty string "id"')
        ids.append(row["id"])
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    if dupes:
        raise ShapeError(f"{layer} names {', '.join(dupes)} more than once; ids are unique")
    return ids


def l2_findings(data, unique: int) -> list:
    """The consolidated findings: one object per unique finding, each with an id and a priority."""
    if not isinstance(data, list):
        raise ShapeError("L2's file must be a JSON array of finding objects")
    _ids(data, "L2")
    for row in data:
        if row.get("priority") not in PRIORITIES:
            raise ShapeError(f'L2 finding {row["id"]} needs "priority" set to P1, P2 or P3')
    if len(data) != unique:
        raise ShapeError(f"L2's file holds {len(data)} findings but --unique is {unique}")
    return data


def l25_verdicts(data, counts: dict, l2_ids) -> dict[str, str]:
    """One verdict per L2 finding, tallying exactly to the recorded counts. Returns id to verdict."""
    if not isinstance(data, list):
        raise ShapeError('L2.5\'s file must be a JSON array of {"id", "verdict"} objects')
    _ids(data, "L2.5")
    known = set(l2_ids)
    tally = dict.fromkeys(VERDICTS, 0)
    verdicts: dict[str, str] = {}
    for row in data:
        verdict = row.get("verdict")
        if verdict not in COUNT_OF:
            raise ShapeError(
                f"L2.5 entry {row['id']} has verdict {verdict!r}; expected one of "
                f"{', '.join(VERDICTS)}"
            )
        if row["id"] not in known:
            raise ShapeError(f"L2.5 gives a verdict for {row['id']}, which L2 did not record")
        tally[verdict] += 1
        verdicts[row["id"]] = verdict
    wrong = [
        f"--{COUNT_OF[v]} {counts.get(COUNT_OF[v], 0)} but {tally[v]} {v}"
        for v in VERDICTS if tally[v] != (counts.get(COUNT_OF[v]) or 0)
    ]
    if wrong:
        raise ShapeError("L2.5's file disagrees with its counts: " + "; ".join(wrong))
    return verdicts


def l3_plan(data, to_fix: int, deferred: int, l25: dict[str, str]) -> dict[str, list[str]]:
    """The fix plan: CONFIRMED ids to fix and CONFIRMED ids deferred, disjoint, matching counts."""
    if not isinstance(data, dict) or not all(
        isinstance(data.get(k), list) for k in ("to_fix", "deferred")
    ):
        raise ShapeError(
            'L3\'s file must be a JSON object with two arrays of finding ids, "to_fix" and '
            '"deferred"'
        )
    plan: dict[str, list[str]] = {}
    for key, want in (("to_fix", to_fix), ("deferred", deferred)):
        ids = data[key]
        if not all(isinstance(i, str) for i in ids):
            raise ShapeError(f"L3 {key} must hold finding id strings")
        if len(set(ids)) != len(ids):
            raise ShapeError(f"L3 {key} names an id more than once")
        if len(ids) != want:
            raise ShapeError(
                f"L3's {key} lists {len(ids)} ids but --{key.replace('_', '-')} is {want}"
            )
        plan[key] = ids
    both = sorted(set(plan["to_fix"]) & set(plan["deferred"]))
    if both:
        raise ShapeError(f"L3 both fixes and defers {', '.join(both)}")
    unconfirmed = [i for i in plan["to_fix"] + plan["deferred"] if l25.get(i) != "CONFIRMED"]
    if unconfirmed:
        raise ShapeError(f"L3 plans {', '.join(unconfirmed)}, which L2.5 did not confirm")
    return plan
```

- [ ] **Step 4: Run the tests green**

Run: `python -m pytest tests/test_layer_outputs.py -q`
Expected: all pass.

- [ ] **Step 5: Red proof, one mutation per docstring**

For each test whose docstring names a mutation: apply it to `cca_checks/layer_outputs.py`, run that single test, confirm it fails in its `pytest.raises` (DID NOT RAISE) or its `assert`, restore. Write the date into each docstring's `Red:` line. Then run the file green again.

- [ ] **Step 6: Lint, type check, commit**

```bash
ruff check cca_checks/layer_outputs.py tests/test_layer_outputs.py && python -m pyright cca_checks/layer_outputs.py
git add cca_checks/layer_outputs.py tests/test_layer_outputs.py
git commit -F - <<'EOF'
feat(pipeline): shape checks for the L2, L2.5 and L3 work files

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 3: `record` enforces the shapes, `--deferred` and `--stale`

**Files:**
- Modify: `cca_checks/pipeline_state.py` (`_check_counts`, `record`, two new helpers)
- Modify: `cca_checks/__main__.py` (`_pipeline` record counts; `record` parser flags)
- Test: `tests/test_pipeline_state.py`, `tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: `layer_outputs.l2_findings`, `l25_verdicts`, `l3_plan`, `ShapeError` (Task 2); conftest helpers (Task 1).
- Produces: `pipeline_state._stored_json(state, layer: str, root) -> object | None` (the layer's stored file parsed, or None); counts `deferred` and `stale` accepted at L2.5, `deferred` at L3; CLI flags `--deferred`, `--stale`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_pipeline_state.py`)

```python
# --- deferred findings are counted, never given a verdict they did not receive ----------

def _to_l2(repo, tier, unique, run_id="r1", priorities=None):
    state = ps.start(tier, run_id=run_id, root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": unique},
              output=findings_output(repo, unique, priorities=priorities), root=repo)
    return state


def test_deferred_findings_complete_the_l2_5_sum(git_repo):
    """Red: mutation `total = judged + n_deferred` became `total = judged` in _check_counts;
    failed at the second L2.5 record (RecordRefused: accounts for 1 findings but 3 unique)."""
    _to_l2(git_repo, "FAST", 3)
    with pytest.raises(ps.RecordRefused, match="3 unique"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0},
                  output=verdicts_output(git_repo, confirmed=1), root=git_repo)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "deferred": 2},
              output=verdicts_output(git_repo, confirmed=1, deferred=2), root=git_repo)
    assert ps.load(git_repo).layers["L2.5"]["counts"]["deferred"] == 2


def test_a_deferred_finding_gets_no_ledger_row(git_repo):
    """Red: mutation `if rows != judged:` became `if rows != total:` in _check_counts;
    failed at the L2.5 record (RecordRefused: ledger holds 1 row(s) but 3 verdicts)."""
    _to_l2(git_repo, "STANDARD", 3)
    append_ledger(git_repo, "r1", 1)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "deferred": 2},
              output=verdicts_output(git_repo, confirmed=1, deferred=2), root=git_repo)


def test_stale_is_refused_outside_a_deferred_run(git_repo):
    """Red: mutation `if stale and state.mode != "DEFERRED":` became `if False:`; failed at
    pytest.raises (DID NOT RAISE RecordRefused)."""
    _to_l2(git_repo, "FAST", 2)
    with pytest.raises(ps.RecordRefused, match="--stale is for a DEFERRED run only"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "stale": 1},
                  output=verdicts_output(git_repo, confirmed=1, stale=1), root=git_repo)


def test_the_l2_file_must_hold_the_unique_findings(git_repo):
    """Red: mutation the `if layer == "L2":` branch of _check_shape deleted; failed at
    pytest.raises (DID NOT RAISE RecordRefused)."""
    ps.start("FAST", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="holds 1 findings but --unique is 2"):
        ps.record("L2", counts={"unique": 2}, output=findings_output(git_repo, 1),
                  root=git_repo)


def test_the_l2_5_file_must_agree_with_its_counts(git_repo):
    """Red: mutation the `elif layer == "L2.5":` branch of _check_shape deleted; failed at
    pytest.raises (DID NOT RAISE RecordRefused)."""
    _to_l2(git_repo, "FAST", 2)
    with pytest.raises(ps.RecordRefused, match="disagrees with its counts"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 1, "uncertain": 0, "duplicate": 0},
                  output=verdicts_output(git_repo, confirmed=2), root=git_repo)


def test_l3_must_plan_or_defer_every_confirmed_finding(git_repo):
    """Red: mutation `to_fix + deferred != confirmed` became `to_fix > confirmed` in
    _check_counts; failed at pytest.raises (DID NOT RAISE RecordRefused)."""
    _to_l2(git_repo, "FAST", 2)
    ps.record("L2.5", counts={"confirmed": 2, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(git_repo, confirmed=2), root=git_repo)
    with pytest.raises(ps.RecordRefused, match="either planned or deferred"):
        ps.record("L3", counts={"to_fix": 1}, output=plan_output(git_repo, to_fix=1),
                  root=git_repo)
    ps.record("L3", counts={"to_fix": 1, "deferred": 1},
              output=plan_output(git_repo, to_fix=1, deferred=1), root=git_repo)


def test_l3_refuses_an_id_l2_5_did_not_confirm(git_repo):
    """Red: mutation the `elif layer == "L3":` branch of _check_shape deleted; failed at
    pytest.raises (DID NOT RAISE RecordRefused)."""
    _to_l2(git_repo, "FAST", 2)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 1, "duplicate": 0},
              output=verdicts_output(git_repo, confirmed=1, uncertain=1), root=git_repo)
    plan = write_output(git_repo, {"to_fix": [finding_id(2)], "deferred": []})
    with pytest.raises(ps.RecordRefused, match="did not confirm"):
        ps.record("L3", counts={"to_fix": 1}, output=plan, root=git_repo)
```

Append to `tests/test_pipeline_cli.py`:

```python
def test_deferred_and_stale_flags_reach_the_counts(capsys, git_repo):
    """Red: mutation `"deferred": a.deferred` became `"deferred": None` in _pipeline; failed at
    `assert code == 0` (exit 2: accounts for 1 findings but 2 unique)."""
    from conftest import findings_output, verdicts_output
    r = str(git_repo)
    _cli(capsys, "start", "--tier", "FAST", "--root", r)
    _cli(capsys, "record", "L1", "--output", write_output(r), "--root", r)
    _cli(capsys, "record", "L2", "--unique", "2", "--output", findings_output(r, 2), "--root", r)
    code, _, _ = _cli(capsys, "record", "L2.5", "--confirmed", "1", "--fp", "0",
                      "--uncertain", "0", "--duplicate", "0", "--deferred", "1",
                      "--output", verdicts_output(r, confirmed=1, deferred=1), "--root", r)
    assert code == 0
```

- [ ] **Step 2: Run them; they fail**

Run: `python -m pytest tests/test_pipeline_state.py tests/test_pipeline_cli.py -q -k "deferred or stale or l2_file or l2_5_file or plan_or_defer or did_not_confirm"`
Expected: failures on the assertions (no shape checks, no `--deferred` flag: argparse exits 2 on the unknown flag for the CLI test). Record which ones fail on assertions: those are red against the pre-change code.

- [ ] **Step 3: Implement in `cca_checks/pipeline_state.py`**

Add `from cca_checks import layer_outputs` after the stdlib imports. Add `_optional` after `_need`:

```python
def _optional(counts: dict, key: str, layer: str) -> int:
    value = counts.get(key)
    if value is None:
        return 0
    if int(value) < 0:
        raise RecordRefused(f"{layer} --{key.replace('_', '-')} cannot be negative")
    return int(value)
```

Replace the `elif layer == "L2.5":` and `elif layer == "L3":` branches of `_check_counts` with:

```python
    elif layer == "L2.5":
        judged = sum(
            _need(counts, k, layer) for k in ("confirmed", "fp", "uncertain", "duplicate")
        )
        # `n_deferred`, not `deferred`: that name is the imported module (Task 5).
        n_deferred = _optional(counts, "deferred", layer)
        stale = _optional(counts, "stale", layer)
        if stale and state.mode != "DEFERRED":
            raise RecordRefused(
                "--stale is for a DEFERRED run only: STALE means the code moved on since the "
                "run that deferred the finding, and a first run has no earlier run"
            )
        judged += stale
        total = judged + n_deferred
        unique = _layer_count(state, "L2", "unique")
        if unique is not None and total != unique:
            raise RecordRefused(
                f"L2.5 accounts for {total} findings but L2 recorded {unique} unique; "
                "every consolidated finding needs exactly one verdict, DEFERRED included"
            )
        if state.tier != "FAST":
            rows, malformed = _ledger_rows(root, state.run_id)
            if rows != judged:
                note = f" ({malformed} malformed line(s) skipped)" if malformed else ""
                raise RecordRefused(
                    f"the disposition ledger holds {rows} row(s) for run {state.run_id} but "
                    f"L2.5 recorded {judged} verdicts{note}; append one row per verified "
                    "finding (a DEFERRED finding gets none) to "
                    ".claude/audits/AUDITOR_SCORECARD.jsonl (Step 2.5b) with "
                    f'"run_id": "{state.run_id}"'
                )
    elif layer == "L3":
        to_fix = _need(counts, "to_fix", layer)
        n_deferred = _optional(counts, "deferred", layer)
        confirmed = _layer_count(state, "L2.5", "confirmed")
        if confirmed is not None and to_fix + n_deferred != confirmed:
            raise RecordRefused(
                f"L3 plans {to_fix} (--to-fix) and defers {n_deferred} (--deferred) but L2.5 "
                f"confirmed {confirmed}; every confirmed finding is either planned or deferred"
            )
```

Add after `_read_output`:

```python
def _stored_json(state: PipelineState, layer: str, root):
    """The work file `layer` stored in this run, parsed; None when absent or unreadable."""
    entry = state.layers.get(layer)
    rel = entry.get("output") if isinstance(entry, dict) else None
    if not rel:
        return None
    try:
        return json.loads((pathlib.Path(root) / rel).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _check_shape(layer: str, state: PipelineState, counts: dict, data: bytes, root) -> None:
    """Refuse an L2, L2.5 or L3 file that disagrees with its counts or with the earlier files."""
    value = json.loads(data)
    try:
        if layer == "L2":
            layer_outputs.l2_findings(value, counts["unique"])
        elif layer == "L2.5":
            l2 = _stored_json(state, "L2", root)
            if not isinstance(l2, list):
                raise layer_outputs.ShapeError(
                    "L2's stored file is missing or unreadable; record L2 again"
                )
            ids = [r.get("id") for r in l2 if isinstance(r, dict)]
            layer_outputs.l25_verdicts(value, counts, ids)
        elif layer == "L3":
            l25 = _stored_json(state, "L2.5", root)
            if not isinstance(l25, list):
                raise layer_outputs.ShapeError(
                    "L2.5's stored file is missing or unreadable; record L2.5 again"
                )
            verdicts = {r.get("id"): r.get("verdict") for r in l25 if isinstance(r, dict)}
            layer_outputs.l3_plan(value, counts["to_fix"], counts.get("deferred", 0), verdicts)
    except layer_outputs.ShapeError as exc:
        raise RecordRefused(str(exc)) from exc
```

In `record`, replace

```python
        if layer in OUTPUT_LAYERS:
            data = _read_output(layer, output, root)
```

with

```python
        if layer in OUTPUT_LAYERS:
            data = _read_output(layer, output, root)
            _check_shape(layer, state, counts, data, root)
```

- [ ] **Step 4: Implement the CLI flags in `cca_checks/__main__.py`**

In `_pipeline`, the record branch's `counts` dict becomes:

```python
            counts = {"unique": a.unique, "confirmed": a.confirmed, "fp": a.fp,
                      "uncertain": a.uncertain, "duplicate": a.duplicate,
                      "deferred": a.deferred, "stale": a.stale, "to_fix": a.to_fix}
```

In `main`, extend the flag table of `pl_record`:

```python
        ("--duplicate", "L2.5: DUPLICATE verdicts"),
        ("--deferred", "L2.5: findings passed on unverified (DEFERRED); "
                       "L3: confirmed findings deferred, not fixed"),
        ("--stale", "L2.5, DEFERRED run only: findings whose code moved on (STALE)"),
        ("--to-fix", "L3: findings the plan will fix"),
```

- [ ] **Step 5: Run green, then the whole suite**

Run: `python -m pytest tests/test_pipeline_state.py tests/test_pipeline_cli.py -q` then `python -m pytest -q`
Expected: all pass.

- [ ] **Step 6: Red proof**

Apply each docstring mutation in turn to `pipeline_state.py` or `__main__.py`, run that one test, confirm it fails at the named assertion, restore. Date the `Red:` lines.

- [ ] **Step 7: Lint, type check, commit**

```bash
ruff check cca_checks tests && python -m pyright
git add cca_checks/pipeline_state.py cca_checks/__main__.py tests/test_pipeline_state.py tests/test_pipeline_cli.py
git commit -F - <<'EOF'
feat(pipeline): record checks layer files against their counts; L2.5 and L3 count deferrals

P3 findings (and P2 on FAST) are no longer filed under a verdict they never received:
L2.5 counts them as DEFERRED, with no ledger row. L3 must plan or defer every confirmed
finding.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 4: `start` records the base commit and validates the mode

**Files:**
- Modify: `cca_checks/plugin/hooks/cca_gate_core.py` (add `MODES`)
- Modify: `cca_checks/pipeline_state.py` (`PipelineState`, `start`, git helpers, `MODES`, imports, `__all__`)
- Modify: `cca_checks/__main__.py` (`--base`, `--mode` choices)
- Test: `tests/test_pipeline_state.py`, `tests/test_pipeline_cli.py`

**Interfaces:**
- Produces: `core.MODES = ("DIFF", "HUNT")` (Task 6 adds `"DEFERRED"`); `pipeline_state.MODES`; `PipelineState.base_commit: str | None`, `PipelineState.parent: str | None`; `start(tier, mode="DIFF", *, no_fix=False, run_id="", base="HEAD", parent="", root=".")` (`parent` is accepted but used from Task 6); `_git(root, *args) -> subprocess.CompletedProcess[bytes] | None`; `_commit_of(root, rev: str) -> str | None`; `_is_ancestor(root, sha: str) -> bool`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_pipeline_state.py`)

```python
# --- the base commit -----------------------------------------------------------------

def _head(repo) -> str:
    return subprocess.run(["git", "-C", str(repo), "rev-parse", "HEAD"], check=True,
                          capture_output=True, text=True).stdout.strip()


def test_start_records_the_commit_the_diff_is_taken_against(git_repo):
    """Red: mutation `base_commit=base_commit` became `base_commit=None` in start(); failed at
    `assert state.base_commit == _head(git_repo)`."""
    state = ps.start("FAST", root=git_repo)
    assert state.base_commit == _head(git_repo)
    raw = json.loads(ps.state_path(git_repo).read_text(encoding="utf-8"))
    assert raw["base_commit"] == state.base_commit and raw["parent"] is None


def test_start_resolves_a_relative_base(git_repo):
    first = _head(git_repo)
    (git_repo / "app.py").write_text("x = 5\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "commit", "-qam", "two"], check=True)
    assert ps.start("FAST", base="HEAD~1", root=git_repo).base_commit == first


def test_start_refuses_a_base_that_names_no_commit(git_repo):
    """Red: mutation the `if base_commit is None and base != "HEAD":` check deleted; failed
    at pytest.raises (DID NOT RAISE RecordRefused)."""
    with pytest.raises(ps.RecordRefused, match="does not name a commit"):
        ps.start("FAST", base="no-such-rev", root=git_repo)
    assert ps.load(git_repo) is None


def test_start_in_a_repository_without_commits_stores_no_base(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    assert ps.start("FAST", root=tmp_path).base_commit is None


def test_start_refuses_an_unknown_mode(git_repo):
    """Red: mutation the `if mode not in MODES:` check deleted from start(); failed at
    pytest.raises (DID NOT RAISE ValueError)."""
    with pytest.raises(ValueError, match="unknown mode"):
        ps.start("FAST", mode="SIDEWAYS", root=git_repo)
```

Append to `tests/test_pipeline_cli.py`:

```python
def test_base_reaches_the_state(capsys, git_repo):
    """Red: mutation `base=a.base` dropped from the start call in _pipeline; failed at
    `assert code == 2` (start used HEAD and succeeded)."""
    code, _, err = _cli(capsys, "start", "--tier", "FAST", "--base", "no-such-rev",
                        "--root", str(git_repo))
    assert code == 2 and "does not name a commit" in err["reason"]
```

- [ ] **Step 2: Run them; they fail**

Run: `python -m pytest tests/test_pipeline_state.py tests/test_pipeline_cli.py -q -k "base or unknown_mode"`
Expected: `TypeError: start() got an unexpected keyword argument 'base'` and `AttributeError: base_commit` (not red proof; Step 6 gives it).

- [ ] **Step 3: Gate core**

In `cca_checks/plugin/hooks/cca_gate_core.py`, after `TIERS = (...)`:

```python
MODES = ("DIFF", "HUNT")
```

- [ ] **Step 4: `pipeline_state.py`**

Add `import subprocess` to the imports. After `OUTPUT_LAYERS = core.OUTPUT_LAYERS` add `MODES = core.MODES` and add `"MODES"` to `__all__`.

Add two fields at the end of `PipelineState` and carry them through JSON:

```python
    base_commit: str | None = None
    parent: str | None = None
```

In `to_json`, add `"base_commit": self.base_commit, "parent": self.parent,` to the dict. In `from_json`, add the arguments:

```python
            base_commit=raw.get("base_commit") if isinstance(raw.get("base_commit"), str) else None,
            parent=raw.get("parent") if isinstance(raw.get("parent"), str) else None,
```

Add the git helpers before `start`:

```python
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
    done = _git(root, "merge-base", "--is-ancestor", sha, "HEAD")
    return done is not None and done.returncode == 0
```

Change `start`'s signature and body (docstring kept, with one sentence added: "`base` is the revision the audited diff is taken against; its sha is stored as `base_commit`."):

```python
def start(tier: str, mode: str = "DIFF", *, no_fix: bool = False, run_id: str = "",
          base: str = "HEAD", parent: str = "",
          root: str | pathlib.Path = ".") -> PipelineState:
    tier = tier.upper()
    if tier not in TIERS:
        raise ValueError(f"unknown tier {tier!r} (expected one of {', '.join(TIERS)})")
    mode = mode.upper()
    if mode not in MODES:
        raise ValueError(f"unknown mode {mode!r} (expected one of {', '.join(MODES)})")
    # ... the existing run id check and open-run check, unchanged ...
    base_commit = _commit_of(root, base)
    if base_commit is None and base != "HEAD":
        raise RecordRefused(
            f"--base {base} does not name a commit; pass the revision the audited diff is "
            "taken against (HEAD, or HEAD~N for `commit N`)"
        )
    stamp = core.now()
    state = PipelineState(
        run_id=run_id or stamp.replace(":", "").replace("-", ""), tier=tier,
        mode=mode, no_fix=no_fix, started_utc=stamp,
        schema=core.SCHEMA, base_tree=core.fingerprint(root), base_commit=base_commit,
    )
    _write(state, root)
    return state
```

- [ ] **Step 5: CLI**

In `_pipeline`'s start branch: `pipeline_state.start(a.tier, a.mode, no_fix=a.no_fix, run_id=a.run_id, base=a.base, root=a.root)`. In `main`, replace the `--mode` line and add `--base`:

```python
    pl_start.add_argument("--mode", default="DIFF", choices=list(pipeline_state.MODES))
    pl_start.add_argument("--base", default="HEAD",
                          help="the revision the audited diff is taken against (HEAD~N for "
                               "`commit N`); stored as the run's base_commit")
```

- [ ] **Step 6: Run green, red proof, whole suite**

Run the Step 2 selection green; apply each docstring mutation, confirm failure at the named assertion, restore, date the `Red:` lines; then `python -m pytest -q`. `test_a_legacy_state_round_trips_without_gaining_a_schema` asserts only on `schema`, so the two new keys a legacy state gains on its next write do not affect it, and the 0.10.x rules never read them.

- [ ] **Step 7: Lint, type check, commit**

```bash
ruff check cca_checks tests && python -m pyright
git add cca_checks tests
git commit -F - <<'EOF'
feat(pipeline): start records the base commit and validates the mode

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 5: `deferred` rules, and `close` writes the run summary

**Files:**
- Create: `cca_checks/deferred.py`
- Modify: `cca_checks/pipeline_state.py` (`close`, `start`, `_write_summary`)
- Modify: `tests/conftest.py` (add `closed_parent`)
- Test: `tests/test_deferred.py`, `tests/test_pipeline_state.py`

**Interfaces:**
- Consumes: `_stored_json` (Task 3); `_commit_of` (Task 4); conftest helpers (Task 1).
- Produces (`cca_checks/deferred.py`): `SUMMARY_SCHEMA = 1`; `DEFERRED_MIN_P3 = 3`; `runs_dir(root) -> pathlib.Path`; `pool_ids(l25, l3) -> list[str]`; `parked_count(root, run_id: str) -> int`; `load_summaries(root) -> tuple[list[dict], list[str]]` (summaries oldest first, unreadable paths); `consumed_by(parent: str, summaries) -> str | None`; `why_not_offered(summary: dict, summaries) -> str | None`; `findings_for(root, summary: dict) -> list[dict]` (raises `LookupError`); `@dataclass Offer` with `run_id, offered, recommendation, reasons, pool, counts, human_pending, commit_reachable, other_pools, unreadable` and `to_json() -> dict`; `assess(summary, summaries, findings, *, commit_reachable: bool, unreadable=()) -> Offer`.
- Produces (conftest): `closed_parent(repo, *, run_id="parent", p2_deferred=0, p3=3, human=0, parked=0) -> str`.

- [ ] **Step 1: Write `tests/test_deferred.py`**

```python
"""Which findings a closed run deferred, and whether the second pass is offered and advised."""

from __future__ import annotations

import json

import pytest

from cca_checks import deferred


def _s(run_id="r1", **over):
    s = {"schema": 1, "run_id": run_id, "tier": "STANDARD", "mode": "DIFF", "no_fix": False,
         "base_commit": "b", "commit": "c", "parent": None,
         "completed_utc": "2026-10-11T10:00:00Z", "pool": ["F-1"], "human_pending": 0}
    s.update(over)
    return s


def _f(i, priority="P3"):
    return {"id": f"F-{i}", "priority": priority, "file": "app.py", "line": i, "claim": f"c{i}"}


@pytest.mark.parametrize("over, reason", [
    ({"pool": []}, "nothing was deferred"),
    ({"no_fix": True}, "no-fix"),
    ({"mode": "DEFERRED"}, "third pass"),
    ({"mode": "HUNT"}, "hunt"),
])
def test_these_runs_are_never_offered_a_pass(over, reason):
    """Red, one per case: mutation the matching `if` of why_not_offered deleted; each case
    failed at `assert offer.offered is False`."""
    s = _s(**over)
    offer = deferred.assess(s, [s], [_f(1)] if s["pool"] else [], commit_reachable=True)
    assert offer.offered is False and offer.recommendation is None
    assert reason in offer.reasons[0]


def test_a_pool_a_completed_deferred_run_took_is_not_offered_again():
    """Red: mutation `consumed_by` returns None; failed at `assert not offer.offered`."""
    parent, child = _s("p"), _s("c", mode="DEFERRED", parent="p", pool=[])
    offer = deferred.assess(parent, [parent, child], [_f(1)], commit_reachable=True)
    assert not offer.offered and "deferred run c already took" in offer.reasons[0]


def test_findings_awaiting_a_human_recommend_skip_even_with_a_p2():
    """Red: mutation the `if human > 0:` branch of assess deleted; failed at
    `assert offer.recommendation == "SKIP"` (got RUN)."""
    s = _s(pool=["F-1"], human_pending=1)
    offer = deferred.assess(s, [s], [_f(1, "P2")], commit_reachable=True)
    assert offer.offered and offer.recommendation == "SKIP"
    assert "await a human decision" in offer.reasons[0]


def test_any_p2_recommends_run():
    """Red: mutation `elif counts["P2"]:` became `elif False:`; failed at
    `assert offer.recommendation == "RUN"` (got SKIP)."""
    s = _s(pool=["F-1"])
    offer = deferred.assess(s, [s], [_f(1, "P2")], commit_reachable=True)
    assert offer.recommendation == "RUN" and offer.counts == {"P1": 0, "P2": 1, "P3": 0}


def test_three_p3_recommend_run_and_two_recommend_skip():
    """Red: mutation `>= DEFERRED_MIN_P3` became `> DEFERRED_MIN_P3`; failed at
    `assert three.recommendation == "RUN"` (got SKIP)."""
    s3, s2 = _s(pool=["F-1", "F-2", "F-3"]), _s(pool=["F-1", "F-2"])
    three = deferred.assess(s3, [s3], [_f(1), _f(2), _f(3)], commit_reachable=True)
    two = deferred.assess(s2, [s2], [_f(1), _f(2)], commit_reachable=True)
    assert three.recommendation == "RUN"
    assert two.recommendation == "SKIP" and "four agent passes" in two.reasons[0]


def test_an_unreachable_commit_is_reported():
    s = _s()
    offer = deferred.assess(s, [s], [_f(1)], commit_reachable=False)
    assert offer.commit_reachable is False
    assert any("not an ancestor of HEAD" in r for r in offer.reasons)


def test_an_older_unconsumed_pool_is_listed_beside_the_newest():
    """Red: mutation `others = []` in assess; failed at `assert offer.other_pools == ["old"]`."""
    old, new = _s("old"), _s("new", pool=[], completed_utc="2026-10-11T11:00:00Z")
    offer = deferred.assess(new, [old, new], [], commit_reachable=True)
    assert offer.other_pools == ["old"]


def test_the_pool_is_l2_5_deferred_then_l3_deferred_each_once():
    """Red: mutation the l25 half of pool_ids deleted; failed at the assert."""
    l25 = [{"id": "F-3", "verdict": "DEFERRED"}, {"id": "F-1", "verdict": "CONFIRMED"}]
    l3 = {"to_fix": ["F-1"], "deferred": ["F-2", "F-3"]}
    assert deferred.pool_ids(l25, l3) == ["F-3", "F-2"]
    assert deferred.pool_ids(l25, None) == ["F-3"]


def test_findings_for_returns_the_pool_objects_in_pool_order(tmp_path):
    run = tmp_path / ".claude/audits/runs/r1"
    run.mkdir(parents=True)
    (run / "L2.json").write_text(json.dumps([_f(1), _f(2), _f(3)]), encoding="utf-8")
    assert [f["id"] for f in deferred.findings_for(tmp_path, _s(pool=["F-3", "F-1"]))] == \
        ["F-3", "F-1"]


def test_findings_for_names_a_missing_l2_file(tmp_path):
    """Review Focus 1. Red: mutation the `except` in findings_for re-raises nothing and
    returns []; failed at pytest.raises (DID NOT RAISE LookupError)."""
    (tmp_path / ".claude/audits/runs/r1").mkdir(parents=True)
    with pytest.raises(LookupError, match="L2.json"):
        deferred.findings_for(tmp_path, _s())


def test_an_unreadable_summary_is_reported_not_skipped(tmp_path):
    """Review Focus 2. Red: mutation `unreadable.append(...)` removed from load_summaries;
    failed at `assert len(unreadable) == 1`."""
    good = tmp_path / ".claude/audits/runs/a"
    bad = tmp_path / ".claude/audits/runs/b"
    good.mkdir(parents=True)
    bad.mkdir(parents=True)
    (good / "summary.json").write_text(json.dumps(_s("a")), encoding="utf-8")
    (bad / "summary.json").write_text("{torn", encoding="utf-8")
    summaries, unreadable = deferred.load_summaries(tmp_path)
    assert [s["run_id"] for s in summaries] == ["a"]
    assert len(unreadable) == 1 and unreadable[0].endswith("b/summary.json")
```

- [ ] **Step 2: Write `cca_checks/deferred.py`**

```python
"""Which findings a closed run deferred, and whether a second pass over them is worth it.

Pure rules over files already on disk: the summary `pipeline close` writes for every
completed run, and the L2 file that run stored. No git and no run state here: the caller
(`pipeline_state`) supplies what needs them, so these rules test without a repository.
"""

from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field

SUMMARY_SCHEMA = 1
#: Fewest P3 findings, with no P2 among them, for which a second pass is recommended. A
#: starting value, not a measured one: a deferred run costs at least four agent passes
#: whatever the size of its pool.
DEFERRED_MIN_P3 = 3
_FIXED_COST = "fp-check, the scorecard, differential-review and architect-reviewer"


def runs_dir(root) -> pathlib.Path:
    return pathlib.Path(root) / ".claude" / "audits" / "runs"


def pool_ids(l25, l3) -> list[str]:
    """The pool: ids L2.5 marked DEFERRED, then ids L3 deferred, each once, in that order."""
    ids: list[str] = []
    if isinstance(l25, list):
        ids += [r["id"] for r in l25 if isinstance(r, dict)
                and r.get("verdict") == "DEFERRED" and isinstance(r.get("id"), str)]
    if isinstance(l3, dict) and isinstance(l3.get("deferred"), list):
        ids += [i for i in l3["deferred"] if isinstance(i, str)]
    return list(dict.fromkeys(ids))


def parked_count(root, run_id: str) -> int:
    """Outcome rows for `run_id` whose result is `parked`: findings left to a human at Step 6."""
    path = pathlib.Path(root) / ".claude" / "audits" / "AUDITOR_SCORECARD.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return 0
    count = 0
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict) and "verdict" not in row and \
                str(row.get("run_id")) == run_id and row.get("result") == "parked":
            count += 1
    return count


def load_summaries(root) -> tuple[list[dict], list[str]]:
    """Every readable summary, oldest first, and the paths of the unreadable ones."""
    found: list[dict] = []
    unreadable: list[str] = []
    base = runs_dir(root)
    for path in sorted(base.glob("*/summary.json")) if base.is_dir() else []:
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            unreadable.append(path.as_posix())
            continue
        if not isinstance(raw, dict) or not isinstance(raw.get("run_id"), str) \
                or not isinstance(raw.get("pool"), list):
            unreadable.append(path.as_posix())
            continue
        found.append(raw)
    found.sort(key=lambda s: (str(s.get("completed_utc", "")), s["run_id"]))
    return found, unreadable


def consumed_by(parent: str, summaries) -> str | None:
    """The completed DEFERRED run that took `parent`'s pool, if any."""
    for s in summaries:
        if s.get("mode") == "DEFERRED" and s.get("parent") == parent:
            return s["run_id"]
    return None


def why_not_offered(summary: dict, summaries) -> str | None:
    """Why this run's pool may not be taken by a deferred run; None when it may."""
    if not summary.get("pool"):
        return "nothing was deferred"
    if summary.get("no_fix"):
        return "the run was started with no-fix, which forbids editing"
    if summary.get("mode") == "DEFERRED":
        return "a deferred run never offers a third pass"
    if summary.get("mode") == "HUNT":
        return "deferred passes over hunt runs are out of scope; the pool is listed, not offered"
    child = consumed_by(summary["run_id"], summaries)
    if child:
        return f"deferred run {child} already took this pool"
    return None


def findings_for(root, summary: dict) -> list[dict]:
    """The run's stored L2 objects for exactly its pool ids, in pool order."""
    path = runs_dir(root) / summary["run_id"] / "L2.json"
    try:
        rows = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise LookupError(
            f"run {summary['run_id']} has no readable L2 findings at {path.as_posix()} "
            f"({exc.__class__.__name__}); its run directory is incomplete, so its pool "
            "cannot be loaded"
        ) from exc
    by_id = {r.get("id"): r for r in rows if isinstance(r, dict)} if isinstance(rows, list) \
        else {}
    missing = [i for i in summary["pool"] if i not in by_id]
    if missing:
        raise LookupError(f"run {summary['run_id']}'s L2 file lacks pool ids {', '.join(missing)}")
    return [by_id[i] for i in summary["pool"]]


@dataclass
class Offer:
    run_id: str
    offered: bool
    recommendation: str | None
    reasons: list[str]
    pool: list[dict]
    counts: dict
    human_pending: int
    commit_reachable: bool
    other_pools: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return asdict(self)


def assess(summary: dict, summaries, findings: list[dict], *, commit_reachable: bool,
           unreadable=()) -> Offer:
    """Whether a second pass over this run's pool is offered, and RUN or SKIP with reasons."""
    counts = {p: sum(1 for f in findings if f.get("priority") == p) for p in ("P1", "P2", "P3")}
    pool = [{k: f.get(k) for k in ("id", "priority", "file", "line", "claim")} for f in findings]
    human = int(summary.get("human_pending", 0) or 0)
    others = [s["run_id"] for s in summaries
              if s["run_id"] != summary["run_id"] and why_not_offered(s, summaries) is None]
    notes = [] if commit_reachable else [
        f"the run's commit {summary.get('commit')} is not an ancestor of HEAD, so fp-check "
        "cannot tell STALE from FALSE_POSITIVE against it"
    ]

    def offer(offered: bool, rec: str | None, why: str) -> Offer:
        return Offer(summary["run_id"], offered, rec, [why, *notes], pool, counts, human,
                     commit_reachable, others, list(unreadable))

    why_not = why_not_offered(summary, summaries)
    if why_not:
        return offer(False, None, why_not)
    if human > 0:
        return offer(True, "SKIP",
                     f"{human} finding(s) from this run await a human decision; settle them first")
    if counts["P2"]:
        return offer(True, "RUN", f"the pool holds {counts['P2']} P2 finding(s)")
    if counts["P3"] >= DEFERRED_MIN_P3:
        return offer(True, "RUN", f"{counts['P3']} P3 findings, at or above the threshold of "
                                  f"{DEFERRED_MIN_P3}")
    return offer(True, "SKIP", f"{counts['P3']} P3 finding(s), below the threshold of "
                               f"{DEFERRED_MIN_P3}: a deferred run costs at least four agent "
                               f"passes ({_FIXED_COST}) whatever its size")
```

- [ ] **Step 3: Run `tests/test_deferred.py` green**

Run: `python -m pytest tests/test_deferred.py -q`
Expected: all pass.

- [ ] **Step 4: Add `closed_parent` to `tests/conftest.py`**

Add `from cca_checks import pipeline_state as ps` to the conftest imports, and:

```python
def closed_parent(repo, *, run_id="parent", p2_deferred=0, p3=3, human=0, parked=0) -> str:
    """A closed STANDARD run: fixes F-1, defers `p2_deferred` CONFIRMED P2s at L3 and `p3` P3s
    at L2.5, leaves `human` UNCERTAIN, and logs `parked` parked outcomes. Returns its run id."""
    confirmed = 1 + p2_deferred
    unique = confirmed + human + p3
    p3_ids = {finding_id(i): "P3" for i in range(confirmed + human + 1, unique + 1)}
    ps.start("STANDARD", run_id=run_id, root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    ps.record("L2", counts={"unique": unique},
              output=findings_output(repo, unique, priorities=p3_ids), root=repo)
    append_ledger(repo, run_id, confirmed + human)
    ps.record("L2.5", counts={"confirmed": confirmed, "fp": 0, "uncertain": human,
                              "duplicate": 0, "deferred": p3},
              output=verdicts_output(repo, confirmed=confirmed, uncertain=human, deferred=p3),
              root=repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": 1, "deferred": p2_deferred},
              output=plan_output(repo, to_fix=1, deferred=p2_deferred), root=repo)
    app = pathlib.Path(repo) / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + f"# fixed by {run_id}\n", encoding="utf-8")
    ps.record("L4", fixes=1, output=write_output(repo), root=repo)
    ps.record("L5", detail="green", root=repo)
    ps.record("L5.5", detail="all SAFE", root=repo)
    ps.record("L5.6", detail="1 RED", source="tool", root=repo)
    ps.record("L6", detail="APPROVED", root=repo)
    _git(repo, "commit", "-qam", f"fix: audit fixes from CCA ({run_id})")
    append_ledger(repo, run_id, 0, outcome_rows=parked, outcome_result="parked")
    ps.close(root=repo)
    return run_id
```

- [ ] **Step 5: Write the failing integration tests** (append to `tests/test_pipeline_state.py`; add `closed_parent` to its `from conftest import (...)` line)

```python
# --- the run summary -----------------------------------------------------------------

def _summary_of(repo, run_id):
    return json.loads((repo / ".claude/audits/runs" / run_id / "summary.json")
                      .read_text(encoding="utf-8"))


def test_close_writes_a_summary_whose_pool_is_both_deferral_points(git_repo):
    """Red: mutation `pool_ids` returns only the L3 half (l25 ignored); failed at
    `assert s["pool"] == ["F-3", "F-4", "F-2"]` (got ["F-2"])."""
    base = _head(git_repo)
    closed_parent(git_repo, p2_deferred=1, p3=2)
    s = _summary_of(git_repo, "parent")
    assert s["pool"] == ["F-3", "F-4", "F-2"]
    assert s["base_commit"] == base and s["commit"] == _head(git_repo)
    assert s["mode"] == "DIFF" and s["parent"] is None and s["schema"] == 1


def test_human_pending_counts_uncertain_and_parked_findings(git_repo):
    """Red: mutation `+ deferred.parked_count(...)` removed from _write_summary; failed at
    `assert ... == 2` (got 1)."""
    closed_parent(git_repo, human=1, parked=1)
    assert _summary_of(git_repo, "parent")["human_pending"] == 2


def test_abort_writes_no_summary(git_repo):
    """Red: mutation `_write_summary(state, root)` added to abort(); failed at
    `assert not (...).exists()`."""
    ps.start("FAST", run_id="gone", root=git_repo)
    ps.abort("test", root=git_repo)
    assert not (git_repo / ".claude/audits/runs/gone/summary.json").exists()


def test_a_no_fix_run_summary_pools_what_l2_5_deferred(git_repo):
    ps.start("STANDARD", no_fix=True, run_id="nf", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2},
              output=findings_output(git_repo, 2, priorities={"F-2": "P3"}), root=git_repo)
    append_ledger(git_repo, "nf", 1)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "deferred": 1},
              output=verdicts_output(git_repo, confirmed=1, deferred=1), root=git_repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=git_repo)
    ps.close(root=git_repo)
    s = _summary_of(git_repo, "nf")
    assert s["no_fix"] is True and s["pool"] == ["F-2"]


def test_start_refuses_a_run_id_that_names_a_completed_run(git_repo):
    """Review Focus 4. Red: mutation the summary-exists check deleted from start(); failed at
    pytest.raises (DID NOT RAISE RecordRefused)."""
    closed_parent(git_repo)
    with pytest.raises(ps.RecordRefused, match="already names a completed run"):
        ps.start("FAST", run_id="parent", root=git_repo)
```

- [ ] **Step 6: Run them; they fail**

Run: `python -m pytest tests/test_pipeline_state.py -q -k "summary or human_pending or names_a_completed"`
Expected: `FileNotFoundError` on `summary.json` and DID NOT RAISE (assertion-level for the start refusal).

- [ ] **Step 7: Implement in `pipeline_state.py`**

Import: `from cca_checks import deferred, layer_outputs`. Add before `close`:

```python
def _write_summary(state: PipelineState, root) -> None:
    """Write runs/<run_id>/summary.json: what a later deferred pass reads about this run."""
    summary = {
        "schema": deferred.SUMMARY_SCHEMA, "run_id": state.run_id, "tier": state.tier,
        "mode": state.mode, "no_fix": state.no_fix, "base_commit": state.base_commit,
        "commit": _commit_of(root, "HEAD"), "parent": state.parent,
        "completed_utc": core.now(),
        "pool": deferred.pool_ids(_stored_json(state, "L2.5", root),
                                  _stored_json(state, "L3", root)),
        "human_pending": (_layer_count(state, "L2.5", "uncertain") or 0)
        + deferred.parked_count(root, state.run_id),
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
```

In `close`, after the `if missing: raise ...` block and before `recorded = ...`:

```python
    if not state.legacy:
        _write_summary(state, root)
```

In `start`, after the open-run check, compute the id first and refuse a completed one (and use `new_id` when building the state):

```python
    stamp = core.now()
    new_id = run_id or stamp.replace(":", "").replace("-", "")
    if (deferred.runs_dir(root) / new_id / "summary.json").exists():
        raise RecordRefused(
            f"run id {new_id!r} already names a completed run under .claude/audits/runs/; "
            "pick another --run-id, so that run's stored work is not overwritten"
        )
```

(Remove the later `stamp = core.now()` line and pass `run_id=new_id` to `PipelineState`.)

- [ ] **Step 8: Run green, red proof, whole suite, commit**

Run the Step 6 selection and `tests/test_deferred.py` green; apply each docstring mutation (in `deferred.py` or `pipeline_state.py`), confirm the named failure, restore, date the `Red:` lines. Then `python -m pytest -q`, `ruff check cca_checks tests`, `python -m pyright`.

```bash
git add cca_checks/deferred.py cca_checks/pipeline_state.py tests/conftest.py tests/test_deferred.py tests/test_pipeline_state.py
git commit -F - <<'EOF'
feat(pipeline): close writes a run summary; deferred pool and offer rules

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 6: the `DEFERRED` run mode

**Files:**
- Modify: `cca_checks/plugin/hooks/cca_gate_core.py` (`MODES`)
- Modify: `cca_checks/pipeline_state.py` (`start`, `_deferred_parent`, `_check_counts`)
- Modify: `cca_checks/__main__.py` (`--from`)
- Test: `tests/test_pipeline_state.py`, `tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: `deferred.load_summaries`, `consumed_by`, `why_not_offered` (Task 5); `_commit_of`, `_is_ancestor` (Task 4); `closed_parent` (Task 5).
- Produces: `core.MODES = ("DIFF", "HUNT", "DEFERRED")`; `start(..., mode="DEFERRED", parent=<run_id or "commit:<sha>">)` storing `state.parent` and the parent's `base_commit`; CLI `pipeline start --from <run_id>`.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_pipeline_state.py`)

```python
# --- the DEFERRED run ----------------------------------------------------------------

def _write_summary_file(repo, run_id, **over):
    s = {"schema": 1, "run_id": run_id, "tier": "STANDARD", "mode": "DIFF", "no_fix": False,
         "base_commit": None, "commit": None, "parent": None,
         "completed_utc": "2026-10-11T00:00:00Z", "pool": ["F-1"], "human_pending": 0}
    s.update(over)
    path = repo / ".claude/audits/runs" / run_id / "summary.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(s), encoding="utf-8")


def _child_to_l5(repo, parent, *, run_id="child", n=3):
    ps.start("STANDARD", mode="DEFERRED", parent=parent, run_id=run_id, root=repo)
    ps.record("L1", output=write_output(repo), root=repo)
    p3 = {finding_id(i): "P3" for i in range(1, n + 1)}
    ps.record("L2", counts={"unique": n}, output=findings_output(repo, n, priorities=p3),
              root=repo)
    append_ledger(repo, run_id, n)
    ps.record("L2.5", counts={"confirmed": n, "fp": 0, "uncertain": 0, "duplicate": 0},
              output=verdicts_output(repo, confirmed=n), root=repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=repo)
    ps.record("L3", counts={"to_fix": n}, output=plan_output(repo, to_fix=n), root=repo)
    app = repo / "app.py"
    app.write_text(app.read_text(encoding="utf-8") + f"# {run_id}\n", encoding="utf-8")
    ps.record("L4", fixes=n, output=write_output(repo), root=repo)
    return ps.record("L5", detail="green", root=repo)


def _close_child(repo, run_id="child"):
    ps.record("L5.5", detail="all SAFE", root=repo)
    ps.record("L5.6", detail="no proofs", source="tool", root=repo)
    ps.record("L6", detail="APPROVED", root=repo)
    subprocess.run(["git", "-C", str(repo), "commit", "-qam", f"fix: {run_id}"], check=True)
    ps.close(root=repo)


@pytest.mark.parametrize("kwargs, summary, match", [
    ({"tier": "STANDARD"}, None, "needs --from"),
    ({"tier": "FAST", "parent": "p"}, {}, "cannot be FAST"),
    ({"tier": "STANDARD", "parent": "p", "no_fix": True}, {}, "exists to fix"),
    ({"tier": "STANDARD", "parent": "absent"}, None, "no completed run 'absent'"),
    ({"tier": "STANDARD", "parent": "p"}, {"mode": "DEFERRED"}, "third pass"),
    ({"tier": "STANDARD", "parent": "p"}, {"mode": "HUNT"}, "hunt"),
    ({"tier": "STANDARD", "parent": "p"}, {"no_fix": True}, "no-fix"),
    ({"tier": "STANDARD", "parent": "p"}, {"pool": []}, "nothing was deferred"),
])
def test_start_deferred_refuses_a_parent_it_cannot_follow(git_repo, kwargs, summary, match):
    """Red, one per case: mutation the matching check in _deferred_parent (or in
    why_not_offered for the last four) deleted; each case failed at pytest.raises
    (DID NOT RAISE RecordRefused)."""
    if summary is not None:
        _write_summary_file(git_repo, "p", **summary)
    kw = dict(kwargs)  # a copy: the parametrize dict is shared across reruns
    with pytest.raises(ps.RecordRefused, match=match):
        ps.start(kw.pop("tier"), mode="DEFERRED", root=git_repo, **kw)
    assert ps.load(git_repo) is None


def test_from_is_refused_outside_a_deferred_run(git_repo):
    """Red: mutation the `if parent and mode != "DEFERRED":` check deleted; failed at
    pytest.raises (DID NOT RAISE RecordRefused)."""
    with pytest.raises(ps.RecordRefused, match="--from"):
        ps.start("STANDARD", parent="p", root=git_repo)


def test_a_deferred_run_takes_its_parents_base_and_names_its_parent(git_repo):
    """Red: mutation `base_commit=base_commit` became `base_commit=_commit_of(root, base)` for
    DEFERRED; failed at `assert child.base_commit == parent_base` (got HEAD)."""
    parent_base = _head(git_repo)
    closed_parent(git_repo)
    child = ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c", root=git_repo)
    assert child.parent == "parent" and child.base_commit == parent_base


def test_the_commit_fallback_needs_a_commit_that_is_an_ancestor_of_head(git_repo):
    """Red: mutation `or not _is_ancestor(root, sha)` removed from _deferred_parent; failed at
    the orphan pytest.raises (DID NOT RAISE RecordRefused). The unknown sha cannot prove it:
    `_commit_of` already returns None for it."""
    def git(*args):
        return subprocess.run(["git", "-C", str(git_repo), *args], check=True,
                              capture_output=True, text=True).stdout.strip()

    with pytest.raises(ps.RecordRefused, match="ancestor of HEAD"):
        ps.start("STANDARD", mode="DEFERRED", parent="commit:deadbeef", root=git_repo)
    orphan = git("commit-tree", "-m", "orphan", git("rev-parse", "HEAD^{tree}"))
    with pytest.raises(ps.RecordRefused, match="ancestor of HEAD"):
        ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{orphan}", root=git_repo)
    head = _head(git_repo)
    state = ps.start("STANDARD", mode="DEFERRED", parent=f"commit:{head[:8]}", root=git_repo)
    assert state.parent == f"commit:{head}" and state.base_commit is None


def test_a_deferred_run_cannot_defer(git_repo):
    """Red: mutation the `if n_deferred and state.mode == "DEFERRED":` check deleted from the
    L2.5 branch; failed at the first pytest.raises (DID NOT RAISE RecordRefused)."""
    closed_parent(git_repo)
    ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c", root=git_repo)
    ps.record("L1", output=write_output(git_repo), root=git_repo)
    ps.record("L2", counts={"unique": 2}, output=findings_output(git_repo, 2), root=git_repo)
    append_ledger(git_repo, "c", 1)
    with pytest.raises(ps.RecordRefused, match="cannot defer"):
        ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                                  "deferred": 1},
                  output=verdicts_output(git_repo, confirmed=1, deferred=1), root=git_repo)
    append_ledger(git_repo, "c", 1)
    ps.record("L2.5", counts={"confirmed": 1, "fp": 0, "uncertain": 0, "duplicate": 0,
                              "stale": 1},
              output=verdicts_output(git_repo, confirmed=1, stale=1), root=git_repo)
    ps.record("L2.6", detail="0 cells", source="tool", root=git_repo)
    with pytest.raises(ps.RecordRefused, match="cannot defer"):
        ps.record("L3", counts={"to_fix": 0, "deferred": 1},
                  output=plan_output(git_repo, to_fix=0, deferred=1), root=git_repo)


def test_a_deferred_run_owes_every_fix_layer_until_l6_approves(git_repo):
    """Red: mutation in cca_gate_core.required_layers, first line
    `if state.get("mode") == "DEFERRED": return ("L1", "L2", "L2.5", "L3", "L5")`; failed at
    `assert {"L5.5", "L5.6", "L6"} <= owed` (owed was empty)."""
    closed_parent(git_repo)
    state = _child_to_l5(git_repo, "parent")
    owed = {r.split(" ")[0] for r in ps.verify_commit_allowed(state, root=git_repo).reasons}
    assert {"L5.5", "L5.6", "L6"} <= owed
    ps.record("L5.5", detail="all SAFE", root=git_repo)
    ps.record("L5.6", detail="no proofs", source="tool", root=git_repo)
    state = ps.record("L6", detail="REVISE", root=git_repo)
    assert not ps.verify_commit_allowed(state, root=git_repo).allowed
    state = ps.record("L6", detail="APPROVED", root=git_repo)
    assert ps.verify_commit_allowed(state, root=git_repo).allowed


def test_a_closed_deferred_run_takes_its_parents_pool(git_repo):
    """Red: mutation `consumed_by` returns None; failed at pytest.raises (DID NOT RAISE)."""
    closed_parent(git_repo)
    _child_to_l5(git_repo, "parent")
    _close_child(git_repo)
    assert _summary_of(git_repo, "child")["parent"] == "parent"
    with pytest.raises(ps.RecordRefused, match="already took this pool"):
        ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="again", root=git_repo)


def test_an_aborted_deferred_run_leaves_the_pool_on_offer(git_repo):
    """Review Focus 3. Red: mutation `_write_summary(state, root)` added to abort() before
    the unlink; failed at the second start (RecordRefused: already took this pool)."""
    closed_parent(git_repo)
    ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c1", root=git_repo)
    ps.abort("blocked", root=git_repo)
    assert ps.start("STANDARD", mode="DEFERRED", parent="parent", run_id="c2",
                    root=git_repo).parent == "parent"
```

Append to `tests/test_pipeline_cli.py`:

```python
def test_from_reaches_start(capsys, git_repo):
    """Red: mutation `parent=a.parent` dropped from the start call in _pipeline; failed at
    `assert code == 0` (exit 2: needs --from)."""
    from conftest import closed_parent
    closed_parent(git_repo)
    code, out, _ = _cli(capsys, "start", "--tier", "STANDARD", "--mode", "DEFERRED",
                        "--from", "parent", "--root", str(git_repo))
    assert code == 0 and out["started"]["parent"] == "parent"
```

- [ ] **Step 2: Run them; they fail**

Run: `python -m pytest tests/test_pipeline_state.py tests/test_pipeline_cli.py -q -k "deferred or from_ or commit_fallback"`
Expected: `ValueError: unknown mode 'DEFERRED'` and argparse errors (not red proof; Step 6 gives it).

- [ ] **Step 3: Gate core**

`MODES = ("DIFF", "HUNT", "DEFERRED")`. Nothing else in the core changes: `required_layers` and `predecessors` deliberately have no DEFERRED case.

- [ ] **Step 4: `pipeline_state.py`**

Add after the git helpers:

```python
_NO_DEFERRAL = (
    "a DEFERRED run cannot defer: its findings are the deferred ones, so each is verified and "
    "then fixed, or left UNCERTAIN for a human"
)


def _deferred_parent(tier: str, no_fix: bool, parent: str, root) -> tuple[str, str | None]:
    """Check the run a DEFERRED run follows; return (parent as stored, base_commit)."""
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
    summaries, _ = deferred.load_summaries(root)
    if parent.startswith("commit:"):
        sha = _commit_of(root, parent.removeprefix("commit:"))
        if sha is None or not _is_ancestor(root, sha):
            raise RecordRefused(
                f"--from {parent} does not name a commit that is an ancestor of HEAD"
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
            "is taken with --from commit:<sha>"
        )
    why_not = deferred.why_not_offered(summary, summaries)
    if why_not:
        raise RecordRefused(f"run {parent} cannot be followed up: {why_not}")
    return parent, summary.get("base_commit")
```

In `start`, right after the mode check:

```python
    if parent and mode != "DEFERRED":
        raise RecordRefused(
            f"--from names the run a DEFERRED run follows up; it is refused with --mode {mode}"
        )
```

and replace the base commit block (after the open-run and completed-id checks) with:

```python
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
```

and pass `parent=stored_parent` to `PipelineState(...)`.

In `_check_counts`, in the L2.5 branch right after `stale = ...`, and in the L3 branch right after `n_deferred = ...`:

```python
        if n_deferred and state.mode == "DEFERRED":
            raise RecordRefused(_NO_DEFERRAL)
```

- [ ] **Step 5: CLI**

In `main`: `pl_start.add_argument("--from", dest="parent", default="", help="--mode DEFERRED only: the completed run (or commit:<sha>) whose deferred findings this run takes")`. In `_pipeline`'s start call add `parent=a.parent`.

- [ ] **Step 6: Run green, red proof, whole suite, commit**

Run the Step 2 selection green. Apply each docstring mutation, confirm the named failure, restore, date the `Red:` lines. `python -m pytest -q`, `ruff check cca_checks tests`, `python -m pyright`.

```bash
git add cca_checks tests
git commit -F - <<'EOF'
feat(pipeline): DEFERRED runs follow a completed run, owe every layer, and cannot defer

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 7: `pipeline deferred`, the report and the L1 writer

**Files:**
- Modify: `cca_checks/pipeline_state.py` (`deferred_report`, `write_deferred`, `__all__`)
- Modify: `cca_checks/__main__.py` (`deferred` subcommand)
- Test: `tests/test_pipeline_cli.py`

**Interfaces:**
- Consumes: everything in `deferred.py` (Task 5); `_is_ancestor` (Task 4); `core._inside`, `core.EXCLUDED`.
- Produces: `pipeline_state.deferred_report(run_id: str = "", root=".") -> deferred.Offer`; `pipeline_state.write_deferred(output, run_id: str = "", root=".") -> tuple[deferred.Offer, str]`; CLI `pipeline deferred [--run <id>] [--write <file>] [--root <dir>]` printing `Offer.to_json()` (plus `"written": <path>` with `--write`), exit 2 on refusal.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_pipeline_cli.py`)

```python
# --- pipeline deferred ---------------------------------------------------------------

def test_deferred_reports_the_newest_run_and_recommends(capsys, git_repo):
    """Red: mutation `summary = summaries[-1]` became `summaries[0]` in _deferred_context, with
    two parents; failed at `assert out["run_id"] == "second"`."""
    from conftest import closed_parent
    closed_parent(git_repo, run_id="first", p3=1)
    closed_parent(git_repo, run_id="second", p3=3)
    code, out, _ = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 0 and out["run_id"] == "second"
    assert out["offered"] is True and out["recommendation"] == "RUN"
    assert out["counts"]["P3"] == 3 and out["other_pools"] == ["first"]


def test_deferred_write_copies_exactly_the_pool(capsys, git_repo):
    """Red: mutation write_deferred dumps the parent's whole stored L2 file; failed at
    `assert ... == ["F-2", "F-3"]` (got F-1 to F-3)."""
    from conftest import closed_parent
    closed_parent(git_repo, p3=2)
    target = git_repo / ".claude/audits/out/L1.json"
    code, out, _ = _cli(capsys, "deferred", "--write", str(target), "--root", str(git_repo))
    written = json.loads(target.read_text(encoding="utf-8"))
    assert code == 0 and [f["id"] for f in written] == ["F-2", "F-3"]
    assert all(f["priority"] == "P3" and "claim" in f for f in written)


def test_deferred_write_replaces_a_stale_l1_file(capsys, git_repo):
    from conftest import closed_parent
    closed_parent(git_repo, p3=3)
    target = git_repo / ".claude/audits/out/L1.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text('[{"id": "OLD-1"}]', encoding="utf-8")
    _cli(capsys, "deferred", "--write", str(target), "--root", str(git_repo))
    assert "OLD-1" not in target.read_text(encoding="utf-8")


def test_deferred_write_is_refused_outside_claude_audits(capsys, git_repo):
    """Red: mutation the `_inside` check in write_deferred deleted; failed at `assert code == 2`."""
    from conftest import closed_parent
    closed_parent(git_repo, p3=3)
    code, _, err = _cli(capsys, "deferred", "--write", str(git_repo / "L1.json"),
                        "--root", str(git_repo))
    assert code == 2 and "outside .claude/audits/" in err["reason"]
    assert not (git_repo / "L1.json").exists()


def test_deferred_write_is_refused_when_the_pass_is_not_offered(capsys, git_repo):
    """Red: mutation the `if not offer.offered:` check in write_deferred deleted; failed at
    `assert code == 2`."""
    from conftest import closed_parent
    closed_parent(git_repo, p3=0)
    code, _, err = _cli(capsys, "deferred", "--write",
                        str(git_repo / ".claude/audits/out/L1.json"), "--root", str(git_repo))
    assert code == 2 and "nothing was deferred" in err["reason"]


def test_deferred_without_a_summary_exits_2(capsys, git_repo):
    code, _, err = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 2 and "commit:<sha>" in err["reason"]


def test_deferred_names_a_run_directory_without_its_l2_file(capsys, git_repo):
    """Review Focus 1. Red: mutation the `except (OSError, ValueError)` in
    deferred.findings_for returns [] instead of raising; failed at `assert code == 2` (the
    report printed an empty pool, exit 0)."""
    from conftest import closed_parent
    closed_parent(git_repo)
    (git_repo / ".claude/audits/runs/parent/L2.json").unlink()
    code, _, err = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 2 and "L2.json" in err["reason"]


def test_deferred_reports_a_parent_commit_that_left_history(capsys, git_repo):
    """Review Focus 5. Red: mutation `reachable = True` in _deferred_context; failed at
    `assert out["commit_reachable"] is False`."""
    import subprocess
    from conftest import closed_parent
    closed_parent(git_repo)
    subprocess.run(["git", "-C", str(git_repo), "reset", "-q", "--hard", "HEAD~1"], check=True)
    code, out, _ = _cli(capsys, "deferred", "--root", str(git_repo))
    assert code == 0 and out["commit_reachable"] is False
    assert any("not an ancestor of HEAD" in r for r in out["reasons"])


def test_round_trip_from_closed_parent_to_a_started_deferred_run(capsys, git_repo):
    """Red: mutation `parent=a.parent` dropped from the start call in _pipeline; failed at
    `assert code == 0` on the start (exit 2: needs --from)."""
    from conftest import closed_parent
    r = str(git_repo)
    closed_parent(git_repo, p3=3)
    out_file = str(git_repo / ".claude/audits/out/L1.json")
    code, offer, _ = _cli(capsys, "deferred", "--write", out_file, "--root", r)
    assert code == 0 and offer["offered"]
    code, started, _ = _cli(capsys, "start", "--tier", "STANDARD", "--mode", "DEFERRED",
                            "--from", offer["run_id"], "--run-id", "child", "--root", r)
    assert code == 0 and started["started"]["parent"] == "parent"
    code, rec, _ = _cli(capsys, "record", "L1", "--detail", "loaded 3 from parent",
                        "--output", out_file, "--root", r)
    assert code == 0 and rec["output"] == ".claude/audits/runs/child/L1.json"
```

- [ ] **Step 2: Run them; they fail**

Run: `python -m pytest tests/test_pipeline_cli.py -q -k deferred`
Expected: argparse `invalid choice: 'deferred'` (exit 2 through SystemExit). Not red proof; Step 5 gives it.

- [ ] **Step 3: `pipeline_state.py`**

Add `"deferred_report", "write_deferred"` to `__all__`, then:

```python
def _deferred_context(run_id: str, root):
    """(summary, findings, Offer) for the named run, or for the newest completed one."""
    summaries, unreadable = deferred.load_summaries(root)
    if run_id:
        summary = next((s for s in summaries if s["run_id"] == run_id), None)
        if summary is None:
            raise RecordRefused(
                f"no completed run {run_id!r} has a summary under .claude/audits/runs/"
            )
    elif summaries:
        summary = summaries[-1]
    else:
        note = f" ({len(unreadable)} unreadable: {', '.join(unreadable)})" if unreadable else ""
        raise RecordRefused(
            "no completed run has a summary under .claude/audits/runs/" + note + "; for a "
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


def deferred_report(run_id: str = "", root: str | pathlib.Path = ".") -> deferred.Offer:
    """What a completed run deferred, and whether a second pass is offered and advised."""
    return _deferred_context(run_id, root)[2]


def write_deferred(output, run_id: str = "",
                   root: str | pathlib.Path = ".") -> tuple[deferred.Offer, str]:
    """Write the offered pool's full finding objects to `output`: a deferred run's L1 input."""
    _, findings, offer = _deferred_context(run_id, root)
    if not offer.offered:
        raise RecordRefused(
            f"run {offer.run_id}'s deferred findings are not offered: {offer.reasons[0]}"
        )
    path = pathlib.Path(output)
    if not path.is_absolute():
        path = pathlib.Path(root) / path
    if not core._inside(str(path), str(pathlib.Path(root) / core.EXCLUDED)):
        raise RecordRefused(
            f"--write {output} is outside .claude/audits/; write it there (for example "
            ".claude/audits/out/L1.json), where the tree fingerprint ignores it"
        )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(findings, indent=2) + "\n", encoding="utf-8")
    return offer, str(path)
```

- [ ] **Step 4: CLI**

In `_pipeline`, before `if a.pipeline_cmd == "abort":`:

```python
        if a.pipeline_cmd == "deferred":
            try:
                if a.write:
                    offer, path = pipeline_state.write_deferred(a.write, a.run, root=a.root)
                    print(json.dumps({**offer.to_json(), "written": path}))
                    return 0
                offer = pipeline_state.deferred_report(a.run, root=a.root)
            except pipeline_state.RecordRefused as exc:
                print(json.dumps({"refused": "deferred", "reason": str(exc)}), file=sys.stderr)
                return 2
            print(json.dumps(offer.to_json()))
            return 0
```

In `main`, after the `close` parser:

```python
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
```

- [ ] **Step 5: Run green, red proof, whole suite, commit**

Run `python -m pytest tests/test_pipeline_cli.py -q -k deferred` green; apply each docstring mutation, confirm the named failure, restore, date. `python -m pytest -q`, `ruff check cca_checks tests`, `python -m pyright`.

```bash
git add cca_checks tests
git commit -F - <<'EOF'
feat(pipeline): `pipeline deferred` reports the pool and writes a deferred run's L1 input

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 8: the scorecard does not count STALE as disposed

**Files:**
- Modify: `cca_checks/plugin/tools/cca_scorecard.py` (the `build` loop)
- Test: `cca_checks/plugin/tools/test_cca_scorecard.py`

- [ ] **Step 1: Write the test** (append)

```python
def test_stale_rows_never_count_toward_a_yield_flag(tmp_path):
    """A STALE row says the code moved on after a run deferred the finding, not that the
    auditor was wrong, so it must not count as disposed.

    Red: baseline (every verdict row increments `disposed`); failed at
    `assert rep.yield_flags == []` (got ["bug-auditor"]).
    """
    led = write(tmp_path, [row("STALE") for _ in range(15)])
    rep = sc.build(led, now=NOW)
    assert rep.yield_flags == []
    assert rep.cells == []
```

- [ ] **Step 2: Run it; it fails on its assertion** (this is the red proof, against the unchanged code)

Run: `python -m pytest cca_checks/plugin/tools/test_cca_scorecard.py -q -k stale`
Expected: FAIL at `assert rep.yield_flags == []`. Date the docstring's `Red:` line.

- [ ] **Step 3: Implement**

In `build`, replace `per_auditor[row["auditor"]]["disposed"] += 1` with:

```python
        if verdict != "STALE":
            # STALE: the code moved on after a run deferred the finding; that says nothing
            # about whether the auditor was right, so it is not a disposition.
            per_auditor[row["auditor"]]["disposed"] += 1
```

- [ ] **Step 4: Run green, whole file, commit**

Run: `python -m pytest cca_checks/plugin/tools/test_cca_scorecard.py -q` then `ruff check cca_checks`.

```bash
git add cca_checks/plugin/tools/cca_scorecard.py cca_checks/plugin/tools/test_cca_scorecard.py
git commit -F - <<'EOF'
fix(scorecard): a STALE verdict is not a disposition

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 9: `/audit-fix` prose asks for what the gate now enforces

**Files:**
- Modify: `cca_checks/plugin/commands/audit-fix.md`
- Test: `tests/test_pipeline_prose.py`

- [ ] **Step 1: Write the prose tests** (append to `tests/test_pipeline_prose.py`)

```python
def test_contract_records_deferrals_and_the_base():
    """Red: baseline 0ec8c47 text; failed at `assert "--deferred <P>" in row25`."""
    contract = PIPELINE[PIPELINE.find("## The Contract"):PIPELINE.find("## Arguments")]
    rows = {ln.split("|")[1].strip(): ln for ln in contract.splitlines()
            if ln.startswith("| ") and ln.count("|") >= 4}
    row25, row3, row06, row7 = rows["2.5"], rows["3"], rows["0.6"], rows["7"]
    assert "--deferred <P>" in row25
    assert "--deferred <D>" in row3 and '--detail "<D> deferred"' not in row3
    assert "--base <REV>" in row06
    assert "pipeline deferred" in row7


def test_step_2_5_defers_unverified_findings_on_the_record():
    """Red: baseline 0ec8c47 text; failed at `assert "P3 are deferred anyway" not in section`."""
    section = PIPELINE[PIPELINE.find("## Step 2.5:"):PIPELINE.find("### Step 2.5b")]
    assert "P3 are deferred anyway" not in section
    assert "`DEFERRED`" in section and "--deferred" in section


def test_step_3_plans_or_defers_every_confirmed_finding():
    """Red: baseline 0ec8c47 text; failed at `assert "--deferred <D>" in section`."""
    section = PIPELINE[PIPELINE.find("## Step 3:"):PIPELINE.find("## Fix Attempt Budget")]
    assert "--deferred <D>" in section
    assert '"to_fix"' in section and '"deferred"' in section


def test_the_summary_offers_the_pass_only_after_close():
    """Red: baseline 0ec8c47 text; failed at `assert offer >= 0`."""
    summary = PIPELINE[PIPELINE.find("## Output Summary"):PIPELINE.find("## Second Pass")]
    offer = summary.find("python -m cca_checks pipeline deferred")
    assert offer >= 0
    assert "After `pipeline close` has succeeded" in summary
    assert "To fix deferred items, run" not in summary


def test_the_second_pass_is_a_gated_run():
    """Red: baseline 0ec8c47 text; failed at `assert "--mode DEFERRED --from" in section`."""
    section = PIPELINE[PIPELINE.find("## Second Pass"):]
    assert "pipeline deferred --write .claude/audits/out/L1.json" in section
    assert "pipeline start --mode DEFERRED --from" in section
    assert "--from commit:<sha>" in section
    assert "MODE=DEFERRED" in section and "STALE" in section
    assert "--no-proofs" in section
```

- [ ] **Step 2: Run them; they fail on their assertions** (red proof against the unchanged text)

Run: `python -m pytest tests/test_pipeline_prose.py -q`
Expected: the five new tests FAIL at the assertions named in their docstrings; the old ones pass. Date the `Red:` lines.

- [ ] **Step 3: Edit the Contract table** (lines 21, 24, 26, 32 of `audit-fix.md`)

```markdown
| 0.6 | tier chosen | `pipeline start --tier <TIER> --base <REV> [--no-fix]` *(DIFF mode; a hunt already started at 0.4)* |
| 2.5 | **verify findings** | `pipeline record L2.5 --confirmed <C> --fp <F> --uncertain <U> --duplicate <D> --deferred <P> --output .claude/audits/out/L2.5.json` |
| 3 | fix plan | `pipeline record L3 --to-fix <K> --deferred <D> --output .claude/audits/out/L3.json` |
| 7 | commit | `pipeline close`, then `pipeline deferred` to offer the deferred pass (§ Output Summary) |
```

- [ ] **Step 4: Edit Arguments** (line 99 and the precedence note)

```markdown
- `deferred` = the deferred pass: a gated run over what the last completed run deferred (its P3
  findings, and the P2 findings it left unfixed). See § Second Pass.
```

```markdown
> **Argument precedence:** `no-fix` always wins. If `no-fix` is present, never edit or commit. On
> the `deferred` path that means: run `python -m cca_checks pipeline deferred`, report what it
> prints, and stop.
```

- [ ] **Step 5: Edit Step 0.6's start block**

Replace the `pipeline start --tier <TIER> [--no-fix]` line with:

```bash
python -m cca_checks pipeline start --tier <TIER> --base <REV> [--no-fix]   # a hunt already started at Step 0.4
```

and add, directly under the code block:

```markdown
`<REV>` is the revision DIFF_CMD diffs against: `HEAD`, or `HEAD~N` for `commit N` (and `HEAD~1`
when Step 0 fell back to the last commit). The run stores its sha as `base_commit`; a later
deferred pass uses it as its scope.
```

- [ ] **Step 6: Edit Step 2's record paragraph**

Directly above the Step 2 `**→ Record.**`, add:

```markdown
**The L2 file** is a JSON array of the consolidated finding objects (Findings Schema), one per
unique finding, each with its `id` and a `priority`. `record` refuses a file whose length is not
`--unique`. A later deferred pass reads findings back from this file by id.
```

- [ ] **Step 7: Replace Step 2.5's first paragraph** (the six lines from "Verify P1/P2 are real" to "never skipped.")

```markdown
Verify P1/P2 are real before any fix. P3 findings are not verified in this run: each is written
to the L2.5 file with the verdict `DEFERRED` and counted in `--deferred`, and the deferred pass
(§ Second Pass) verifies it before it is ever edited. FAST verifies P1 only, with a single
`fp-check`, so on FAST the P2 findings are `DEFERRED` too. The layer is recorded on every tier. If
there are zero P1/P2 findings, nothing goes to `fp-check`, but the layer is still owed: record it
with zero counts (`python -m cca_checks pipeline record L2.5 --confirmed 0 --fp 0 --uncertain 0
--duplicate 0 --deferred <P> --output .claude/audits/out/L2.5.json`, the file listing the deferred
findings, or an empty list). A layer with nothing to do is recorded, never skipped.

**The L2.5 file** is a JSON array with one object per L2 finding,
`{"id": "<finding id>", "verdict": "CONFIRMED|FALSE_POSITIVE|UNCERTAIN|DUPLICATE|DEFERRED"}`, plus
any evidence keys you keep. `record` refuses a file whose tally per verdict disagrees with the
counts, or that names an id L2 did not record.
```

- [ ] **Step 8: Edit Step 2.5b**

Change "append one row per verified finding" to "append one row per verified finding (a `DEFERRED` finding gets none: it received no verdict)". Change "counts these rows and refuses when they do not match the verdicts" to "counts these rows and refuses when they do not match the verdicts other than `DEFERRED`". In the record command of 2.5b, insert `--deferred <P>` after `--duplicate <D>`.

- [ ] **Step 9: Replace Step 3** (from "(Reachable only when" to the end of its code block)

````markdown
(Reachable only when `no-fix` is absent.) From CONFIRMED findings only: always fix P1; fix P2 unless
`p1-only`, in which case defer it. Every CONFIRMED finding is either planned or deferred: `record`
refuses an L3 whose `--to-fix` and `--deferred` do not add up to L2.5's `--confirmed`. (P3 findings
never reach this step as CONFIRMED: L2.5 recorded them `DEFERRED`.)

**→ Record.** The file is `{"to_fix": [<ids>], "deferred": [<ids>]}`, every id CONFIRMED at L2.5.

```bash
python -m cca_checks pipeline record L3 --to-fix <N> --deferred <D> \
  --output .claude/audits/out/L3.json   # the fix plan: finding ids to fix, deferred ids
```
````

- [ ] **Step 10: Edit Step 7's commit template**

Replace

```
P3:
- <deferred items>
```

with

```
Deferred:
- <id> <priority> <file:line> <claim>, for every DEFERRED finding and every id L3 deferred
```

- [ ] **Step 11: Edit the Output Summary**

In its code block: the Fix Plan row becomes `| 3. Fix Plan | DONE: Z planned (P1:A P2:B); D deferred (L2.5 and L3) |`; `### Uncertain — needs human   ### Deferred (P3)` becomes `### Uncertain, needs human   ### Deferred (the pool)`; delete the line `💡 To fix deferred items, run: \`/audit-fix deferred\``. After the code block, add:

````markdown
### Offer the deferred pass

After `pipeline close` has succeeded, and never before it:

```bash
python -m cca_checks pipeline deferred
```

It prints the pool of the run just closed (`pool`, `counts`), whether a second pass is `offered`,
the `recommendation` (`RUN` or `SKIP`) and its `reasons`. When `offered` is false, report the
first reason in one line and stop. When it is true, end with one question and nothing after it:

> N findings were deferred (P2: a, P3: b). Recommendation: RUN (or SKIP), because <reasons[0]>.
> Run the deferred pass now?

Yes: continue with § Second Pass in this session. No: stop; `/audit-fix deferred` still works
later, until a completed deferred run takes the pool. The recommendation is advice: SKIP never
refuses the pass. A `no-fix` run, a deferred run and a hunt are never offered one, and the
command says which. If `commit_reachable` is false, repeat that reason too: it weakens the STALE
check of the pass.
````

- [ ] **Step 12: Replace § Second Pass entirely** (from `## Second Pass` to the end of the file)

````markdown
## Second Pass: the Deferred Run

`/audit-fix deferred` runs this pipeline over the findings the last completed run deferred: every
`DEFERRED` finding of its L2.5 (its P3 findings, and its P2 findings on FAST) and every id its L3
deferred (P2 findings under `p1-only`). It is a CCA run like any other: it owes every layer a
fixing run of its tier owes, and the commit guard and the Stop hook hold it to that. What differs
is where its findings come from and how they are verified.

If `no-fix` was passed, run `python -m cca_checks pipeline deferred`, report what it prints, and
stop. Never edit or commit.

**D.1 Load the pool and open the run.**

```bash
python -m cca_checks pipeline deferred --write .claude/audits/out/L1.json   # --run <run_id> for an older run
python -m cca_checks pipeline start --mode DEFERRED --from <run_id> --tier <TIER>
```

`<run_id>` is the `run_id` the first command printed. `<TIER>` is DEEP when that run was DEEP, or
when any pool finding has `high_stakes: true` or sits on a high-stakes path by Step 0.5;
otherwise STANDARD. FAST is refused, because it verifies P1 only and every deferred finding must
be verified. The run takes its scope (`base_commit`) from the parent. `start` refuses a parent
that is itself a deferred run, a hunt or a `no-fix` run, whose pool is empty, or whose pool a
completed deferred run already took. An aborted deferred run takes nothing.

**No summary to read** (the first command exits 2 because the checkout that ran the audit is
gone, or the run predates 0.11): find the last commit whose subject contains "audit fixes from
CCA" (`git log -5 --format='%H %s'`), parse its `Deferred:` section (`P3:` before 0.11) into
`.claude/audits/out/L1.json` as Findings Schema objects carrying `"source": "commit-message"` and
a `priority` (P3 unless the line says P2), and start with `--from commit:<sha>`. The scope check
of D.4 cannot run then; say so in the report and in the commit message.

**D.2 Step 1 (L1).** No auditors. Record the file D.1 wrote:

```bash
python -m cca_checks pipeline record L1 --detail "loaded <N> from <run_id>" \
  --output .claude/audits/out/L1.json
```

**D.3 Step 2 (L2).** Re-anchor each finding to its current file and line: the parent's own fixes,
and anything committed since, may have moved it. Keep the parent's `priority`. Merge duplicates by
the usual key. Record as Step 2 says.

**D.4 Step 2.5 (L2.5).** One `fp-check` over every finding, P2 and P3 alike, in MODE=DEFERRED,
given the parent's `base_commit` and `commit` (`pipeline deferred` printed the run id; both are in
`.claude/audits/runs/<run_id>/summary.json`):

```
subagent_type: fp-check
MODE=DEFERRED. For each finding, verify against ACTUAL code via Read/Grep:
  (a) does the issue exist at the cited (re-anchored) file:line?
  (b″) is the cited code inside `git diff <base_commit>`, the code the first run audited or
       fixed? Outside it ⇒ FALSE_POSITIVE, reason "out of scope". base_commit null ⇒ say this
       check could not run.
  (c) is the stated impact real, or already mitigated?
  (d) does it contradict an already-SETTLED decision?
STALE: the issue is present at the first run's commit (`git show <commit>:<file>`) and absent
now, because later work moved, rewrote or fixed it. FALSE_POSITIVE means it was never there.
Verdict per finding: CONFIRMED | FALSE_POSITIVE | STALE | UNCERTAIN.
```

Append one ledger row per finding (Step 2.5b), carrying this run's `run_id` and the ORIGINAL
finding's `auditor` and `category`, so a deferred finding confirmed now credits the auditor that
raised it. Record with `--stale <S>` and no `--deferred`: a deferred run cannot defer, and
`record` refuses it.

**D.5 Steps 2.6 to 5.5** run as written above. At Step 3 every CONFIRMED finding is planned;
UNCERTAIN goes to a human.

**D.6 Step 5.6.** The pool holds no P1, so record `python "$TC" verify --no-proofs --record`.

**D.7 Step 6.** The architect's fix to finding mapping covers EVERY CONFIRMED finding, P3
included: in this run they are the point.

**D.8 Step 7.** Commit, then close:

```
fix(<scope>): N deferred fixes from CCA (<TIER> tier, parent <run_id>)

- <id> FIXED <file:line>
- <id> STALE | FALSE_POSITIVE | UNCERTAIN <one line why>

Audit: deferred pass over <P> findings of run <run_id> → V confirmed → Z fixed. Regression diff:
clean. Architect: APPROVED.

Co-Authored-By: Claude <noreply@anthropic.com>
```

`pipeline close` records the parent, which takes its pool: it is never offered again. A deferred
run offers no further pass.
````

- [ ] **Step 13: Run all prose tests green, commit**

Run: `python -m pytest tests/test_pipeline_prose.py tests/test_codex_skill.py -q`
Expected: all pass (the Codex skill's `references/pipeline.md` is generated from this file at install, and `test_codex_skill_reuses_the_canonical_pipeline_and_agent_prompts` checks that equality).

```bash
git add cca_checks/plugin/commands/audit-fix.md tests/test_pipeline_prose.py
git commit -F - <<'EOF'
docs(pipeline): L2.5 and L3 record deferrals; offer the deferred pass after close; the pass is gated

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 10: agent prompts, the Codex skill, README, diagram, CHANGELOG

**Files:**
- Modify: `cca_checks/plugin/agents/cca-fp-check.md`, `cca_checks/plugin/agents/cca-fix-planner.md`, `cca_checks/plugin/codex/cca-audit/SKILL.md`, `README.md`, `docs/pipeline-diagram.md`, `claude-code/README.md`, `CHANGELOG.md`
- Test: `tests/test_pipeline_prose.py`

- [ ] **Step 1: Write the prose tests** (append to `tests/test_pipeline_prose.py`)

```python
FP_CHECK = (ROOT / "cca_checks/plugin/agents/cca-fp-check.md").read_text(encoding="utf-8")


def test_codex_offers_the_deferred_pass_after_close():
    """Red: baseline 0ec8c47 text; failed at
    `assert section.startswith("## Offer the deferred pass")`."""
    section = CODEX[CODEX.find("## Offer the deferred pass"):CODEX.find("## Report completion")]
    assert section.startswith("## Offer the deferred pass")
    assert "pipeline deferred" in section and "Never start it without that yes" in section


def test_fp_check_knows_mode_deferred():
    """Red: baseline 0ec8c47 text; failed at `assert "MODE=DEFERRED" in FP_CHECK`."""
    assert "MODE=DEFERRED" in FP_CHECK and "STALE" in FP_CHECK
    assert "P3 findings are not verified — they are\ndeferred regardless." not in FP_CHECK
```

- [ ] **Step 2: Run them; they fail on their assertions** (red proof; date the `Red:` lines)

Run: `python -m pytest tests/test_pipeline_prose.py -q -k "codex_offers or fp_check_knows"`

- [ ] **Step 3: `cca-fp-check.md`**

Replace the two lines "diff command used by the audit (e.g. `git diff HEAD`). P3 findings are not verified — they are deferred regardless." with:

```markdown
diff command used by the audit (e.g. `git diff HEAD`). In MODE=DIFF and MODE=HUNT you are never
sent P3 findings: the run records them `DEFERRED`. In MODE=DEFERRED you receive the deferred
findings themselves, P2 and P3 alike, and verify every one.
```

Under **In scope**, after the MODE=HUNT bullet, add:

```markdown
   - **MODE=DEFERRED** (a deferred pass over findings an earlier run deferred): is the cited
     code inside `git diff <base_commit>`, the code that run audited or fixed? Outside it is
     FALSE_POSITIVE, reason "out of scope". When no `base_commit` is given, say this check
     could not run. A fifth verdict exists in this mode only: **STALE**, when the issue is
     present at that run's commit (`git show <commit>:<file>`) and absent now because later
     work moved, rewrote or fixed it. FALSE_POSITIVE means it was never there.
```

(The neighbouring bullets put an em dash after the mode name; this one uses a colon, per the
no-dash rule for new prose. Do not restyle the old bullets.)

- [ ] **Step 4: `cca-fix-planner.md`**

Change `**P3 — Nice-to-have** (Defer or skip)` to `**P3 — Nice-to-have** (Deferred: recorded DEFERRED at L2.5, offered to the deferred pass after the run closes)`, and under `## P3 — Deferred` replace the `...` line with `List each with its id, file:line and claim; these become the pool of \`/audit-fix deferred\`.`

- [ ] **Step 5: Codex `SKILL.md`**

Insert between "## Gate before reporting" and "## Report completion":

```markdown
## Offer the deferred pass

After `pipeline close` succeeds, run `python -m cca_checks pipeline deferred`. When it prints
`"offered": true`, end the report with one question: how many findings were deferred, by
priority; the `recommendation` (RUN or SKIP) and its first reason; and whether to run the
deferred pass now. On yes, run `audit+fix deferred` as the canonical § Second Pass describes,
in this task. Never start it without that yes. When it prints `"offered": false`, report the
reason in one line.
```

- [ ] **Step 6: `README.md`**

Usage block: `/audit-fix deferred        # the deferred pass: a gated run over what the last run deferred`. Priority table P3 row action: `Deferred; offered as a gated second pass`. Replace the "Round 2 (`/audit-fix deferred`) reads ..." paragraph with:

```markdown
When a fixing run closes, `pipeline deferred` lists what it deferred (its P3 findings, and the P2
findings it left unfixed under `p1-only` or on FAST) with a RUN or SKIP recommendation, and the
run asks whether to start the deferred pass. `/audit-fix deferred` is a full gated run over that
pool: `fp-check` verifies every finding before it is edited, findings whose code has moved on
are marked STALE, and the run owes the regression diff and the architect gate like any other. It
cannot defer again, so no audit leaves a tail.
```

- [ ] **Step 7: `docs/pipeline-diagram.md` and `claude-code/README.md`**

Diagram line 50: `Step 2.5, and skips Step 5.5. On FAST, P2 and P3 findings are recorded DEFERRED rather than fixed, and offered to the deferred pass.` Step 3 bullet: `- **P3 Nice-to-have**: deferred (and P2 under \`p1-only\`); after the run closes, the deferred pass is offered (see \`audit-fix.md\` § Second Pass).` `claude-code/README.md` line 64: `/audit-fix deferred           # Gated second pass over what the last run deferred`.

- [ ] **Step 8: `CHANGELOG.md`** (under `## Unreleased`)

Under `### Changed` add:

```markdown
- L2.5 counts findings passed on unverified (P3, and P2 on FAST) with `--deferred`, and its file must give every L2 finding exactly one verdict, `DEFERRED` included. They used to be filed under a verdict they never received. A `DEFERRED` finding gets no ledger row.
- L3 must plan or defer every confirmed finding (`--to-fix` plus `--deferred` equals L2.5's confirmed); `--deferred` replaces the free text `--detail "<D> deferred"`.
- `record` checks the L2, L2.5 and L3 files against their counts and against each other, and refuses a file that disagrees.
- `pipeline start --base <rev>` stores the commit the audited diff is taken against (default `HEAD`); `--mode` is validated.
- The scorecard no longer counts `STALE` rows as disposed.
```

Under `### Added` add:

```markdown
- `pipeline close` writes `.claude/audits/runs/<run_id>/summary.json`: tier, mode, base and final commit, and the deferred pool.
- `pipeline deferred [--run <id>] [--write <file>]`: what the last completed run deferred, whether a second pass is offered, and RUN or SKIP with reasons; `--write` produces the deferred run's L1 input.
- `pipeline start --mode DEFERRED --from <run_id>` (or `--from commit:<sha>`): a deferred pass that owes every layer, refuses FAST, and cannot defer again. `/audit-fix` offers it after a run closes.
```

- [ ] **Step 9: Run green, whole suite, commit**

Run: `python -m pytest -q` and `ruff check`.

```bash
git add cca_checks/plugin/agents cca_checks/plugin/codex/cca-audit/SKILL.md README.md docs/pipeline-diagram.md claude-code/README.md CHANGELOG.md tests/test_pipeline_prose.py
git commit -F - <<'EOF'
docs: fp-check MODE=DEFERRED, the Codex offer, README, diagram and changelog for the deferred pass

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

---

### Task 11: whole-branch verification

**Files:** `README.md` (the test count only, if it changed)

- [ ] **Step 1: The three checks, in CI order**

Run: `python -m pyright` then `ruff check` then `python -m pytest -q`
Expected: zero pyright errors, ruff clean, every test passing. Note the passing count.

- [ ] **Step 2: Every new behaviour test carries a dated red receipt**

Run: `git diff origin/master --stat -- tests cca_checks/plugin/tools` and, for each new test function in that diff, confirm its docstring has a `Red <date>:` line naming a mutation (or the baseline text) and the assertion that failed. Task 1's helpers are the only labelled nonbehavioral control. A test without a receipt is not done: produce the receipt, or report it as unproven.

- [ ] **Step 3: README test count**

If `README.md`'s Engineering table states a test count, set it to the Step 1 count.

- [ ] **Step 4: Commit**

```bash
git add README.md
git commit -F - <<'EOF'
docs(readme): test count after the deferred pass

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>
EOF
```

Do not reinstall CCA user-wide and do not open a pull request: both are the user's call.
