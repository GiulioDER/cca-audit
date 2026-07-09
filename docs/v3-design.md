# CCA-Audit v3 — Isolation & Deterministic Verification (Design)

**Status:** Design of record. First build slice = **v3.0-min (Python)**.
**Motivation:** community feedback on the launch writeup converged on one theme — the verification gate is only as strong as its *independence* from the thing it's verifying.

---

## 1. The problem: the verifier shares the generator's prior

CCA's anti-hallucination gate (`fp-check`, Layer 2.5) re-checks every finding before anything is fixed, biased toward refuting. The sharpest critique of this design is correct:

> If the same model that emitted the finding also runs verification, it shares the prior that produced the false positive — so re-asking tends to confirm its own hallucination. Refutation bias helps only if the verifier gets evidence the generator didn't have.

Where CCA is **today**: the verifier is a separate agent with a fresh context that re-derives findings from *source* (reads the whole file + call graph, not the diff hunk). That genuinely differs from "same context, colder prompt" — but it is **still an LLM**, so it still shares the training prior. Fresh context *reduces* self-confirmation; it does not eliminate it.

## 2. Principle: a verdict must rest on evidence the generator didn't have

Independence has tiers:

| Tier | Verifier | Independence |
|------|----------|--------------|
| 0 | Same context, sterner prompt | none — shares everything |
| 1 | **CCA today** — fresh-context LLM, re-reads source | partial — shares the training prior |
| 2 | **v3 target** — deterministic tools carry the verdict; LLM adjudicates only the residue | strong — evidence the generator never had |
| 3 | Formal methods / proof (aspirational) | total, for the subset that admits it |

**"Isolation is the key" means: move the burden of proof from "another LLM couldn't refute it" to "a mechanical check settled it."** v3 is the Tier 1 → Tier 2 move.

## 3. Architecture (full v3)

Every finding carries an implicit, machine-checkable claim. v3 turns findings into claims, settles each with a real tool where one exists, and demotes the LLM to adjudicating only what no tool can settle.

```
auditor (semantic suspicion)
   → structured CLAIM
      → deterministic CHECK (mechanical proof, via a real tool)
         → [settled]  → verdict + evidence artifact
         → [no tool]  → LLM ADJUDICATOR (residue only; must cite gathered facts)
            → verdict + cited facts
```

### 3.1 Claim schema

Findings stop being prose. Each auditor finding emits:

```
{ id, file, line, claim_type, proposition, predicted_impact, suggested_check }
```

`claim_type ∈ { definedness, type, nullability, reachability, crash_impact, taint, semantic }`

### 3.2 claim_type → checker

| claim_type | The claim | Deterministic checker |
|------------|-----------|-----------------------|
| `definedness` | "symbol / config key / import X is not defined on any path here" | symbol/import resolution (Python: `pyright` undefined-name, or AST symbol table) |
| `type` | "the types don't hold" | type checker (`pyright` / `mypy`) run on the hypothesis |
| `nullability` | "Optional value accessed without a guard" | `pyright` optional-access diagnostics |
| `reachability` | "line N is reachable with value V" (e.g. div-by-zero) | CFG/dominator reasoning; in v3.0-min, subsumed by the repro check |
| `crash_impact` | "input I triggers the predicted impact" | **generated `pytest` repro** through the real entry point |
| `taint` | "untrusted source reaches sink" | static taint (`semgrep` / CodeQL-style) — later slice |
| `semantic` | domain/business-logic judgment | **no tool** → LLM adjudicator with cited facts |

### 3.3 Verdict rule

- `CONFIRMED` **requires an evidence artifact** — a tool result *or*, for `semantic` claims, explicitly cited facts.
- No artifact → default **`UNCERTAIN`** (escalate to human). The burden of proof is literal.

---

## 4. v3.0-min — the first build slice (Python)

**Approach A (chosen): upgrade `fp-check` in place**, with its protocol internally split into a *mechanical* phase and a *semantic* phase, so lifting it to a separate deterministic layer later (Approach B) is a refactor, not a rewrite.

> **Scope reducer:** in v3.0-min, `fp-check` **infers** each finding's `claim_type` itself — the auditors are unchanged. The formal auditor-emitted claim schema (§3.1) is a v3.1 enhancement. This keeps the min slice to essentially one changed file (`fp-check`) plus the two tool adapters.

### 4.1 First checkers (the two highest-value for Python)

1. **`pyright` adapter** — one tool, broad coverage: undefined names/imports (kills the `definedness` / config-trap class), type errors, and Optional-access nullability. Run `pyright --outputjson` on the changed file(s), parse diagnostics, match to the claim's `file:line` + `claim_type`.
   - Claim asserts a defect **and** pyright reports it at that location → **CONFIRMED** (artifact = the diagnostic).
   - Claim asserts a defect **and** pyright is clean there → **FALSE_POSITIVE** (artifact = "pyright: no diagnostic at file:line").
