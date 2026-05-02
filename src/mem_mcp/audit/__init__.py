"""Audit logging — INSERT into audit_log per spec §5.5.

Public API:
    AuditAction       — Literal type of canonical action strings
    AuditResult       — Literal 'success' | 'denied' | 'error'
    AuditLogger       — Protocol with audit() method
    DbAuditLogger     — production impl (INSERT on the caller's connection)
    NoopAuditLogger   — drops everything (for tests / unwired environments)
"""

from mem_mcp.audit.logger import (
    AUDIT_ACTIONS,
    AuditAction,
    AuditLogger,
    AuditResult,
    DbAuditLogger,
    NoopAuditLogger,
)

__all__ = [
    "AUDIT_ACTIONS",
    "AuditAction",
    "AuditLogger",
    "AuditResult",
    "DbAuditLogger",
    "NoopAuditLogger",
]
