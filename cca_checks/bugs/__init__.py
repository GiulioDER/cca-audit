"""Cross-project ledger of the bugs CCA fixed.

Spec: docs/superpowers/specs/2026-10-11-cca-bug-ledger-design.md. A bug counts as
`proved` only when four conditions are on record (confirmed at L2.5, fix landed,
approved at L6, red at L5.6); everything else is `historical`, with each condition
marked for what can and cannot be shown.
"""
