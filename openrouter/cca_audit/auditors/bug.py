"""Runtime bug auditor."""

from cca_audit.auditors.base import BaseAuditor


class BugAuditor(BaseAuditor):
    name = "bug"
    prefix = "BUG"
    output_file = "AUDIT_BUGS.md"

    def template_name(self) -> str:
        return "bug.j2"
