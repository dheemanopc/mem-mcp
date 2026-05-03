"""Tests for mem_mcp.web.csrf (T-8.3)."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mem_mcp.web.csrf import (
    CSRF_COOKIE_NAME,
    CSRF_HEADER_NAME,
    CsrfMiddleware,
    mint_csrf_token,
)

# --------------------------------------------------------------------------
# Tests for mint_csrf_token
# --------------------------------------------------------------------------


class TestMintCsrfToken:
    def test_returns_string(self) -> None:
        """mint_csrf_token returns a string."""
        token = mint_csrf_token()
        assert isinstance(token, str)

    def test_unique_tokens(self) -> None:
        """mint_csrf_token returns unique values."""
        tokens = [mint_csrf_token() for _ in range(10)]
        assert len(set(tokens)) == 10

    def test_token_is_urlsafe(self) -> None:
        """mint_csrf_token produces urlsafe base64."""
        token = mint_csrf_token()
        # Should not raise
        import base64

        decoded = base64.urlsafe_b64decode(token + "==")
        assert len(decoded) == 32


# --------------------------------------------------------------------------
# Tests for CsrfMiddleware
# --------------------------------------------------------------------------


def _build_app() -> tuple[FastAPI, TestClient]:
    """Build a test app with CsrfMiddleware."""
    app = FastAPI()
    app.add_middleware(CsrfMiddleware)

    @app.get("/api/web/foo")
    async def get_foo() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/api/web/foo")
    async def post_foo() -> dict[str, str]:
        return {"status": "ok"}

    @app.patch("/api/web/bar")
    async def patch_bar() -> dict[str, str]:
        return {"status": "ok"}

    @app.delete("/api/web/baz")
    async def delete_baz() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/auth/logout")
    async def logout() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    return app, TestClient(app)


class TestCsrfMiddleware:
    def test_get_no_csrf_required(self) -> None:
        """GET /api/web/* bypasses CSRF check."""
        app, client = _build_app()
        response = client.get("/api/web/foo")
        assert response.status_code == 200

    def test_post_without_token_returns_403(self) -> None:
        """POST /api/web/* without CSRF token returns 403."""
        app, client = _build_app()
        response = client.post("/api/web/foo")
        assert response.status_code == 403
        assert "csrf_token_missing" in response.text

    def test_post_with_matching_token_succeeds(self) -> None:
        """POST /api/web/* with matching CSRF token succeeds."""
        app, client = _build_app()

        # Set a known token in both cookie and header
        token = mint_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, token)

        # POST with matching cookie and header
        post_response = client.post(
            "/api/web/foo",
            headers={CSRF_HEADER_NAME: token},
        )
        assert post_response.status_code == 200

    def test_post_with_mismatched_token_returns_403(self) -> None:
        """POST /api/web/* with mismatched CSRF token returns 403."""
        app, client = _build_app()

        # Set a CSRF token cookie manually
        token1 = mint_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, token1)

        # Try to POST with a different token in the header
        token2 = mint_csrf_token()
        post_response = client.post(
            "/api/web/foo",
            headers={CSRF_HEADER_NAME: token2},
        )
        assert post_response.status_code == 403
        assert "csrf_token_mismatch" in post_response.text

    def test_unprotected_path_bypassed(self) -> None:
        """POST /healthz (not protected) bypasses CSRF check."""
        app, client = _build_app()
        # POST without CSRF token should succeed
        response = client.post("/healthz")
        assert response.status_code == 200

    def test_csrf_cookie_set_on_first_response(self) -> None:
        """CSRF cookie is set on first response if absent."""
        app, client = _build_app()
        response = client.get("/api/web/foo")
        assert response.status_code == 200
        assert CSRF_COOKIE_NAME in client.cookies

    def test_patch_protected(self) -> None:
        """PATCH /api/web/* is protected by CSRF."""
        app, client = _build_app()

        # PATCH without token should fail
        response = client.patch("/api/web/bar")
        assert response.status_code == 403

        # PATCH with token should succeed
        token = mint_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, token)
        response = client.patch(
            "/api/web/bar",
            headers={CSRF_HEADER_NAME: token},
        )
        assert response.status_code == 200

    def test_delete_protected(self) -> None:
        """DELETE /api/web/* is protected by CSRF."""
        app, client = _build_app()

        # DELETE without token should fail
        response = client.delete("/api/web/baz")
        assert response.status_code == 403

        # DELETE with token should succeed
        token = mint_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, token)
        response = client.delete(
            "/api/web/baz",
            headers={CSRF_HEADER_NAME: token},
        )
        assert response.status_code == 200

    def test_post_auth_logout_protected(self) -> None:
        """POST /auth/logout is protected by CSRF."""
        app, client = _build_app()

        # POST /auth/logout without token should fail
        response = client.post("/auth/logout")
        assert response.status_code == 403

        # POST /auth/logout with token should succeed
        token = mint_csrf_token()
        client.cookies.set(CSRF_COOKIE_NAME, token)
        response = client.post(
            "/auth/logout",
            headers={CSRF_HEADER_NAME: token},
        )
        assert response.status_code == 200

    def test_head_bypassed(self) -> None:
        """HEAD requests bypass CSRF check."""
        app, client = _build_app()
        # HEAD without CSRF should bypass CSRF check (safe method)
        # It may return 405 if not explicitly handled, but CSRF middleware doesn't block it
        response = client.head("/api/web/foo")
        # If it's not 403, CSRF check was bypassed
        assert response.status_code != 403

    def test_options_bypassed(self) -> None:
        """OPTIONS requests bypass CSRF check."""
        app, client = _build_app()
        # OPTIONS without CSRF should succeed (safe method)
        response = client.options("/api/web/foo")
        assert response.status_code in (200, 204, 405)
