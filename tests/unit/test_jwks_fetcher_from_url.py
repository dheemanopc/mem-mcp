"""Tests for HttpxJwksFetcher.from_url classmethod.

JF-1/JF-2: constructs from an arbitrary URL without region/pool_id; the
internal fetch() still works against that URL.
"""

from __future__ import annotations

from mem_mcp.auth.jwks import HttpxJwksFetcher


class TestHttpxJwksFetcherFromUrl:
    def test_jf1_from_url_sets_url(self) -> None:
        f = HttpxJwksFetcher.from_url("http://example.com/.well-known/jwks.json")
        assert f.url == "http://example.com/.well-known/jwks.json"
        assert f.timeout_seconds == 5.0

    def test_jf2_from_url_custom_timeout(self) -> None:
        f = HttpxJwksFetcher.from_url("http://x/y", timeout_seconds=10.0)
        assert f.url == "http://x/y"
        assert f.timeout_seconds == 10.0

    def test_from_url_doesnt_run_init(self) -> None:
        """Regression: __init__ takes region+user_pool_id; from_url bypasses it.

        Calling from_url should NOT raise even though the named init args
        aren't supplied.
        """
        # No exception expected
        HttpxJwksFetcher.from_url("http://x/y")

    def test_legacy_init_still_works(self) -> None:
        """Lock that the AWS URL builder path is untouched."""
        f = HttpxJwksFetcher(region="ap-south-1", user_pool_id="ap-south-1_TESTPOOL")
        assert "cognito-idp.ap-south-1.amazonaws.com" in f.url
        assert "ap-south-1_TESTPOOL/.well-known/jwks.json" in f.url
