"""Embedding client factory — single source of truth for provider selection.

Three callsites construct an EmbeddingClient: main.py (sync write path),
jobs/embedding_backfill.py, jobs/async_write_drain.py. All three must
honor the same MEM_MCP_EMBEDDINGS_PROVIDER env var, otherwise the async
drain re-embeds with one model while the inline path used another →
mixed vector spaces in the same memories.embedding column → search
ranking corrupts silently. This factory locks the invariant in one place.
"""

from __future__ import annotations

from mem_mcp.embeddings.bedrock import BedrockEmbeddingClient, EmbeddingClient


def make_embedding_client(settings: object) -> EmbeddingClient:
    """Construct an EmbeddingClient per settings.embeddings_provider.

    ``settings`` is a mem_mcp.config.Settings instance (untyped here to
    avoid a circular import — config.py imports nothing from embeddings).
    """
    provider = getattr(settings, "embeddings_provider", "bedrock")
    if provider == "ollama":
        ollama_url = getattr(settings, "ollama_url", None)
        if not ollama_url:
            raise RuntimeError(
                "MEM_MCP_EMBEDDINGS_PROVIDER=ollama requires MEM_MCP_OLLAMA_URL to be set"
            )
        # Local import — bedrock is the default path and ollama deps shouldn't
        # be paid in prod imports.
        from mem_mcp.embeddings.ollama import OllamaEmbeddingClient

        return OllamaEmbeddingClient(
            url=ollama_url,
            model=getattr(settings, "ollama_embed_model", "bge-m3"),
        )
    if provider != "bedrock":
        raise RuntimeError(
            f"MEM_MCP_EMBEDDINGS_PROVIDER={provider!r}: unknown provider; "
            "must be 'bedrock' or 'ollama'"
        )
    return BedrockEmbeddingClient(region=getattr(settings, "region", "ap-south-1"))
