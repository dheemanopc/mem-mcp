"""Embedding clients (Bedrock Titan v2 in v1; reranker may join later).

Public API:
    EmbeddingClient    — Protocol implemented by all clients
    EmbedResult        — frozen dataclass: vector + input_tokens
    EmbeddingError     — typed exception with .code
    BedrockEmbeddingClient — production impl using boto3
"""

from mem_mcp.embeddings.bedrock import (
    BedrockEmbeddingClient,
    EmbeddingClient,
    EmbeddingError,
    EmbeddingErrorCode,
    EmbedResult,
)

__all__ = [
    "BedrockEmbeddingClient",
    "EmbedResult",
    "EmbeddingClient",
    "EmbeddingError",
    "EmbeddingErrorCode",
]
