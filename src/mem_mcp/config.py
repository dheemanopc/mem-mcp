"""Process configuration for mem-mcp.

Loads runtime settings from a combination of environment variables and AWS SSM
Parameter Store. Per GUIDELINES §1.2, the SSM call is wrapped behind a
``SsmLoader`` Protocol so tests can inject a fake without touching real AWS.

Public API:
- ``Settings`` — frozen Pydantic BaseSettings with every runtime field
- ``SsmLoader`` — Protocol for fetching parameters
- ``BotoSsmLoader`` — production impl (boto3); used when ``get_settings()`` is
  called without a loader
- ``get_settings(loader=None) -> Settings`` — singleton, lru-cached
- ``_reset_settings_cache_for_tests()`` — test-only cache reset
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol

from pydantic_settings import BaseSettings, SettingsConfigDict

_SSM_PREFIX = "/mem-mcp/"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="MEM_MCP_",
        frozen=True,
        extra="ignore",  # SSM may return params we don't have fields for
        case_sensitive=False,  # MEM_MCP_DB_DSN, mem_mcp_db_dsn both work
    )

    region: str = "ap-south-1"
    db_dsn: str
    db_maint_dsn: str
    cognito_user_pool_id: str
    cognito_domain: str
    resource_url: str
    web_url: str
    web_client_id: str
    web_client_secret: str
    internal_lambda_secret: str
    ses_from: str
    backup_bucket: str
    backup_gpg_passphrase: str
    web_session_secret: str
    link_state_secret: str
    bedrock_model_id: str = "amazon.titan-embed-text-v2:0"
    log_level: str = "INFO"


class SsmLoader(Protocol):
    """Boundary for SSM Parameter Store reads.

    Production wires ``BotoSsmLoader``; tests inject in-memory fakes per
    GUIDELINES §1.2 (no moto, no localstack).
    """

    def get_parameters_by_path(self, path: str, with_decryption: bool = True) -> dict[str, str]:
        """Return {full_ssm_path: value} for all params under ``path``."""
        ...


class BotoSsmLoader:
    """Production SSM loader using boto3.

    Constructs an SSM client lazily; closes nothing (boto3 sessions are
    cheap and short-lived in the load-once-at-startup pattern).
    """

    def __init__(self, region: str | None = None) -> None:
        # Imported here so unit tests don't pay the boto3 import cost
        import boto3  # type: ignore[import-untyped]

        kwargs = {}
        if region:
            kwargs["region_name"] = region
        self._client = boto3.client("ssm", **kwargs)

    def get_parameters_by_path(self, path: str, with_decryption: bool = True) -> dict[str, str]:
        result: dict[str, str] = {}
        paginator = self._client.get_paginator("get_parameters_by_path")
        for page in paginator.paginate(Path=path, Recursive=True, WithDecryption=with_decryption):
            for param in page["Parameters"]:
                result[param["Name"]] = param["Value"]
        return result


def _ssm_key_to_env_key(ssm_path: str) -> str:
    """e.g. '/mem-mcp/db/password' → 'MEM_MCP_DB_PASSWORD'."""
    if not ssm_path.startswith(_SSM_PREFIX):
        raise ValueError(f"SSM path must start with {_SSM_PREFIX!r}: {ssm_path!r}")
    suffix = ssm_path[len(_SSM_PREFIX) :]
    return "MEM_MCP_" + suffix.upper().replace("/", "_").replace("-", "_")


@lru_cache(maxsize=1)
def get_settings(loader: SsmLoader | None = None) -> Settings:
    """Load settings from env + SSM (env takes precedence). Cached.

    To rotate secrets without restart, call ``_reset_settings_cache_for_tests()``
    (or restart the process). In v1 we restart the process per GUIDELINES.
    """
    if loader is None:
        # Production default — read region from env first if available
        region = os.environ.get("MEM_MCP_REGION", "ap-south-1")
        loader = BotoSsmLoader(region=region)

    ssm_values = loader.get_parameters_by_path(_SSM_PREFIX, with_decryption=True)

    # Build env-key → value dict from SSM, then overlay os.environ (env wins)
    merged: dict[str, str] = {}
    for path, value in ssm_values.items():
        try:
            env_key = _ssm_key_to_env_key(path)
        except ValueError:
            # Parameters outside /mem-mcp/ shouldn't be returned by the loader
            # but skip defensively
            continue
        merged[env_key] = value
    # os.environ takes precedence
    for k, v in os.environ.items():
        if k.startswith("MEM_MCP_"):
            merged[k] = v

    # Pydantic BaseSettings reads env vars itself; we'd have to either set them
    # all in os.environ (mutates global state — bad) OR pass them as init kwargs
    # by stripping the prefix and lowercasing. The latter:
    init_kwargs: dict[str, str] = {}
    for env_key, value in merged.items():
        if env_key.startswith("MEM_MCP_"):
            init_kwargs[env_key[len("MEM_MCP_") :].lower()] = value

    return Settings(**init_kwargs)


def _reset_settings_cache_for_tests() -> None:
    """Clear the lru_cache so the next get_settings() call rebuilds.

    For test isolation only; do NOT call from production code.
    """
    get_settings.cache_clear()
