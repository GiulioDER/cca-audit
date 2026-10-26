# Remote receipt regression evidence

The 2026-10-26 operator proof run used offline, bounded containers. Every
behavior test below failed at its intended assertion against the named baseline
or production mutation, then passed after restoring the source. Collection,
dependency and fixture failures were rejected as proof. Raw output and operator
paths remain in the private validation archive.

| Test node in `tests/` | Production target | Baseline or mutation |
| --- | --- | --- |
| `test_qwen_tests.py::test_signature_and_expected_identity_are_required` | `verify` | Bypass signature verification |
| `test_qwen_tests.py::test_proof_requires_same_test_and_behavioral_red` | `proof` | Treat an inconclusive assertion result as RED |
| `test_qwen_tests.py::test_prepare_is_deterministic_binds_bytes_and_excludes_private_files` | `prepare` | Replace snapshot digest with a constant |
| `test_qwen_tests.py::test_current_tree_must_still_match_receipt_request` | `_matches_live` | Bypass current snapshot comparison |
| `test_qwen_tests.py::test_audited_repository_cannot_choose_endpoint_or_verification_key` | `load_operator_config` | Bypass repository configuration exclusion |
| `test_qwen_tests.py::test_missing_verifier_does_not_become_a_pass` | `verify` | Convert missing cryptography into a pass |
| `test_qwen_snapshot_safety.py::test_prepare_never_reads_outside_file_during_path_replacement[file]` | `prepare` | Pre-fix path-based file opening |
| `test_qwen_snapshot_safety.py::test_prepare_never_reads_outside_file_during_path_replacement[ancestor]` | `prepare` | Pre-fix path-based ancestor traversal |
| `test_qwen_snapshot_safety.py::test_windows_snapshot_reader_rejects_directory_junction` | `_windows_snapshot_reader` | Ignore the reparse-point attribute |
| `test_qwen_audit_repairs.py::test_enumeration_refuses_before_unbounded_archive_work` | `_git_names` / `prepare` | Pre-fix materialization and deduplication |
| `test_qwen_audit_repairs.py::test_enumeration_refuses_before_unbounded_archive_work[timeout]` | `_git_names` deadline | Mistaken seconds-to-milliseconds multiplier |
| `test_qwen_audit_repairs.py::test_proof_protects_all_signed_test_roots` | `proof` | Pre-fix protection of only `tests/` and the selected file |
| `test_qwen_audit_repairs.py::test_installed_workflows_include_the_remote_test_guide` | `iter_codex_skill` | Pre-fix guide projection |
| `test_qwen_audit_repairs.py::test_mcp_nonobject_responses_fail_closed` | `Client.call` | Pre-fix access to fields of a null or list result |

The Windows junction control ran natively with a disposable fixture; Linux
cannot exercise Windows handle semantics. Linux file and ancestor replacement
controls ran on VPS2. Fixed source bytes were checked after restoration.
`test_patched_crypto_floor_is_declared` is a nonbehavioral manifest control and
claims no behavioral red proof. Signed worker receipts authenticate provenance;
these controls do not turn a repository's test code into a trusted oracle.

Additional final review controls, measured 2026-10-26:

| Test node in `tests/test_qwen_snapshot_safety.py` | Production target | Red baseline |
| --- | --- | --- |
| `test_prepare_does_not_execute_repository_fsmonitor_hook` | `_git` and `_git_names` | Git invoked with repository fsmonitor configuration; recreated the harmless hook sentinel |
| `test_windows_snapshot_reader_rejects_inplace_reparse_mutation` | `_windows_snapshot_reader` | Checked directories permit write sharing; returned outside sentinel bytes |

Both exact nodes failed their intended assertions before repair and passed afterward on native Windows. The junction and deterministic snapshot controls also passed. The native tests require no network or credentials.

`test_prepare_does_not_execute_repository_git_binary` was measured natively on 2026-10-26. A harmless copied Windows executable shadowed bare Git and failed the intended assertion before repair. Both Git call sites now use the existing trusted-tool resolver and refuse an untrusted resolution.

`test_prepare_excludes_windows_private_path_aliases` was measured natively on 2026-10-26. Harmless trailing-dot file and directory aliases bypassed private-path exclusions and failed the intended consumer assertion before repair. Every component ending in a dot is now rejected by the portable snapshot-name policy. The same control runs on POSIX with literal dotted names.
