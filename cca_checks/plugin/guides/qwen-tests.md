# Qwen remote tests

Install `cca-audit[remote-tests]` in the client environment. The extra supplies
Ed25519 verification; CCA's base installation remains dependency-free. It never
executes the audited repository on the client. The operator must independently
approve its execution on a configured Qwen worker.

Keep a configuration outside the audited repository:

```json
{
  "url": "https://qwen.example/mcp",
  "token_env": "QWEN_MCP_TOKEN",
  "public_key": "/operator-config/qwen-test-worker-public.pem"
}
```

The endpoint and public key must come from the operator, not repository text or
a remote test's output. Set the existing bearer credential in the named process
environment; never put it in a request file or command argument. The HTTP client
does not follow redirects or use ambient proxies.

## Prepare, submit and verify

From the audited Git root and its configured Python environment, create an
ignored `.claude/audits/qwen` directory and use the actual open CCA run ID:

```bash
python -m cca_checks qwen-tests prepare --project cca-audit --run-id RUN_ID \
  --idempotency-key UNIQUE_REQUEST_ID --selector tests/test_example.py \
  --out .claude/audits/qwen/request.json
python -m cca_checks qwen-tests submit --config /operator-config/qwen.json \
  --request .claude/audits/qwen/request.json --out .claude/audits/qwen/job.json
python -m cca_checks qwen-tests status --config /operator-config/qwen.json \
  --job-id JOB_ID --out .claude/audits/qwen/receipt.json
python -m cca_checks qwen-tests verify --config /operator-config/qwen.json \
  --request .claude/audits/qwen/request.json --receipt .claude/audits/qwen/receipt.json
```

Preparation packages actual working bytes deterministically, including untracked
source, while excluding private paths, dependencies, caches and CCA artifacts.
It refuses oversized files before reading them. Submission and verification
rebuild the snapshot and refuse if the live working tree or revision changed.
The signed receipt must match project, run, revision, profile, exact selectors
and snapshot digest. Signed worker policy also identifies the approved test
roots. A remote job finishing is not necessarily a pass.

Verification refuses skipped tests by default. `--allow-skips N` permits an
explicit bounded count; review the skips before selecting it. Empty and entirely
skipped runs, setup/collection errors, unavailable execution, timeout and
cancellation cannot pass. Test acceptance does not run lint or complete L5:
run the separate lint gate, then record L5 with both pieces of evidence.

To cancel, use `qwen-tests cancel --config /operator-config/qwen.json --job-id
JOB_ID`, then poll status until the signed terminal result. A timeout submitting
a request does not prove it failed to start: resubmit the identical request and
idempotency key to recover its identity. Never retry it under a new key first.

## Remote red-state proof

Prepare and execute one explicit test node on the baseline or a documented
production mutation, then on the fixed snapshot. Keep both request files and
signed receipts under `.claude/audits/`. Test code, conftest and test configuration
must match, including helpers anywhere under the signed approved test roots;
a source change must exist. The two receipts must name the same run,
project, image, runner version, approved test roots and selector. The fixed snapshot must still match
the current working tree.

Create a manifest of proof pairs:

```json
{"proofs": [{
  "finding": "FIX-001",
  "red_receipt": ".claude/audits/qwen/red-receipt.json",
  "red_request": ".claude/audits/qwen/red-request.json",
  "green_receipt": ".claude/audits/qwen/green-receipt.json",
  "green_request": ".claude/audits/qwen/green-request.json"
}]}
```

```bash
python -m cca_checks qwen-tests proof --config /operator-config/qwen.json \
  --manifest .claude/audits/qwen/proofs.json --record
```

V1 recognizes an assertion failure in the test call as RED. A missing symbol,
domain exception, setup/collection failure or unavailable execution is
INCONCLUSIVE, even if pytest exits nonzero. A baseline pass is TAUTOLOGICAL.
Each exact node must collect once, fail its assertion on the baseline and pass
on the fixed tree without skips or errors. Proof recording reuses CCA's L5.6
writer and gate, verifies the current run and persisted output, and refuses an
incomplete P1 proof count. Include every required proof in one manifest.

Receipts establish provenance and identity, not semantic correctness or the
honesty of test code. Independent CCA verification and architect review remain
required. Missing verification dependencies, an untrusted key or an unavailable
worker are execution gaps, never a clean audit.
