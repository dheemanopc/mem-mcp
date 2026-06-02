"""Ollama embeddings client — local-dev alternative to Bedrock Titan v2.

Targets Ollama's `/api/embed` endpoint (the post-2024 plural form, not the
legacy `/api/embeddings`). The plural form returns L2-normalized vectors
by default (matches Titan v2 with `normalize: true`) and exposes
`prompt_eval_count` for token accounting parity with EmbedResult.

Same EmbeddingClient Protocol as BedrockEmbeddingClient — drop-in via
mem_mcp.embeddings.factory.make_embedding_client.

Default model: bge-m3 (1024-dim, multilingual, no schema migration vs
Titan v2). Override via MEM_MCP_OLLAMA_EMBED_MODEL.
"""

from __future__ import annotations

import asyncio
from typing import Any

from tenacity import AsyncRetrying, retry_if_exception, stop_after_attempt, wait_exponential

from mem_mcp.embeddings.bedrock import EmbeddingError, EmbedResult

# bge-m3 caps at 8192 tokens; 50_000 chars is a safe Latin-ish proxy upper bound.
_OLLAMA_MAX_INPUT_CHARS = 50_000


def _is_retryable_http(exc: BaseException) -> bool:
    """Retry on 5xx + transient network errors, not on 4xx."""
    # httpx is imported in the client; here we just check by attribute name
    # to avoid the import cost at decoration time.
    status = getattr(getattr(exc, "response", None), "status_code", None)
    if isinstance(status, int):
        return 500 <= status < 600
    # Connect / read / pool errors → retry
    return type(exc).__name__ in {
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "PoolTimeout",
        "RemoteProtocolError",
    }


class OllamaEmbeddingClient:
    """Production-grade Ollama embeddings client over the `/api/embed` endpoint."""

    def __init__(
        self,
        *,
        url: str,
        model: str = "bge-m3",
        timeout_seconds: float = 30.0,
    ) -> None:
        # Strip trailing slash so f"{url}/api/embed" is well-formed.
        self.url = url.rstrip("/")
        self.model = model
        self.timeout_seconds = timeout_seconds

    async def _post(self, text: str) -> dict[str, Any]:
        """Single POST to Ollama; raises httpx errors for the retry layer."""
        # Local import keeps unit tests from paying httpx import cost at module load.
        import httpx

        async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
            resp = await client.post(
                f"{self.url}/api/embed",
                json={"model": self.model, "input": text},
            )
            resp.raise_for_status()
            data: Any = resp.json()
        if not isinstance(data, dict):
            raise EmbeddingError("unavailable", f"non-dict response: {type(data).__name__}")
        return data

    async def embed(self, text: str) -> EmbedResult:
        # Local-side input validation (mirrors BedrockEmbeddingClient.embed).
        if not isinstance(text, str) or len(text) == 0:
            raise EmbeddingError("invalid_input", "empty input")
        if len(text) > _OLLAMA_MAX_INPUT_CHARS:
            raise EmbeddingError("invalid_input", f"input exceeds {_OLLAMA_MAX_INPUT_CHARS} chars")

        retrying = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=3.2),
            retry=retry_if_exception(_is_retryable_http),
            reraise=True,
        )

        try:
            async for attempt in retrying:
                with attempt:
                    payload = await self._post(text)
        except Exception as exc:
            # Anything that escapes retries is treated as unavailable;
            # preserve message for ops.
            raise EmbeddingError(
                "unavailable", f"ollama embed failed: {type(exc).__name__}: {exc}"
            ) from exc

        # /api/embed shape: {"model": ..., "embeddings": [[float, ...]], "prompt_eval_count": int}
        try:
            embeddings = payload["embeddings"]
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(
                "unavailable",
                f"unexpected ollama response shape: keys={list(payload.keys())}",
            ) from exc
        if not isinstance(embeddings, list) or len(embeddings) == 0:
            raise EmbeddingError(
                "unavailable", f"empty or non-list 'embeddings': {type(embeddings).__name__}"
            )
        vector = embeddings[0]
        if not isinstance(vector, list) or len(vector) == 0:
            raise EmbeddingError(
                "unavailable", f"empty or non-list embedding vector: {type(vector).__name__}"
            )

        tokens_raw = payload.get("prompt_eval_count", 0)
        try:
            tokens = int(tokens_raw)
        except (TypeError, ValueError):
            tokens = 0

        # Best-effort yield to the loop after potentially heavy parsing.
        await asyncio.sleep(0)

        return EmbedResult(vector=[float(x) for x in vector], input_tokens=tokens)
