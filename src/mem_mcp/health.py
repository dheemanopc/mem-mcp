"""Health-check protocols + production implementations for /readyz.

Per GUIDELINES §1.2, the dependency probes live behind narrow Protocol
seams so the test suite can inject fakes without real DB/Bedrock/HTTP.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Protocol

CheckStatus = Literal["ok", "fail"]


@dataclass(frozen=True)
class CheckResult:
    name: str
    status: CheckStatus
    message: str = ""

    def is_ok(self) -> bool:
        return self.status == "ok"


class HealthChecker(Protocol):
    """A single named health check. ``name`` keys into the /readyz response."""

    name: str

    async def check(self) -> CheckResult: ...


# ---------------------------------------------------------------------------
# Production implementations (each instantiated lazily from main.py)
# ---------------------------------------------------------------------------


class DbHealthChecker:
    """Runs SELECT 1 via the global mem_mcp.db pool."""

    name = "db"

    async def check(self) -> CheckResult:
        try:
            from mem_mcp.db import get_pool, system_tx

            pool = get_pool()
            async with system_tx(pool) as conn:
                value = await conn.fetchval("SELECT 1")
            if value == 1:
                return CheckResult(self.name, "ok")
            return CheckResult(self.name, "fail", f"unexpected SELECT 1 result: {value!r}")
        except Exception as exc:
            return CheckResult(self.name, "fail", str(exc)[:200])


class BedrockHealthChecker:
    """Verifies a Bedrock client can be constructed for the configured region.

    We deliberately do NOT make a real API call here — that would cost money
    and slow down /readyz. Successful boto3 client construction means
    credentials + region are wired correctly, which is what /readyz cares about.
    """

    name = "bedrock"

    def __init__(self, region: str) -> None:
        self.region = region

    async def check(self) -> CheckResult:
        try:
            import boto3  # type: ignore[import-untyped]

            boto3.client("bedrock-runtime", region_name=self.region)
            return CheckResult(self.name, "ok")
        except Exception as exc:
            return CheckResult(self.name, "fail", str(exc)[:200])


class CognitoJwksHealthChecker:
    """Fetches Cognito JWKS document and verifies it parses as JSON.

    URL: https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json
    """

    name = "cognito_jwks"

    def __init__(self, region: str, user_pool_id: str, timeout_seconds: float = 3.0) -> None:
        self.region = region
        self.user_pool_id = user_pool_id
        self.timeout_seconds = timeout_seconds

    async def check(self) -> CheckResult:
        url = (
            f"https://cognito-idp.{self.region}.amazonaws.com/"
            f"{self.user_pool_id}/.well-known/jwks.json"
        )
        try:
            import httpx

            async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                payload = resp.json()
            if not isinstance(payload, dict) or "keys" not in payload:
                return CheckResult(self.name, "fail", "jwks payload missing 'keys'")
            return CheckResult(self.name, "ok")
        except Exception as exc:
            return CheckResult(self.name, "fail", str(exc)[:200])


def aggregate(checks: list[CheckResult]) -> tuple[CheckStatus, dict[str, str]]:
    """Combine individual results into the /readyz body.

    Returns (overall_status, per_check_messages). per_check_messages maps
    check name → 'ok' or the failure message.
    """
    overall: CheckStatus = "ok"
    out: dict[str, str] = {}
    for c in checks:
        if c.is_ok():
            out[c.name] = "ok"
        else:
            out[c.name] = c.message or "fail"
            overall = "fail"
    return overall, out
