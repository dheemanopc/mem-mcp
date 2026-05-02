"""Tests for mem_mcp.config (T-3.2).

Per GUIDELINES §1.2, no real AWS — all SSM access goes through a fake
SsmLoader implementing the Protocol.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest

from mem_mcp.config import (
    BotoSsmLoader,
    Settings,
    SsmLoader,
    _reset_settings_cache_for_tests,
    _ssm_key_to_env_key,
    get_settings,
)


# ---------------------------------------------------------------------------
# Fake SsmLoader
# ---------------------------------------------------------------------------


class FakeSsmLoader:
    """Test double for SsmLoader. Stores params in a dict; returns them on demand."""

    def __init__(self, params: dict[str, str] | None = None) -> None:
        self.params = params or {}
        self.calls: list[tuple[str, bool]] = []

    def get_parameters_by_path(
        self, path: str, with_decryption: bool = True
    ) -> dict[str, str]:
        self.calls.append((path, with_decryption))
        return {k: v for k, v in self.params.items() if k.startswith(path)}


# Minimum fake SSM payload to satisfy all required Settings fields
_FULL_SSM_PARAMS = {
    "/mem-mcp/db/dsn": "postgresql://mem_app@/mem_mcp?host=/var/run/postgresql",
    "/mem-mcp/db/maint/dsn": "postgresql+psycopg://mem_maint@/mem_mcp?host=/var/run/postgresql",
    "/mem-mcp/cognito/user_pool_id": "ap-south-1_TESTPOOL",
    "/mem-mcp/cognito/domain": "memauth.dheemantech.in",
    "/mem-mcp/resource/url": "https://memsys.dheemantech.in",
    "/mem-mcp/web/url": "https://memapp.dheemantech.in",
    "/mem-mcp/cognito/web_client_id": "test-client-id",
    "/mem-mcp/cognito/web_client_secret": "test-client-secret",
    "/mem-mcp/internal/lambda_secret": "test-internal-secret",
    "/mem-mcp/ses/from": "noreply@dheemantech.com",
    "/mem-mcp/backup/bucket": "mem-mcp-backups-test",
    "/mem-mcp/backup/gpg_passphrase": "test-gpg-pass",
    "/mem-mcp/web/session_secret": "test-session-sec",
    "/mem-mcp/web/link_state_secret": "test-link-sec",
}


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _isolate_settings_cache() -> Iterator[None]:
    """Clear the lru_cache before AND after every test."""
    _reset_settings_cache_for_tests()
    yield
    _reset_settings_cache_for_tests()


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Remove any MEM_MCP_* env vars so tests start from a known state."""
    for k in list(os.environ):
        if k.startswith("MEM_MCP_"):
            monkeypatch.delenv(k, raising=False)


# ---------------------------------------------------------------------------
# _ssm_key_to_env_key
# ---------------------------------------------------------------------------


class TestSsmKeyToEnvKey:
    def test_simple_key(self) -> None:
        assert _ssm_key_to_env_key("/mem-mcp/db/dsn") == "MEM_MCP_DB_DSN"

    def test_nested_key(self) -> None:
        assert (
            _ssm_key_to_env_key("/mem-mcp/cognito/user_pool_id")
            == "MEM_MCP_COGNITO_USER_POOL_ID"
        )

    def test_dashes_become_underscores(self) -> None:
        assert _ssm_key_to_env_key("/mem-mcp/some-key/foo-bar") == "MEM_MCP_SOME_KEY_FOO_BAR"

    def test_rejects_path_outside_prefix(self) -> None:
        with pytest.raises(ValueError, match="must start with"):
            _ssm_key_to_env_key("/other-prefix/x")


# ---------------------------------------------------------------------------
# get_settings
# ---------------------------------------------------------------------------


