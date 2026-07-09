# CCA v3.0-min: Deterministic Verification — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade CCA's `fp-check` gate so verdicts on Python findings are carried by real tools (`pyright`, a generated `pytest` repro) instead of a second LLM opinion — with graceful fallback to today's behavior.

**Architecture:** A small, unit-tested Python package `cca_checks/` wraps the deterministic tools and maps their output to a `Verdict{CONFIRMED|FALSE_POSITIVE|UNCERTAIN, evidence, source}`. The `cca-fp-check` agent classifies each finding into a `claim_type`, calls `cca_checks` via shell for the checkable classes, requires the returned artifact, and falls back to LLM adjudication only for the residue.

**Tech Stack:** Python 3.10+, `pyright` (CLI, `--outputjson`), `pytest` (subprocess), stdlib `dataclasses`/`json`/`subprocess`/`argparse`. Package tested with `pytest`.

## Global Constraints

- Repo: `cca-audit`, branch `v3-design`. License MIT.
- **Never regress v2:** any missing tool / unsupported language / uncovered `claim_type` → fall back to LLM-adjudication (flagged `LLM-adjudicated` in the evidence column).
- **Artifact-or-UNCERTAIN:** a `CONFIRMED` or `FALSE_POSITIVE` verdict MUST carry a non-empty `evidence` string; otherwise it becomes `UNCERTAIN`.
- **Repro respects the boundary:** a generated repro drives the code's public entry point, never the raw internal function. "Couldn't reproduce" → `UNCERTAIN`, never a silent refute.
- **Line indexing:** pyright `range.start.line` is **0-indexed**; findings are **1-indexed**. Convert at the boundary (`pyright_line + 1 == finding_line`).

## File Structure

- Create `cca_checks/__init__.py` — package marker.
- Create `cca_checks/claim.py` — `Claim`, `Verdict`, `make_verdict()` (enforces the artifact rule).
- Create `cca_checks/pyright_check.py` — run pyright, parse, `verdict_for_definedness()`.
- Create `cca_checks/repro_runner.py` — run a generated pytest, map to `Verdict`.
- Create `cca_checks/__main__.py` — CLI dispatch (`definedness`, `repro`) → one JSON line.
- Create `tests/test_claim.py`, `tests/test_pyright_check.py`, `tests/test_repro_runner.py`, `tests/test_cli.py`.
- Create `tests/fixtures/` — sample repro tests (one that raises, one that passes).
- Create `pyproject.toml` — deps + pytest config.
- Modify `claude-code/agents/cca-fp-check.md` — the two-phase protocol.
- Create `examples/bps-sizing/definedness_trap/` — clean `definedness` fixture (symbol defined off-diff).
- Create `tests/acceptance/test_trap_suite.py` — runs the checkers against the fixtures, asserts verdicts (skips if tools absent).
- Modify `docs/v3-design.md` — flip status line once v3.0-min lands.

---

### Task 1: Claim & Verdict schema + package scaffold

**Files:**
- Create: `pyproject.toml`, `cca_checks/__init__.py`, `cca_checks/claim.py`
- Test: `tests/test_claim.py`

**Interfaces:**
- Produces: `Claim(finding_id:str, file:str, line:int, claim_type:str, proposition:str="", predicted_impact:str="")`; `Verdict(finding_id:str, verdict:str, evidence:str, source:str)`; `make_verdict(finding_id, verdict, evidence, source) -> Verdict`.

- [ ] **Step 1: Scaffold the package + test tooling**

Create `pyproject.toml`:
```toml
[project]
name = "cca_checks"
version = "0.1.0"
requires-python = ">=3.10"

[tool.pytest.ini_options]
testpaths = ["tests"]
```
Create empty `cca_checks/__init__.py`.

- [ ] **Step 2: Write the failing test**

`tests/test_claim.py`:
```python
from cca_checks.claim import Claim, Verdict, make_verdict

def test_confirmed_without_evidence_becomes_uncertain():
    v = make_verdict("BUG-1", "CONFIRMED", "", "pyright")
    assert v.verdict == "UNCERTAIN"

def test_confirmed_with_evidence_stands():
    v = make_verdict("BUG-1", "CONFIRMED", "pyright: undefined X", "pyright")
    assert v.verdict == "CONFIRMED"
    assert v.source == "pyright"

def test_claim_is_constructible():
    c = Claim("BUG-1", "sizer.py", 12, "definedness", "X undefined")
    assert c.line == 12 and c.claim_type == "definedness"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_claim.py -v`
