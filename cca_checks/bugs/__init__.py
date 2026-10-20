"""Cross-project ledger of the bugs CCA fixed.

Spec: docs/superpowers/specs/2026-10-11-cca-bug-ledger-design.md. A bug counts as
`proved` only when four conditions are on record (confirmed at L2.5, fix landed,
approved at L6, and a red proof). A P1 fix needs a RED proof at L5.6; a P2 or P3 fix
with no proof entry counts as not required. Everything else is `historical`, with each
condition marked for what can and cannot be shown.
"""
