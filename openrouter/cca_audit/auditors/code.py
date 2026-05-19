"""Code quality auditor."""

from cca_audit.auditors.base import BaseAuditor


class CodeAuditor(BaseAuditor):
    name = "code"
    prefix = "CODE"
    output_file = "AUDIT_CODE.md"

    def template_name(self) -> str:
        return "code.j2"