Expected: FAIL — `ModuleNotFoundError: cca_checks.claim`.

- [ ] **Step 4: Write minimal implementation**

`cca_checks/claim.py`:
```python
from dataclasses import dataclass

@dataclass(frozen=True)
class Claim:
    finding_id: str
    file: str
    line: int  # 1-indexed
    claim_type: str
    proposition: str = ""
    predicted_impact: str = ""

@dataclass(frozen=True)
class Verdict:
    finding_id: str
    verdict: str  # CONFIRMED | FALSE_POSITIVE | UNCERTAIN
    evidence: str
    source: str   # pyright | pytest | llm

def make_verdict(finding_id: str, verdict: str, evidence: str, source: str) -> Verdict:
    # Artifact-or-UNCERTAIN: a decisive verdict must carry evidence.
    if verdict in ("CONFIRMED", "FALSE_POSITIVE") and not evidence.strip():
        return Verdict(finding_id, "UNCERTAIN", "no evidence artifact; escalated", source)
    return Verdict(finding_id, verdict, evidence, source)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_claim.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml cca_checks/__init__.py cca_checks/claim.py tests/test_claim.py
git commit -m "feat(cca_checks): Claim/Verdict schema with artifact-or-UNCERTAIN rule"
```

---

### Task 2: pyright adapter (definedness)

**Files:**
- Create: `cca_checks/pyright_check.py`
- Test: `tests/test_pyright_check.py`

**Interfaces:**
- Consumes: `Claim`, `make_verdict` (Task 1).
- Produces: `run_pyright(path:str) -> list[dict]` (pyright `generalDiagnostics`); `verdict_for_definedness(claim:Claim, diags:list[dict]) -> Verdict`.

**Scope note:** this slice wires only `definedness` (the highest-value false-positive class). `type` and `nullability` reuse the *identical* mechanism — add `TYPE_RULES` / `NULLABILITY_RULES` sets and a generic `verdict_for_claim(claim, diags, rules)` — and are a fast follow (v3.1), deliberately deferred to keep the first slice minimal. Until then, `type`/`nullability` findings ride the v2 LLM fallback (Task 5, Phase 2).

- [ ] **Step 1: Write the failing test** (mock the diagnostics — no pyright needed for the unit test)

`tests/test_pyright_check.py`:
```python
from cca_checks.claim import Claim
from cca_checks.pyright_check import verdict_for_definedness

def _claim(line): return Claim("ENV-1", "sizer.py", line, "definedness", "X undefined")

def test_undefined_reported_confirms():
    diags = [{"rule": "reportUndefinedVariable", "message": "X is not defined",
              "range": {"start": {"line": 11}}}]  # 0-indexed -> line 12
    v = verdict_for_definedness(_claim(12), diags)
    assert v.verdict == "CONFIRMED" and v.source == "pyright"

def test_symbol_defined_refutes():
    v = verdict_for_definedness(_claim(12), [])  # pyright silent = defined
    assert v.verdict == "FALSE_POSITIVE"

def test_diag_on_other_line_refutes():
    diags = [{"rule": "reportUndefinedVariable", "message": "Y", "range": {"start": {"line": 40}}}]
    v = verdict_for_definedness(_claim(12), diags)
    assert v.verdict == "FALSE_POSITIVE"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_pyright_check.py -v`
Expected: FAIL — `ModuleNotFoundError: cca_checks.pyright_check`.

- [ ] **Step 3: Write minimal implementation**

