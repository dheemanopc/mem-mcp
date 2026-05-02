"""Bedrock Titan Embed v2 client (amazon.titan-embed-text-v2:0).

Per LLD §4.4 + spec §10.1:
  - Region ap-south-1, dimensions=1024, normalize=true
  - Tenacity: 3 attempts with expo backoff (200ms, 800ms, 3.2s)
  - Retries: ThrottlingException, ServiceUnavailable, InternalServerError,
             ModelTimeoutException
  - Final failure → EmbeddingError(code='unavailable')
  - ValidationException OR len out of range → EmbeddingError(code='invalid_input')
  - boto3 sync calls wrapped with asyncio.to_thread (stdlib only; no aioboto3)
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from tenacity import (
    AsyncRetrying,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

EmbeddingErrorCode = Literal["throttled", "unavailable", "invalid_input"]

# Titan v2 input cap (per AWS docs)
_TITAN_MAX_INPUT_CHARS = 50_000
# Retryable Bedrock error codes
_RETRYABLE_AWS_CODES: frozenset[str] = frozenset(
    {
        "ThrottlingException",
        "ServiceUnavailable",
        "InternalServerError",
        "ModelTimeoutException",
    }
)
# Non-retryable validation errors
_INPUT_INVALID_CODES: frozenset[str] = frozenset({"ValidationException"})


class EmbeddingError(Exception):
    """Raised on Bedrock failures or invalid input."""

    def __init__(
        self,
        code: EmbeddingErrorCode,
        message: str = "",
        *,
        retry_after_seconds: int = 0,
    ) -> None:
        self.code: EmbeddingErrorCode = code
        self.retry_after_seconds = retry_after_seconds
        super().__init__(message or code)


@dataclass(frozen=True)
class EmbedResult:
    """Validated Bedrock embedding response."""

    vector: list[float]
    input_tokens: int


class EmbeddingClient(Protocol):
    """Embedding client boundary (test seam)."""

    async def embed(self, text: str) -> EmbedResult: ...


def _aws_error_code(exc: BaseException) -> str | None:
    """Extract the Bedrock error code from a botocore ClientError, if present."""
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return None
    err = response.get("Error")
    if not isinstance(err, dict):
        return None
    code = err.get("Code")
    return code if isinstance(code, str) else None


def _is_retryable(exc: BaseException) -> bool:
    code = _aws_error_code(exc)
    return code is not None and code in _RETRYABLE_AWS_CODES


def _is_input_invalid(exc: BaseException) -> bool:
    code = _aws_error_code(exc)
    return code is not None and code in _INPUT_INVALID_CODES


class BedrockEmbeddingClient:
    """Production Bedrock Titan Embed v2 client."""

    DEFAULT_MODEL_ID = "amazon.titan-embed-text-v2:0"
    DEFAULT_DIMENSIONS = 1024

    def __init__(
        self,
        *,
        region: str,
        model_id: str = DEFAULT_MODEL_ID,
        dimensions: int = DEFAULT_DIMENSIONS,
        client: Any | None = None,  # boto3 bedrock-runtime client; tests inject
    ) -> None:
        self.region = region
        self.model_id = model_id
        self.dimensions = dimensions
        self._client = client  # lazy-initialized in _get_client() if None

    def _get_client(self) -> Any:
        if self._client is None:
            import boto3  # type: ignore[import-untyped]

            self._client = boto3.client("bedrock-runtime", region_name=self.region)
        return self._client

    def _invoke_sync(self, text: str) -> dict[str, Any]:
        """Synchronous Bedrock call. Wrapped by asyncio.to_thread."""
        body = json.dumps({"inputText": text, "dimensions": self.dimensions, "normalize": True})
        client = self._get_client()
        resp = client.invoke_model(
            modelId=self.model_id,
            contentType="application/json",
            accept="application/json",
            body=body,
        )
        # resp['body'] is a streaming body; .read() returns bytes
        raw = resp["body"].read()
        return json.loads(raw)  # type: ignore[no-any-return]

    async def embed(self, text: str) -> EmbedResult:
        # Local-side input validation (cheap; avoids burning a Bedrock call)
        if not isinstance(text, str) or len(text) == 0:
            raise EmbeddingError("invalid_input", "empty input")
        if len(text) > _TITAN_MAX_INPUT_CHARS:
            raise EmbeddingError(
                "invalid_input",
                f"input exceeds {_TITAN_MAX_INPUT_CHARS} chars",
            )

        retrying = AsyncRetrying(
            stop=stop_after_attempt(3),
            wait=wait_exponential(multiplier=0.2, min=0.2, max=3.2),
            retry=retry_if_exception(_is_retryable),
            reraise=True,
        )

        try:
            async for attempt in retrying:
                with attempt:
                    payload = await asyncio.to_thread(self._invoke_sync, text)
        except Exception as exc:
            if _is_input_invalid(exc):
                raise EmbeddingError("invalid_input", str(exc)[:200]) from exc
            if _is_retryable(exc):
                # Final failure after retries
                raise EmbeddingError(
                    "unavailable",
                    f"bedrock unavailable after retries: {_aws_error_code(exc)}",
                    retry_after_seconds=4,
                ) from exc
            # Unexpected: bubble up as 'unavailable' but preserve message
            raise EmbeddingError(
                "unavailable",
                f"bedrock unexpected error: {type(exc).__name__}: {exc}",
            ) from exc

        # Validate the response shape
        try:
            embedding = payload["embedding"]
            tokens = payload.get("inputTextTokenCount", 0)
        except (KeyError, TypeError) as exc:
            raise EmbeddingError(
                "unavailable",
                f"unexpected bedrock response shape: {list(payload.keys()) if isinstance(payload, dict) else type(payload).__name__}",
            ) from exc

        if not isinstance(embedding, list) or len(embedding) != self.dimensions:
            raise EmbeddingError(
                "unavailable",
                f"unexpected embedding shape: type={type(embedding).__name__}, len={len(embedding) if hasattr(embedding, '__len__') else '?'}",
            )

        return EmbedResult(vector=[float(x) for x in embedding], input_tokens=int(tokens))
