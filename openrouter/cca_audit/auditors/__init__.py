"""Auditor registry."""

from cca_audit.auditors.base import BaseAuditor
from cca_audit.auditors.bug import BugAuditor
from cca_audit.auditors.code import CodeAuditor
from cca_audit.auditors.dep import DepAuditor
from cca_audit.auditors.doc import DocAuditor
from cca_audit.auditors.env import EnvAuditor
from cca_audit.auditors.perf import PerfAuditor
from cca_audit.auditors.security import SecurityAuditor

AUDITOR_REGISTRY: dict[str, type[BaseAuditor]] = {
    "code": CodeAuditor,
    "bug": BugAuditor,
    "security": SecurityAuditor,
    "perf": PerfAuditor,
    "doc": DocAuditor,
    "env": EnvAuditor,
    "dep": DepAuditor,
}

__all__ = [
    "AUDITOR_REGISTRY",
    "BaseAuditor",
    "BugAuditor",
    "CodeAuditor",
    "DepAuditor",
    "DocAuditor",
    "EnvAuditor",
    "PerfAuditor",
    "SecurityAuditor",
]