`cca_checks/pyright_check.py`:
```python
import json
import subprocess
from typing import Optional
from .claim import Claim, Verdict, make_verdict

DEFINEDNESS_RULES = {"reportUndefinedVariable", "reportUnboundVariable", "reportMissingImports"}

def run_pyright(path: str) -> list[dict]:
    proc = subprocess.run(["pyright", "--outputjson", path], capture_output=True, text=True)
    try:
        data = json.loads(proc.stdout or "{}")
    except json.JSONDecodeError:
        return []
    return data.get("generalDiagnostics", [])

def _diag_at(diags: list[dict], line_1based: int, rules: set[str]) -> Optional[dict]:
    for d in diags:
        start = d.get("range", {}).get("start", {})
        if start.get("line", -1) + 1 == line_1based and d.get("rule") in rules:
            return d
    return None

def verdict_for_definedness(claim: Claim, diags: list[dict]) -> Verdict:
    hit = _diag_at(diags, claim.line, DEFINEDNESS_RULES)
    if hit:
        ev = f"pyright {hit['rule']} @ {claim.file}:{claim.line}: {hit['message']}"
        return make_verdict(claim.finding_id, "CONFIRMED", ev, "pyright")
    ev = f"pyright: no undefined-symbol diagnostic @ {claim.file}:{claim.line}"
    return make_verdict(claim.finding_id, "FALSE_POSITIVE", ev, "pyright")
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_pyright_check.py -v`
Expected: PASS (3 passed).

- [ ] **Step 5: Commit**

```bash
git add cca_checks/pyright_check.py tests/test_pyright_check.py
git commit -m "feat(cca_checks): pyright definedness adapter"
```

---

### Task 3: pytest repro runner (impact)

**Files:**
- Create: `cca_checks/repro_runner.py`, `tests/fixtures/raises_test.py`, `tests/fixtures/passes_test.py`
- Test: `tests/test_repro_runner.py`

**Interfaces:**
- Consumes: `make_verdict` (Task 1).
- Produces: `run_repro(finding_id:str, test_path:str, expected_error:Optional[str]) -> Verdict`.

- [ ] **Step 1: Write the fixtures**

`tests/fixtures/raises_test.py`:
```python
def test_repro():
    raise ZeroDivisionError("division by zero")
```
`tests/fixtures/passes_test.py`:
```python
def test_repro():
    assert 1 == 1
```

- [ ] **Step 2: Write the failing test**

`tests/test_repro_runner.py`:
```python
from cca_checks.repro_runner import run_repro

def test_failing_repro_confirms():
    v = run_repro("BUG-1", "tests/fixtures/raises_test.py", "ZeroDivisionError")
    assert v.verdict == "CONFIRMED" and v.source == "pytest"

def test_passing_repro_is_uncertain_not_refuted():
    v = run_repro("BUG-1", "tests/fixtures/passes_test.py", "ZeroDivisionError")
    assert v.verdict == "UNCERTAIN"

def test_wrong_error_is_uncertain():
    v = run_repro("BUG-1", "tests/fixtures/raises_test.py", "KeyError")
    assert v.verdict == "UNCERTAIN"
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_repro_runner.py -v`
Expected: FAIL — `ModuleNotFoundError: cca_checks.repro_runner`.

- [ ] **Step 4: Write minimal implementation**

`cca_checks/repro_runner.py`:
```python
import subprocess
from typing import Optional
from .claim import Verdict, make_verdict

def run_repro(finding_id: str, test_path: str, expected_error: Optional[str]) -> Verdict:
    proc = subprocess.run(["python", "-m", "pytest", "-xq", test_path],
                          capture_output=True, text=True)
    out = (proc.stdout or "") + (proc.stderr or "")
    tail = out[-800:]
    if proc.returncode != 0:  # a failing test == the impact reproduced
        if expected_error and expected_error not in out:
            return make_verdict(finding_id, "UNCERTAIN",
                                f"repro failed but not with '{expected_error}':\n{tail}", "pytest")
        return make_verdict(finding_id, "CONFIRMED", f"repro reproduced the impact:\n{tail}", "pytest")
    return make_verdict(finding_id, "UNCERTAIN",
                        "repro did not trigger the impact through the validated boundary; escalated",
                        "pytest")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_repro_runner.py -v`
Expected: PASS (3 passed).

- [ ] **Step 6: Commit**

```bash
git add cca_checks/repro_runner.py tests/fixtures/ tests/test_repro_runner.py
git commit -m "feat(cca_checks): pytest repro runner (confirm-on-fail, escalate-on-pass)"
```

---

### Task 4: CLI so the agent can call the checkers via shell

**Files:**
- Create: `cca_checks/__main__.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `Claim`, `run_pyright`, `verdict_for_definedness`, `run_repro`.
- Produces: `main(argv:list[str]) -> int`; prints one JSON line `{finding_id, verdict, evidence, source}`. Invoked as `python -m cca_checks definedness|repro ...`.

- [ ] **Step 1: Write the failing test** (monkeypatch pyright so no tool is needed)

`tests/test_cli.py`:
```python
import json
import cca_checks.__main__ as cli

