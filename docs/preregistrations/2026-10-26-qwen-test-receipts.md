# Pre-registration: Qwen test receipts in CCA

Date: 2026-10-26. Status: predicted, not yet measured.

## Question and prediction

Can CCA consume a signed remote test receipt only for its exact run, source
snapshot and selectors, preserving failed, unavailable, empty and unproven
results? I predict every altered signature, mismatched identity and invalid
proof pair will be refused. A valid passing receipt will support L5; only
the same assertion failing on a different baseline snapshot and passing on
the fixed snapshot will support a remote red-state proof.

## Falsification and method

Acceptance is falsified by a mismatched or inconclusive receipt becoming a
passing gate. Run tests/test_qwen_tests.py serially in a memory-capped remote
container. Each new behavior test must first fail at its intended assertion
under a plausible production mutation, then pass after restoration. Record
exact node IDs and mutations. Exercise the real worker and MCP transport
separately; mocked signatures or transport do not establish live readiness.

## Confounds

Worker signatures establish provenance, not the semantic honesty of repository
tests. Collection errors, setup failures, timeouts, all-skipped runs and zero
collected tests must not become red proof. Missing cryptography support or an
unpinned worker public key must fail closed. Existing local proof behavior
remains available and must be regression checked.
