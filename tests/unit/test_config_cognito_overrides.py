"""Tests for cognito/ollama optional overrides in Settings.

Per R-LOCALDEV trio v2: when MEM_MCP_COGNITO_ISSUER_URL / _JWKS_URL /
MEM_MCP_EMBEDDINGS_PROVIDER / _OLLAMA_URL / _OLLAMA_EMBED_MODEL are unset,
prod path is byte-identical (defaults preserve legacy AWS URL form).
"""

from __future__ import annotations

import pytest

from mem_mcp.config import Settings, _reset_settings_cache_for_tests


@pytest.fixture(autouse=True)
def _reset_cache() -> None:
    _reset_settings_cache_for_tests()


def _base_kwargs() -> dict[str, str]:
    return {
        "db_dsn": "postgresql://x@y/z",
        "db_maint_dsn": "postgresql+psycopg://x@y/z",
        "cognito_user_pool_id": "ap-south-1_TESTPOOL",
        "cognito_domain": "auth.example.com",
        "resource_url": "http://x",
        "web_url": "http://x",
        "web_client_id": "x",
        "web_client_secret": "x",
        "internal_lambda_secret": "x",
        "ses_from": "x@x",
        "backup_bucket": "x",
        "backup_gpg_passphrase": "x",
        "web_session_secret": "x",
        "link_state_secret": "x",
    }


class TestCognitoOverrides:
    def test_cc1_issuer_url_defaults_to_none(self) -> None:
        s = Settings(**_base_kwargs())
        assert s.cognito_issuer_url is None

    def test_cc2_jwks_url_defaults_to_none(self) -> None:
        s = Settings(**_base_kwargs())
        assert s.cognito_jwks_url is None

    def test_cc3_legacy_path_unchanged_when_overrides_unset(self) -> None:
        """Regression-lock: prod URL form is byte-identical when both unset.

        Replicates the construction logic in main.py:359-372.
        """
        s = Settings(**_base_kwargs())
        # Mirror what main.py does
        issuer = s.cognito_issuer_url or (
            f"https://cognito-idp.{s.region}.amazonaws.com/{s.cognito_user_pool_id}"
        )
        jwks_url = s.cognito_jwks_url or f"{issuer}/.well-known/jwks.json"
        assert issuer == "https://cognito-idp.ap-south-1.amazonaws.com/ap-south-1_TESTPOOL"
        assert jwks_url == (
            "https://cognito-idp.ap-south-1.amazonaws.com/"
            "ap-south-1_TESTPOOL/.well-known/jwks.json"
        )

    def test_cc4_issuer_override_takes_precedence(self) -> None:
        kw = _base_kwargs()
        kw["cognito_issuer_url"] = "http://localhost:9229/local_memmcp"
        s = Settings(**kw)
        issuer = s.cognito_issuer_url or (
            f"https://cognito-idp.{s.region}.amazonaws.com/{s.cognito_user_pool_id}"
        )
        assert issuer == "http://localhost:9229/local_memmcp"

    def test_cc5_jwks_url_override_takes_precedence(self) -> None:
        kw = _base_kwargs()
        kw["cognito_issuer_url"] = "http://localhost:9229/local_memmcp"
        kw["cognito_jwks_url"] = "http://cognito-local:9229/local_memmcp/.well-known/jwks.json"
        s = Settings(**kw)
        assert s.cognito_jwks_url == (
            "http://cognito-local:9229/local_memmcp/.well-known/jwks.json"
        )


class TestEmbeddingsProviderOverrides:
    def test_ee1_provider_defaults_to_bedrock(self) -> None:
        s = Settings(**_base_kwargs())
        assert s.embeddings_provider == "bedrock"

    def test_ee2_ollama_provider_opt_in(self) -> None:
        kw = _base_kwargs()
        kw["embeddings_provider"] = "ollama"
        kw["ollama_url"] = "http://ollama:11434"
        s = Settings(**kw)
        assert s.embeddings_provider == "ollama"
        assert s.ollama_url == "http://ollama:11434"

    def test_ee3_ollama_embed_model_defaults_to_bge_m3(self) -> None:
        s = Settings(**_base_kwargs())
        assert s.ollama_embed_model == "bge-m3"