def test_definedness_cli(monkeypatch, capsys):
    monkeypatch.setattr(cli, "run_pyright", lambda path: [])  # pyright silent = defined
    rc = cli.main(["definedness", "--finding-id", "ENV-1", "--file", "sizer.py", "--line", "12"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["verdict"] == "FALSE_POSITIVE" and out["finding_id"] == "ENV-1"

def test_repro_cli(capsys):
    rc = cli.main(["repro", "--finding-id", "BUG-1",
                   "--test", "tests/fixtures/raises_test.py", "--expect-error", "ZeroDivisionError"])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0 and out["verdict"] == "CONFIRMED"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_cli.py -v`
Expected: FAIL — `AttributeError`/`ModuleNotFoundError` (no `__main__`).

- [ ] **Step 3: Write minimal implementation**

`cca_checks/__main__.py`:
```python
import argparse
import json
import sys
from dataclasses import asdict
from .claim import Claim
from .pyright_check import run_pyright, verdict_for_definedness
from .repro_runner import run_repro

def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    p = argparse.ArgumentParser(prog="cca_checks")
    sub = p.add_subparsers(dest="cmd", required=True)
    d = sub.add_parser("definedness")
    d.add_argument("--finding-id", required=True)
    d.add_argument("--file", required=True)
    d.add_argument("--line", type=int, required=True)
    d.add_argument("--symbol", default="")
    r = sub.add_parser("repro")
    r.add_argument("--finding-id", required=True)
    r.add_argument("--test", required=True)
    r.add_argument("--expect-error", default=None)
    a = p.parse_args(argv)
    if a.cmd == "definedness":
        claim = Claim(a.finding_id, a.file, a.line, "definedness", a.symbol)
        v = verdict_for_definedness(claim, run_pyright(a.file))
    else:
        v = run_repro(a.finding_id, a.test, a.expect_error)
    print(json.dumps(asdict(v)))
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_cli.py -v`
Expected: PASS (2 passed).

- [ ] **Step 5: Full suite green + commit**

Run: `python -m pytest -q`  (Expected: all tasks' tests pass.)
```bash
git add cca_checks/__main__.py tests/test_cli.py
git commit -m "feat(cca_checks): CLI (definedness|repro) emitting JSON verdicts"
```

---

### Task 5: Rewrite the `fp-check` agent protocol (deterministic-first)

**Files:**
- Modify: `claude-code/agents/cca-fp-check.md`

**Interfaces:**
- Consumes: `python -m cca_checks definedness|repro` (Tasks 2–4).
- Produces: the Layer-2.5 verdict table, now with an **Evidence** column. No pytest test — validated by Task 6's acceptance harness and a manual `/audit-fix` run.

- [ ] **Step 1: Add the two-phase protocol section**

In `claude-code/agents/cca-fp-check.md`, add a section titled `## Deterministic-first verification (v3.0-min)` containing exactly:

```markdown
For each P1/P2 finding, run TWO phases in order:

**Phase 1 — mechanical (preferred).** Classify the finding into a claim_type, then:

- `definedness` / undefined symbol / missing import / config-key-undefined →
  `python -m cca_checks definedness --finding-id <ID> --file <path> --line <N> --symbol <name>`
- `crash_impact` (crash / wrong value with a concrete input) → FIRST write a minimal repro test
  `t_<ID>.py` that drives the code through its **public entry point** (respect validators — never
  call the raw internal function), predicting the impact, THEN:
  `python -m cca_checks repro --finding-id <ID> --test t_<ID>.py --expect-error <ErrorType>`

Use the tool's JSON `{verdict, evidence, source}` verbatim. Delete the temp repro test after.

**Phase 2 — semantic (residue only).** If claim_type is `semantic`, or the required tool is missing
(command not found / non-Python), adjudicate with a fresh judgment that MUST cite the specific facts
you gathered (guard location, caller list, resolved symbol) — never re-read the finding text alone.
Mark `source: llm` and `evidence:` = the cited facts.

**Verdict rule:** a `CONFIRMED`/`FALSE_POSITIVE` verdict MUST carry non-empty `evidence`; otherwise
emit `UNCERTAIN` and escalate. Never silently drop a `crash_impact` you couldn't reproduce — that is
`UNCERTAIN`, not `FALSE_POSITIVE`.
```

- [ ] **Step 2: Add the Evidence column to the output table**

In the same file's Output Format, change the verdict table header to:
```markdown
| ID | Verdict | Source | Evidence |
```
and require every row to fill `Evidence` (tool artifact or cited facts).

- [ ] **Step 3: Sanity-check the file renders and references are consistent**

Run: `grep -n "cca_checks" claude-code/agents/cca-fp-check.md`
Expected: the two invocation lines present, spelled exactly `python -m cca_checks`.

- [ ] **Step 4: Commit**

```bash
git add claude-code/agents/cca-fp-check.md
git commit -m "feat(fp-check): deterministic-first protocol (pyright/pytest evidence, LLM residue)"
```

---

### Task 6: Trap-suite fixture, acceptance test, docs

**Files:**
- Create: `examples/bps-sizing/definedness_trap/settings.py`, `examples/bps-sizing/definedness_trap/service.py`
- Create: `tests/acceptance/test_trap_suite.py`
- Modify: `docs/v3-design.md`

**Interfaces:**
- Consumes: `run_pyright`, `verdict_for_definedness`, `run_repro`.

- [ ] **Step 1: Add the clean `definedness` fixture** (symbol defined off-diff, looks undefined in the changed file)

`examples/bps-sizing/definedness_trap/settings.py`:
```python
RISK_CAP_USD = 50_000.0  # pre-existing, off-diff
```
`examples/bps-sizing/definedness_trap/service.py`:
```python
from settings import RISK_CAP_USD  # the "PR" line an auditor might flag as undefined

def cap(x: float) -> float:
    return min(x, RISK_CAP_USD)
```

- [ ] **Step 2: Write the acceptance test** (skips if tools absent, so CI stays green without them)

`tests/acceptance/test_trap_suite.py`:
```python
import shutil
import pytest
from cca_checks.claim import Claim
from cca_checks.pyright_check import run_pyright, verdict_for_definedness
from cca_checks.repro_runner import run_repro

pyright_missing = shutil.which("pyright") is None

@pytest.mark.skipif(pyright_missing, reason="pyright not installed")
def test_defined_symbol_is_dropped_by_pyright():
    path = "examples/bps-sizing/definedness_trap/service.py"
    claim = Claim("ENV-1", path, 1, "definedness", "RISK_CAP_USD undefined")
    v = verdict_for_definedness(claim, run_pyright(path))
    assert v.verdict == "FALSE_POSITIVE" and "pyright" in v.source

def test_guarded_div_by_zero_is_escalated_not_refuted():
    # a repro that passes (guard holds) must yield UNCERTAIN, never FALSE_POSITIVE
    v = run_repro("BUG-1", "tests/fixtures/passes_test.py", "ZeroDivisionError")
    assert v.verdict == "UNCERTAIN"
```

- [ ] **Step 3: Run the acceptance test**

Run: `python -m pytest tests/acceptance/test_trap_suite.py -v`
Expected: `test_guarded_div_by_zero...` PASS; the pyright test PASS if pyright installed, else SKIPPED.

- [ ] **Step 4: Flip the design-doc status**

In `docs/v3-design.md`, change the roadmap line for `v3.0-min` to note it is **implemented** (checkers + fp-check protocol + acceptance) and link this plan.

- [ ] **Step 5: Commit**

```bash
git add examples/bps-sizing/definedness_trap/ tests/acceptance/test_trap_suite.py docs/v3-design.md
git commit -m "test(v3): trap-suite acceptance (definedness dropped, guarded crash escalated) + docs"
```

---

### Task 7: Manual end-to-end eval (not automated)

**Files:** none (validation only).

- [ ] **Step 1:** Install tools: `pip install pyright pytest pydantic`.
- [ ] **Step 2:** From the repo root, run the full deterministic suite: `python -m pytest -q` — expect all green (pyright acceptance may skip if not installed).
- [ ] **Step 3:** In an interactive Claude Code session with cca-audit installed, run `/audit-fix commit 1` on the `bps-sizing` demo and confirm the L2.5 table now has an **Evidence** column, the config/definedness finding cites `pyright`, and the bps bug cites a `pytest` artifact. This is the human eval gate — the LLM-orchestration layer is non-deterministic, so it is checked by observation, not a unit test.
