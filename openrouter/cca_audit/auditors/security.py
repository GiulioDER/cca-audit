"""Security auditor — single authority for all security checks."""

from cca_audit.auditors.base import BaseAuditor


class SecurityAuditor(BaseAuditor):
    name = "security"
    prefix = "SEC"
    output_file = "AUDIT_SECURITY.md"

    def template_name(self) -> str:
        return "security.j2"
