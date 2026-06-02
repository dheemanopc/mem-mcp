"""Tests for make_embedding_client factory.

EE-4/EE-5: factory returns the right client type per settings.embeddings_provider;
raises if ollama selected without ollama_url.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from mem_mcp.embeddings.bedrock import BedrockEmbeddingClient
from mem_mcp.embeddings.factory import make_embedding_client
from mem_mcp.embeddings.ollama import OllamaEmbeddingClient


@dataclass
class _FakeSettings:
    """Test double matching just the Settings fields the factory reads."""

    embeddings_provider: str = "bedrock"
    ollama_url: str | None = None
    ollama_embed_model: str = "bge-m3"
    region: str = "ap-south-1"


class TestEmbeddingFactory:
    def test_default_returns_bedrock(self) -> None:
        c = make_embedding_client(_FakeSettings())
        assert isinstance(c, BedrockEmbeddingClient)
        assert c.region == "ap-south-1"

    def test_ollama_provider_returns_ollama_client(self) -> None:
        c = make_embedding_client(
            _FakeSettings(
                embeddings_provider="ollama",
                ollama_url="http://ollama:11434",
                ollama_embed_model="bge-m3",
            )
        )
        assert isinstance(c, OllamaEmbeddingClient)
        assert c.url == "http://ollama:11434"
        assert c.model == "bge-m3"

    def test_ollama_without_url_raises(self) -> None:
        with pytest.raises(RuntimeError, match="MEM_MCP_OLLAMA_URL"):
            make_embedding_client(_FakeSettings(embeddings_provider="ollama"))

    def test_unknown_provider_raises(self) -> None:
        with pytest.raises(RuntimeError, match="unknown provider"):
            make_embedding_client(_FakeSettings(embeddings_provider="huggingface"))

    def test_oe5_protocol_parity(self) -> None:
        """Both clients implement EmbeddingClient.embed(text) -> EmbedResult.

        Locks the Protocol so the factory can swap without downstream changes.
        """
        bedrock = make_embedding_client(_FakeSettings())
        ollama = make_embedding_client(
            _FakeSettings(embeddings_provider="ollama", ollama_url="http://x:11434")
        )
        # Both have embed(text: str) per the Protocol
        assert callable(getattr(bedrock, "embed", None))
        assert callable(getattr(ollama, "embed", None))

        # Best-effort: both should accept positional text param without keyword
        # (Reviewer §B). Just verifies signature shape; doesn't call.
        import inspect

        for client in (bedrock, ollama):
            sig = inspect.signature(client.embed)
            params = list(sig.parameters.keys())
            assert params == [
                "text"
            ], f"{type(client).__name__}.embed() params {params} != ['text']"

    def test_factory_handles_missing_attr_defensively(self) -> None:
        """Settings-shaped object without embeddings_provider falls back to bedrock."""

        class _Bare:
            region = "us-east-1"

        c = make_embedding_client(_Bare())
        assert isinstance(c, BedrockEmbeddingClient)


# Silence unused import in case test grows
_ = Any
