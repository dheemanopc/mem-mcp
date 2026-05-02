"""Tests for mem_mcp.main FastAPI app (T-3.5)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from mem_mcp.health import CheckResult, HealthChecker
from mem_mcp.main import create_app

# ---------------------------------------------------------------------------
# Fake HealthChecker
# ---------------------------------------------------------------------------


class FakeChecker:
    """Returns a pre-canned CheckResult. Records call count."""

    def __init__(
        self, name: str, status: str = "ok", message: str = ""
    ) -> None:
        self.name = name
        self._status: str = status
        self._message = message
        self.calls = 0

    async def check(self) -> CheckResult:
        self.calls += 1
        return CheckResult(
            self.name, self._status, self._message  # type: ignore[arg-type]
        )


def _build_app(checkers: list[Any]) -> TestClient:
    """Build a test client with explicit checkers — no lifespan / DB init."""
    app = create_app(checkers=checkers)
    # Use TestClient WITHOUT triggering lifespan (would require real config + pool).
    # FastAPI's TestClient runs lifespan by default; we bypass by constructing
    # without entering the with-block context. Workaround: pass raise_server_exceptions=False
    # and patch app.router.lifespan_context to a no-op.
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def noop_lifespan(_app):  # type: ignore[no-untyped-def]
        yield

    app.router.lifespan_context = noop_lifespan
    return TestClient(app)


# ---------------------------------------------------------------------------
# /healthz
# ---------------------------------------------------------------------------


class TestHealthz:
    def test_returns_200_ok(self) -> None:
        client = _build_app([FakeChecker("db")])
        resp = client.get("/healthz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok"}

    def test_does_not_invoke_checkers(self) -> None:
        """healthz is liveness only — must not call any checker."""
        c = FakeChecker("db")
        client = _build_app([c])
        client.get("/healthz")
        assert c.calls == 0


# ---------------------------------------------------------------------------
# /readyz
# ---------------------------------------------------------------------------


class TestReadyz:
    def test_all_ok_returns_200(self) -> None:
        checkers = [
            FakeChecker("db"),
            FakeChecker("bedrock"),
            FakeChecker("cognito_jwks"),
        ]
        client = _build_app(checkers)
        resp = client.get("/readyz")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["checks"] == {"db": "ok", "bedrock": "ok", "cognito_jwks": "ok"}
        # Each checker invoked once
        for c in checkers:
            assert c.calls == 1

    def test_one_failure_returns_503_with_details(self) -> None:
        checkers = [
            FakeChecker("db"),
            FakeChecker("bedrock", status="fail", message="connection refused"),
            FakeChecker("cognito_jwks"),
        ]
        client = _build_app(checkers)
        resp = client.get("/readyz")
        assert resp.status_code == 503
        body = resp.json()
        assert body["status"] == "fail"
        assert body["checks"]["db"] == "ok"
        assert body["checks"]["bedrock"] == "connection refused"
        assert body["checks"]["cognito_jwks"] == "ok"

    def test_all_failures_returns_503(self) -> None:
        checkers = [
            FakeChecker("db", status="fail", message="x"),
            FakeChecker("bedrock", status="fail", message="y"),
        ]
        client = _build_app(checkers)
        resp = client.get("/readyz")
        assert resp.status_code == 503
        assert resp.json()["status"] == "fail"

    def test_no_checkers_means_ok(self) -> None:
        """Edge case: empty checker list → vacuously ok."""
        client = _build_app([])
        resp = client.get("/readyz")
        assert resp.status_code == 200
        assert resp.json() == {"status": "ok", "checks": {}}


# ---------------------------------------------------------------------------
# Docs are disabled
# ---------------------------------------------------------------------------


class TestDocsDisabled:
    @pytest.mark.parametrize("path", ["/docs", "/redoc", "/openapi.json"])
    def test_docs_endpoints_404(self, path: str) -> None:
        client = _build_app([])
        resp = client.get(path)
        assert resp.status_code == 404
