"""Helper to embed content or gracefully skip on degraded conditions.

Handles:
- Caller opt-out via indexable=False
- Content too large for Bedrock (oversize)
- Bedrock throttle/unavailable (graceful degrade, write with NULL embedding)
- Bedrock validation errors (propagate to caller)
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from mem_mcp.embeddings.bedrock import EmbeddingError
from mem_mcp.logging_setup import get_logger

if TYPE_CHECKING:
    from mem_mcp.embeddings.bedrock import EmbeddingClient

_log = get_logger("mem_mcp.embeddings.embed_or_skip")

# Bedrock Titan v2 input cap
EMBED_MAX_INPUT_CHARS = 32_000


async def embed_or_skip(
    content: str,
    indexable: bool,
    embedder: EmbeddingClient,
    memory_id_for_log: str | None = None,
) -> tuple[list[float] | None, int, str]:
    """Embed content or skip gracefully, returning (embedding, tokens, reason).

    Reasons:
      - 'caller_opted_out': indexable=False
      - 'oversize': content exceeds EMBED_MAX_INPUT_CHARS
      - 'bedrock_degraded:throttled': ThrottlingException
      - 'bedrock_degraded:unavailable': ServiceUnavailable / other retryable
      - 'embedded': success
      - raises EmbeddingError for invalid_input or unexpected errors
    """
    if not indexable:
        _log.info(
            "memory_write_skipped_embed",
            extra={
                "memory_id": memory_id_for_log,
                "reason": "caller_opted_out",
                "content_len": len(content),
            },
        )
        return None, 0, "caller_opted_out"

    if len(content) > EMBED_MAX_INPUT_CHARS:
        _log.info(
            "memory_write_skipped_embed",
            extra={
                "memory_id": memory_id_for_log,
                "reason": "oversize",
                "content_len": len(content),
            },
        )
        return None, 0, "oversize"

    try:
        emb = await embedder.embed(content)
        return emb.vector, emb.input_tokens, "embedded"
    except EmbeddingError as e:
        if e.code in ("throttled", "unavailable"):
            _log.warning(
                "memory_write_skipped_embed",
                extra={
                    "memory_id": memory_id_for_log,
                    "reason": f"bedrock_degraded:{e.code}",
                    "content_len": len(content),
                    "retry_after_seconds": e.retry_after_seconds,
                },
            )
            return None, 0, f"bedrock_degraded:{e.code}"
        # Re-raise validation errors for caller to handle
        raise
