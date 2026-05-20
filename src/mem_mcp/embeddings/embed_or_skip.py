"""Helper to embed content or gracefully skip on degraded conditions.

Handles:
- Caller opt-out via indexable=False
- Content too large for Bedrock (oversize)
- Bedrock throttle/unavailable (graceful degrade, write with NULL embedding)
- Bedrock validation errors (propagate to caller — BEHAVIOR CHANGE)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from mem_mcp.embeddings.bedrock import EmbeddingError
from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    from mem_mcp.embeddings.bedrock import EmbeddingClient

_log = get_logger("mem_mcp.embeddings.embed_or_skip")

# Bedrock Titan v2 input cap
EMBED_MAX_INPUT_CHARS = 32_000

EmbeddingResult = Literal[
    "ok",
    "skipped_oversize",
    "skipped_opt_out",
    "failed_throttled",
    "failed_unavailable",
    "failed_validation",
    "failed_unknown",
]


async def embed_or_skip(
    content: str,
    indexable: bool,
    embedder: EmbeddingClient,
    memory_id_for_log: str | None = None,
) -> tuple[list[float] | None, int, EmbeddingResult]:
    """Embed content or skip gracefully, returning (embedding, tokens, status).

    Status values:
      - 'skipped_opt_out': indexable=False
      - 'skipped_oversize': content exceeds EMBED_MAX_INPUT_CHARS
      - 'ok': embedding succeeded
      - 'failed_throttled': Bedrock throttled (retryable)
      - 'failed_unavailable': Bedrock unavailable (retryable)
      - 'failed_validation': Invalid input (NOT retryable — raised to caller)
      - 'failed_unknown': Unexpected error (retryable)

    BEHAVIOR CHANGE: bedrock_validation_failed (invalid_input) is now raised,
    not gracefully degraded. This indicates malformed input that the caller
    should see and handle.
    """
    if not indexable:
        _log.info(
            "memory_write_skipped_embed",
            extra={
                "memory_id": memory_id_for_log,
                "reason": "skipped_opt_out",
                "content_len": len(content),
            },
        )
        return None, 0, "skipped_opt_out"

    if len(content) > EMBED_MAX_INPUT_CHARS:
        _log.info(
            "memory_write_skipped_embed",
            extra={
                "memory_id": memory_id_for_log,
                "reason": "skipped_oversize",
                "content_len": len(content),
            },
        )
        return None, 0, "skipped_oversize"

    try:
        emb = await embedder.embed(content)
        return emb.vector, emb.input_tokens, "ok"
    except EmbeddingError as e:
        if e.code == "throttled":
            _log.warning(
                "memory_write_failed_embed",
                extra={
                    "memory_id": memory_id_for_log,
                    "status": "failed_throttled",
                    "content_len": len(content),
                    "retry_after_seconds": e.retry_after_seconds,
                },
            )
            return None, 0, "failed_throttled"
        elif e.code == "unavailable":
            _log.warning(
                "memory_write_failed_embed",
                extra={
                    "memory_id": memory_id_for_log,
                    "status": "failed_unavailable",
                    "content_len": len(content),
                    "retry_after_seconds": e.retry_after_seconds,
                },
            )
            return None, 0, "failed_unavailable"
        elif e.code == "invalid_input":
            _log.error(
                "memory_write_invalid_input",
                extra={
                    "memory_id": memory_id_for_log,
                    "content_len": len(content),
                },
            )
            raise
    except Exception as ex:
        _log.warning(
            "memory_write_failed_embed",
            extra={
                "memory_id": memory_id_for_log,
                "status": "failed_unknown",
                "content_len": len(content),
                "error": str(ex),
            },
        )
        return None, 0, "failed_unknown"
