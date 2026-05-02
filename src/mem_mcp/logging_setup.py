"""Structured JSON logging for mem-mcp.

Single-process invariant: ``setup_logging()`` is called once at startup
(see ``mem_mcp.main`` lifespan, T-3.5). Subsequent calls are no-ops.

Per spec §14.5:
  - JSON lines on stdout (systemd → journald → CloudWatch agent)
  - Redact filter scrubs known sensitive keys (per GUIDELINES §6.2)
  - Request context bound via structlog.contextvars

Public API:
    setup_logging(level="INFO")          — once at startup
    get_logger(name=None)                — returns a structlog BoundLogger
    bind_request_context(**kwargs)       — bind per-request fields
    clear_request_context()              — clear at request end (not strictly
                                            needed if uvicorn handles ctxvars)
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Mapping
from typing import Any

import structlog
from structlog.types import EventDict, Processor

# Keys whose values must NEVER appear in logs (redacted to fixed sentinel).
# Substring match (case-insensitive) — covers nested dicts and odd casings.
_REDACT_SUBSTRINGS: tuple[str, ...] = (
    "password",
    "secret",
    "token",
    "passphrase",
    "authorization",
    "cookie",
    "session",  # mem_session, web_session_secret, etc.
    "private_key",
    "client_secret",
    "refresh_token",
    "access_token",
    "id_token",
    "x-amz-security",
    "credential",
)

_REDACTED_VALUE = "<redacted>"

# Single-shot guard so setup_logging() is idempotent
_INITIALIZED = False


def _redact(value: Any) -> Any:
    """Recursively redact values for keys matching sensitive substrings.

    Mappings: redact each matching value.
    Sequences (list/tuple): recurse element-wise.
    Other types: return as-is.
    """
    if isinstance(value, Mapping):
        return {
            k: (_REDACTED_VALUE if _is_sensitive_key(str(k)) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list | tuple):
        return type(value)(_redact(v) for v in value)
    return value


def _is_sensitive_key(key: str) -> bool:
    lk = key.lower()
    return any(s in lk for s in _REDACT_SUBSTRINGS)


def _redact_processor(_logger: object, _method: str, event_dict: EventDict) -> EventDict:
    """structlog processor that scrubs sensitive keys at the top level + nested."""
    # Mutate in place — top-level keys
    for k in list(event_dict.keys()):
        if _is_sensitive_key(k):
            event_dict[k] = _REDACTED_VALUE
        elif isinstance(event_dict[k], Mapping | list | tuple):
            event_dict[k] = _redact(event_dict[k])
    return event_dict


def _add_log_level_to_event(_logger: object, method_name: str, event_dict: EventDict) -> EventDict:
    """Ensure 'level' field is present (structlog's add_log_level does this for us)."""
    event_dict.setdefault("level", method_name)
    return event_dict


def setup_logging(level: str = "INFO") -> None:
    """Configure structlog → JSON stdout. Idempotent.

    Call once at process startup (e.g. FastAPI lifespan). Stdlib logging is
    also configured so 3rd-party libs (uvicorn, asyncpg, boto3) flow through
    the same JSON pipeline.
    """
    global _INITIALIZED
    if _INITIALIZED:
        return

    # Configure stdlib logging to feed structlog
    log_level_int = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=log_level_int,
    )

    processors: list[Processor] = [
        structlog.contextvars.merge_contextvars,  # request_id, tenant_id, ...
        structlog.processors.add_log_level,  # 'level' field
        structlog.processors.TimeStamper(fmt="iso", utc=True),  # 'timestamp' ISO 8601 UTC
        _redact_processor,  # scrub sensitive keys
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,  # exc_info → traceback string
        structlog.processors.JSONRenderer(),  # final: dict → JSON string
    ]

    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level_int),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )

    _INITIALIZED = True


def get_logger(name: str | None = None) -> structlog.stdlib.BoundLogger:
    """Return a bound logger. ``name`` becomes the 'logger' field if provided."""
    if name:
        return structlog.get_logger(name)  # type: ignore[no-any-return]
    return structlog.get_logger()  # type: ignore[no-any-return]


def bind_request_context(**kwargs: object) -> None:
    """Bind per-request fields (request_id, tenant_id, client_id, ...) via contextvars.

    Survives across await boundaries within the same task. Call from the
    Bearer middleware (T-4.3).
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_request_context() -> None:
    """Clear all bound contextvars. Call at request end."""
    structlog.contextvars.clear_contextvars()


def _reset_for_tests() -> None:
    """Test-only: reset the idempotency guard so setup_logging can be re-called."""
    global _INITIALIZED
    _INITIALIZED = False
    structlog.reset_defaults()
    structlog.contextvars.clear_contextvars()
