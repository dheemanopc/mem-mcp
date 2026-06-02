"""Tests for OllamaEmbeddingClient (mocked HTTP — no real Ollama).

OE-1..4: response-shape parsing, error handling, retry behavior, Protocol
parity with BedrockEmbeddingClient via EmbedResult.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mem_mcp.embeddings.bedrock import EmbeddingError, EmbedResult
from mem_mcp.embeddings.ollama import OllamaEmbeddingClient

pytestmark = pytest.mark.asyncio


def _mock_httpx_post(payload: dict[str, Any] | None = None, status: int = 200) -> Any:
    """Build a context-manager-style httpx mock returning the given JSON."""
    resp = MagicMock()
    resp.status_code = status
    if status >= 400:
        # raise_for_status should raise; emulate httpx behavior
        import httpx

        request = MagicMock()
        request.url = "http://test/api/embed"
        resp.raise_for_status = MagicMock(
            side_effect=httpx.HTTPStatusError(f"status {status}", request=request, response=resp)
        )
    else:
        resp.raise_for_status = MagicMock(return_value=None)
    resp.json = MagicMock(return_value=payload or {})

    client = MagicMock()
    client.post = AsyncMock(return_value=resp)
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    return client


class TestOllamaEmbedShape:
    async def test_oe1_happy_path_returns_embed_result(self) -> None:
        payload = {
            "model": "bge-m3",
            "embeddings": [[0.1] * 1024],
            "prompt_eval_count": 7,
        }
        with patch("httpx.AsyncClient", return_value=_mock_httpx_post(payload)):
            c = OllamaEmbeddingClient(url="http://ollama:11434")
            out = await c.embed("hello world")
        assert isinstance(out, EmbedResult)
        assert len(out.vector) == 1024
        assert out.vector[0] == 0.1
        assert out.input_tokens == 7

    async def test_oe2_strips_trailing_slash_from_url(self) -> None:
        c = OllamaEmbeddingClient(url="http://ollama:11434/")
        assert c.url == "http://ollama:11434"

    async def test_oe3_missing_embeddings_key_raises_unavailable(self) -> None:
        bad_payload = {"model": "bge-m3", "prompt_eval_count": 0}
        with patch("httpx.AsyncClient", return_value=_mock_httpx_post(bad_payload)):
            c = OllamaEmbeddingClient(url="http://x")
            with pytest.raises(EmbeddingError) as exc:
                await c.embed("hi")
            assert exc.value.code == "unavailable"

    async def test_oe4_empty_embedding_vector_raises_unavailable(self) -> None:
        bad_payload = {"embeddings": [[]], "prompt_eval_count": 1}
        with patch("httpx.AsyncClient", return_value=_mock_httpx_post(bad_payload)):
            c = OllamaEmbeddingClient(url="http://x")
            with pytest.raises(EmbeddingError) as exc:
                await c.embed("hi")
            assert exc.value.code == "unavailable"

    async def test_oe_missing_prompt_eval_count_returns_zero_tokens(self) -> None:
        """prompt_eval_count is optional in some responses; coerce to 0."""
        payload = {"embeddings": [[0.5, 0.5]]}
        with patch("httpx.AsyncClient", return_value=_mock_httpx_post(payload)):
            c = OllamaEmbeddingClient(url="http://x")
            out = await c.embed("hi")
        assert out.input_tokens == 0


class TestOllamaInputValidation:
    async def test_empty_input_raises_invalid(self) -> None:
        c = OllamaEmbeddingClient(url="http://x")
        with pytest.raises(EmbeddingError) as exc:
            await c.embed("")
        assert exc.value.code == "invalid_input"

    async def test_oversized_input_raises_invalid(self) -> None:
        c = OllamaEmbeddingClient(url="http://x")
        with pytest.raises(EmbeddingError) as exc:
            await c.embed("x" * 60_000)
        assert exc.value.code == "invalid_input"
