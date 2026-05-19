"""Dependency health auditor."""

from cca_audit.auditors.base import BaseAuditor


class DepAuditor(BaseAuditor):
    name = "dep"
    prefix = "DEP"
    output_file = "AUDIT_DEPS.md"

    def template_name(self) -> str:
        return "dep.j2"
