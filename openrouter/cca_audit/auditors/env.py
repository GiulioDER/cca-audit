"""Environment configuration validator."""

from cca_audit.auditors.base import BaseAuditor


class EnvAuditor(BaseAuditor):
    name = "env"
    prefix = "ENV"
    output_file = "AUDIT_ENV.md"

    def template_name(self) -> str:
        return "env.j2"