class TestGetSettings:
    def test_loads_from_ssm_only(self) -> None:
        loader = FakeSsmLoader(_FULL_SSM_PARAMS)
        s = get_settings(loader)
        assert isinstance(s, Settings)
        assert s.db_dsn == "postgresql://mem_app@/mem_mcp?host=/var/run/postgresql"
        assert s.cognito_user_pool_id == "ap-south-1_TESTPOOL"
        assert s.region == "ap-south-1"  # default
        assert s.bedrock_model_id == "amazon.titan-embed-text-v2:0"  # default

    def test_env_overrides_ssm(self, monkeypatch: pytest.MonkeyPatch) -> None:
        loader = FakeSsmLoader(_FULL_SSM_PARAMS)
        monkeypatch.setenv("MEM_MCP_DB_DSN", "postgresql://override-from-env")
        monkeypatch.setenv("MEM_MCP_LOG_LEVEL", "DEBUG")
        s = get_settings(loader)
        assert s.db_dsn == "postgresql://override-from-env"
        assert s.log_level == "DEBUG"

    def test_loads_from_env_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # Set every required field via env; SSM returns nothing
        monkeypatch.setenv("MEM_MCP_DB_DSN", "x")
        monkeypatch.setenv("MEM_MCP_DB_MAINT_DSN", "x")
        monkeypatch.setenv("MEM_MCP_COGNITO_USER_POOL_ID", "x")
        monkeypatch.setenv("MEM_MCP_COGNITO_DOMAIN", "x")
        monkeypatch.setenv("MEM_MCP_RESOURCE_URL", "x")
        monkeypatch.setenv("MEM_MCP_WEB_URL", "x")
        monkeypatch.setenv("MEM_MCP_WEB_CLIENT_ID", "x")
        monkeypatch.setenv("MEM_MCP_WEB_CLIENT_SECRET", "x")
        monkeypatch.setenv("MEM_MCP_INTERNAL_LAMBDA_SECRET", "x")
        monkeypatch.setenv("MEM_MCP_SES_FROM", "x")
        monkeypatch.setenv("MEM_MCP_BACKUP_BUCKET", "x")
        monkeypatch.setenv("MEM_MCP_BACKUP_GPG_PASSPHRASE", "x")
        monkeypatch.setenv("MEM_MCP_WEB_SESSION_SECRET", "x")
        monkeypatch.setenv("MEM_MCP_LINK_STATE_SECRET", "x")
        s = get_settings(FakeSsmLoader())  # empty SSM
        assert s.db_dsn == "x"
        assert s.region == "ap-south-1"  # default

    def test_missing_required_field_raises(self) -> None:
        partial = {"/mem-mcp/db/dsn": "x"}  # only one of many required fields
        loader = FakeSsmLoader(partial)
        with pytest.raises(Exception) as exc_info:
            get_settings(loader)
        # Pydantic raises ValidationError; just check it mentions a missing field
        assert "field" in str(exc_info.value).lower() or "required" in str(exc_info.value).lower()

    def test_settings_is_frozen(self) -> None:
        loader = FakeSsmLoader(_FULL_SSM_PARAMS)
        s = get_settings(loader)
        with pytest.raises(Exception):  # Pydantic raises ValidationError on frozen
            s.region = "us-west-2"  # type: ignore[misc]

    def test_get_settings_is_cached(self) -> None:
        loader = FakeSsmLoader(_FULL_SSM_PARAMS)
        s1 = get_settings(loader)
        s2 = get_settings(loader)
        assert s1 is s2
        # Loader called only once
        assert len(loader.calls) == 1

    def test_loader_called_with_decryption(self) -> None:
        loader = FakeSsmLoader(_FULL_SSM_PARAMS)
        get_settings(loader)
        assert loader.calls == [("/mem-mcp/", True)]

    def test_extra_ssm_params_ignored(self) -> None:
        params = dict(_FULL_SSM_PARAMS)
        params["/mem-mcp/something/unknown"] = "ignored-value"
        loader = FakeSsmLoader(params)
        s = get_settings(loader)
        # No exception. Settings doesn't have a field for this; extra='ignore' handles it.
        assert s.db_dsn == _FULL_SSM_PARAMS["/mem-mcp/db/dsn"]


# ---------------------------------------------------------------------------
# Protocol structural typing
# ---------------------------------------------------------------------------


class TestSsmLoaderProtocol:
    def test_fake_satisfies_protocol(self) -> None:
        """FakeSsmLoader is structurally a SsmLoader."""
        loader: SsmLoader = FakeSsmLoader()  # mypy/static check
        result = loader.get_parameters_by_path("/mem-mcp/")
        assert result == {}

    def test_boto_loader_satisfies_protocol_signature(self) -> None:
        """BotoSsmLoader's signature matches the Protocol (compile-time only)."""
        # Don't instantiate BotoSsmLoader (would call boto3); just check the type
        # signature exists. mypy will catch divergence at lint time.
        assert hasattr(BotoSsmLoader, "get_parameters_by_path")
        assert callable(BotoSsmLoader.get_parameters_by_path)
