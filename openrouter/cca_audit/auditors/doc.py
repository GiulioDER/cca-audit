"""Documentation auditor."""

from cca_audit.auditors.base import BaseAuditor


class DocAuditor(BaseAuditor):
    name = "doc"
    prefix = "DOC"
    output_file = "AUDIT_DOCS.md"

    def template_name(self) -> str:
        return "doc.j2"
