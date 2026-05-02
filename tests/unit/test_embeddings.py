"""Tests for mem_mcp.embeddings.bedrock (T-5.4)."""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock

import pytest

from mem_mcp.embeddings.bedrock import (
    BedrockEmbeddingClient,
    EmbeddingError,
)


def _ok_response(vector: list[float] | None = None, tokens: int = 12) -> dict[str, Any]:
    """Build a fake successful Bedrock invoke_model response."""
    vec = vector if vector is not None else [0.1] * 1024
    body = json.dumps({"embedding": vec, "inputTextTokenCount": tokens}).encode("utf-8")
    return {"body": io.BytesIO(body)}


def _client_error(code: str, message: str = "boom") -> Exception:
    """Construct a botocore.exceptions.ClientError for the given Bedrock code."""
    from botocore.exceptions import ClientError

    return ClientError(
        error_response={"Error": {"Code": code, "Message": message}},
        operation_name="InvokeModel",
    )


# --------------------------------------------------------------------------
# Input validation (no Bedrock call)
# --------------------------------------------------------------------------


class TestInputValidation:
    @pytest.mark.asyncio
    async def test_empty_string_raises_invalid_input(self) -> None:
        client = MagicMock()
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        with pytest.raises(EmbeddingError) as exc_info:
            await c.embed("")
        assert exc_info.value.code == "invalid_input"
        # Bedrock NOT called
        client.invoke_model.assert_not_called()

    @pytest.mark.asyncio
    async def test_too_long_raises_invalid_input(self) -> None:
        client = MagicMock()
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        with pytest.raises(EmbeddingError) as exc_info:
            await c.embed("x" * 50_001)
        assert exc_info.value.code == "invalid_input"
        client.invoke_model.assert_not_called()


# --------------------------------------------------------------------------
# Happy path
# --------------------------------------------------------------------------


class TestSuccess:
    @pytest.mark.asyncio
    async def test_returns_embed_result(self) -> None:
        client = MagicMock()
        client.invoke_model.return_value = _ok_response(tokens=42)
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        result = await c.embed("hello world")
        assert len(result.vector) == 1024
        assert result.input_tokens == 42
        client.invoke_model.assert_called_once()
        # Verify we passed the right model + body
        kwargs = client.invoke_model.call_args.kwargs
        assert kwargs["modelId"] == "amazon.titan-embed-text-v2:0"
        body = json.loads(kwargs["body"])
        assert body["inputText"] == "hello world"
        assert body["dimensions"] == 1024
        assert body["normalize"] is True


# --------------------------------------------------------------------------
# Retry behavior
# --------------------------------------------------------------------------


class TestRetries:
    @pytest.mark.asyncio
    async def test_throttling_retried_then_succeeds(self) -> None:
        client = MagicMock()
        client.invoke_model.side_effect = [
            _client_error("ThrottlingException"),
            _ok_response(),
        ]
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        result = await c.embed("retry-then-ok")
        assert len(result.vector) == 1024
        assert client.invoke_model.call_count == 2

    @pytest.mark.asyncio
    async def test_three_throttles_raises_unavailable(self) -> None:
        client = MagicMock()
        client.invoke_model.side_effect = [
            _client_error("ThrottlingException"),
            _client_error("ThrottlingException"),
            _client_error("ThrottlingException"),
        ]
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        with pytest.raises(EmbeddingError) as exc_info:
            await c.embed("perpetually-throttled")
        assert exc_info.value.code == "unavailable"
        assert exc_info.value.retry_after_seconds > 0
        assert client.invoke_model.call_count == 3

    @pytest.mark.asyncio
    async def test_service_unavailable_retried(self) -> None:
        client = MagicMock()
        client.invoke_model.side_effect = [
            _client_error("ServiceUnavailable"),
            _ok_response(),
        ]
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        result = await c.embed("ok")
        assert len(result.vector) == 1024
        assert client.invoke_model.call_count == 2


# --------------------------------------------------------------------------
# Non-retryable errors
# --------------------------------------------------------------------------


class TestNonRetryableErrors:
    @pytest.mark.asyncio
    async def test_validation_exception_is_invalid_input(self) -> None:
        client = MagicMock()
        client.invoke_model.side_effect = _client_error("ValidationException", "bad input")
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        with pytest.raises(EmbeddingError) as exc_info:
            await c.embed("trips-validation")
        assert exc_info.value.code == "invalid_input"
        # Not retried
        assert client.invoke_model.call_count == 1

    @pytest.mark.asyncio
    async def test_unknown_error_is_unavailable_not_retried(self) -> None:
        client = MagicMock()
        client.invoke_model.side_effect = _client_error("AccessDeniedException")
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        with pytest.raises(EmbeddingError) as exc_info:
            await c.embed("denied")
        assert exc_info.value.code == "unavailable"
        # Not retried (not in retryable set)
        assert client.invoke_model.call_count == 1


# --------------------------------------------------------------------------
# Response shape validation
# --------------------------------------------------------------------------


class TestResponseShape:
    @pytest.mark.asyncio
    async def test_missing_embedding_key_raises(self) -> None:
        body = json.dumps({"inputTextTokenCount": 5}).encode("utf-8")
        client = MagicMock()
        client.invoke_model.return_value = {"body": io.BytesIO(body)}
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        with pytest.raises(EmbeddingError) as exc_info:
            await c.embed("ok")
        assert exc_info.value.code == "unavailable"

    @pytest.mark.asyncio
    async def test_wrong_vector_length_raises(self) -> None:
        client = MagicMock()
        client.invoke_model.return_value = _ok_response(vector=[0.1] * 512)
        c = BedrockEmbeddingClient(region="ap-south-1", client=client)
        with pytest.raises(EmbeddingError) as exc_info:
            await c.embed("ok")
        assert exc_info.value.code == "unavailable"


# --------------------------------------------------------------------------
# EmbeddingError
# --------------------------------------------------------------------------


class TestEmbeddingError:
    def test_code_attribute(self) -> None:
        err = EmbeddingError("throttled", "slow down", retry_after_seconds=8)
        assert err.code == "throttled"
        assert err.retry_after_seconds == 8
        assert "slow down" in str(err)

    def test_default_message(self) -> None:
        err = EmbeddingError("invalid_input")
        assert "invalid_input" in str(err)
