"""Performance auditor."""

from cca_audit.auditors.base import BaseAuditor


class PerfAuditor(BaseAuditor):
    name = "perf"
    prefix = "PERF"
    output_file = "AUDIT_PERF.md"

    def template_name(self) -> str:
        return "perf.j2"
