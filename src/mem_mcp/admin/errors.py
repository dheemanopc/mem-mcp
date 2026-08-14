"""Admin service error hierarchy.

Transport-agnostic on purpose: the web layer maps these to HTTPException,
the MCP layer maps them to JsonRpcError. The service itself raises neither.
"""

from __future__ import annotations


class AdminError(Exception):
    """Base for all admin service failures."""


class NotFoundError(AdminError):
    """Requested entity does not exist."""


class ConflictError(AdminError):
    """Operation conflicts with current state (already applied, self-target, ...)."""
