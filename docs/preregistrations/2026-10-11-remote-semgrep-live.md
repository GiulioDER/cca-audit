# Pre-registration: do the semgrep tests that cannot pass on this workstation pass through the remote?

**Date:** 2026-10-11   **Status:** predicted, not yet measured

## The question

With `CCA_REMOTE_SEMGREP=cca-analyzer` and `CCA_TESTS_USE_REMOTE=1` on the author's Windows
workstation (where semgrep is blocked by Smart App Control), how many of the 11 tests that fail
there without the remote pass?

## What I predict

**11 of 11 pass.** Each reaches semgrep through `semgrep_check.verdict_for_taint` or
`semgrep_check.run_semgrep`, which go remote when the local probe fails; none calls the binary
another way. (The plan expected some to call the binary directly; reading them showed none does.)
None skips, because each is guarded by `shutil.which("semgrep")` and the blocked binary is still on
PATH.

The 11, from the full suite run at `42ac952`:

* `tests/test_selfaudit_hardening.py::test_benign_code_is_still_refuted[code_exec|path|command|sql]` (4)
* `tests/test_rust_taint.py::test_semgrep_really_parses_rust`
* `tests/test_rust_taint.py::test_end_to_end_verdicts[19-command-...]`, `[19-sql-...]` (2)
* `tests/acceptance/test_taint_suite.py::test_unlisted_driver_escalates_never_refutes`,
  `test_concatenated_sql_is_escalated_with_the_taint_match_never_refuted`,
  `test_parameterized_sql_is_neither_blessed_nor_refuted`,
  `test_orm_with_no_raw_sql_sink_is_refuted_with_an_artifact` (4)

## What would falsify this

Any of the 11 failing. A failure that names the remote (an UNCERTAIN whose evidence says the
remote scan failed) falsifies the transport; a wrong verdict with `source: semgrep` would mean the
remote semgrep (1.178.0) disagrees with the version CI installs, which is a different finding.

## How it will be measured

```bash
CCA_REMOTE_SEMGREP=cca-analyzer CCA_TESTS_USE_REMOTE=1 python -m pytest <the 11 node ids> -q
```

on branch `feat/remote-semgrep`, host set up per `docs/remote-semgrep.md` sections 2, 2b and 3.
Metric: passed out of 11.

## What I already know

* Apparatus checks, same day, before this record: the key reaches only the runner (`ssh -n
  cca-analyzer id` returns the runner's refusal); remote port forwarding is refused;
  `capabilities` reports `"remote": {"taint": "semgrep 1.178.0 on cca-analyzer"}` through the
  capped launch; one taint check on `tests/fixtures/taint/sql_concat.py` returned the expected
  UNCERTAIN with both rule matches and the host named.
* Not yet run: the root-only isolation checks of `docs/remote-semgrep.md` section 5.

## Confounds I can name now

* semgrep 1.178.0 on the host against whatever newer version CI installs: a rule behaving
  differently between versions would fail a test for a reason that is not the transport.
* The remote adds a few seconds per call; a test with a tight time bound could fail on latency.