2. **`pytest` repro-generator** — for `crash_impact` claims: synthesize a minimal test that drives the code **through its real public entry point** (so validators/guards are respected) with inputs predicted to trigger the impact, and run it.
   - Repro fails with the predicted error → **CONFIRMED** (artifact = the failing test + traceback).
   - No triggering input constructible through the validated boundary (in-budget) → **UNCERTAIN/escalate** — *never silently refute* (avoids trading false positives for false negatives on a money path).

> **Key design point:** the repro must go through the *validated entry point*, not the raw internal function. Bypassing the guard would "reproduce" a crash that can't happen in practice (a false confirm). Respecting the boundary is exactly how repro-gen drops a guarded div-by-zero: it cannot construct a validated input that triggers it.

### 4.2 Components (well-bounded units)

| Unit | Responsibility | Depends on |
|------|----------------|------------|
| **Claim classifier** (in `fp-check`) | infer `claim_type` for each finding — *no auditor change in v3.0-min* | consolidated findings |
| **Checker router** (in `fp-check`) | map `claim_type` → checker; else → adjudicator | claim schema |
| **`pyright` adapter** | run pyright, parse JSON, verdict + artifact for a claim | `pyright` on PATH |
| **`pytest` repro adapter** | write/run a boundary-respecting repro, verdict + artifact | `pytest`, callable entry point |
| **LLM adjudicator** | residue only; verdict + **cited** facts | fresh agent context |
| **Verdict assembler** | enforce "artifact-or-UNCERTAIN"; emit the L2.5 table | all of the above |

### 4.3 Data flow

Consolidated P1/P2 findings → `fp-check` classifies each into a `claim_type` → router → per-claim: run tool → collect {verdict, artifact} → residue to adjudicator → assembler applies the verdict rule → the existing L2.5 verdict table, now with an **evidence column** citing the tool/artifact per verdict.

### 4.4 Graceful fallback (strict superset of v2)

If a tool is missing (`pyright`/`pytest` not installed), the language is unsupported, or a claim has no checker → **fall back to today's v2 LLM-adjudication** for that claim, flagged as `LLM-adjudicated` in the evidence column. **v3.0-min never regresses v2 — it only adds mechanical proof where a tool exists.**

## 5. Testing / acceptance

Promote the `bps-sizing` demo (real 100× bug + 3 planted traps) into a **regression fixture**. v3.0-min passes iff:

- [ ] The bps units bug is **CONFIRMED with a deterministic artifact** (a failing property/repro test showing wrong magnitude), not just an LLM opinion.
- [ ] **Config trap** (`definedness`) is **dropped by `pyright`** resolving the key as evidence — no LLM judgment needed.
- [ ] **Div-by-zero traps** (both) are dropped because repro-gen cannot trigger them through the validated boundary.
- [ ] Every verdict in the L2.5 table has a non-empty **evidence** cell (tool artifact or cited facts); any bare verdict fails the gate.
- [ ] With `pyright`/`pytest` absent, the pipeline still completes via v2 fallback (no regression).

> **Fixture note:** the current trap-3 is a `.get()`-brittleness flavor, not an undefined symbol. Extend the fixture with a clean `definedness` case — a symbol defined *off-diff* that looks undefined in the changed file — to exercise the `pyright` resolution path directly.

## 6. Non-goals & honest limits

- **Not** a from-scratch static-analysis engine — v3 orchestrates existing tools via shell.
- **Not** multi-language in this slice — Python only; other languages ride the v2 fallback until adapters land.
- **`semantic` claims stay LLM-adjudicated.** v3 shrinks the residue that depends on LLM judgment to the claims no tool can settle, and forces even those to cite facts. It does not make "is this the *right* business logic" deterministic — that ceiling is real and disclosed.
- Repro-gen is **confirmatory, not refuting**: "couldn't reproduce" escalates, it does not drop.

## 7. Roadmap

- **v3.0-min (this slice):** claim schema + `fp-check` router + `pyright` adapter + `pytest` repro-gen, Python, with v2 fallback. Acceptance = the trap suite above.
- **v3.1:** `taint` via `semgrep`; nullability/reachability via a proper CFG check; more claim types.
- **v3.x:** language adapters (TS via `tsc`/LSP, Go via `go vet`/type info, Rust via `cargo check`).
- **Architecture B:** once ≥3 checker classes exist, split the mechanical checks into their own Layer 2.4 (independently testable) ahead of the L2.5 adjudicator.
