"""Unit tests for the portable kite→memsys client. No network, no AWS —
uses httpx.MockTransport. Travels with kite_memsys_client.py into the kite
repo; run with `pytest docs/integration/test_kite_memsys_client.py`.
"""

from __future__ import annotations

import json

import httpx
import pytest

from kite_memsys_client import (
    ClientCredentialsTokenProvider,
    MemsysAuthError,
    MemsysClient,
    MemsysToolError,
)


def _mcp_ok(result_obj: dict) -> httpx.Response:
    """A JSON-RPC success envelope wrapping result_obj as content[0].text."""
    return httpx.Response(
        200,
        json={
            "jsonrpc": "2.0",
            "id": 1,
            "result": {"content": [{"type": "text", "text": json.dumps(result_obj)}]},
        },
    )


class _StaticToken:
    def __init__(self) -> None:
        self.calls = 0

    def get_token(self) -> str:
        self.calls += 1
        return "test-token"


def _client(handler) -> tuple[MemsysClient, _StaticToken]:
    tok = _StaticToken()
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return MemsysClient("http://mem", tok, http=http), tok


def test_memory_write_builds_tools_call_envelope_and_unwraps_result():
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers["authorization"]
        seen["body"] = json.loads(req.content)
        return _mcp_ok({"id": "mem-123", "embedding_status": "success"})

    client, _ = _client(handler)
    out = client.memory_write("hello", type="note", tags=["kite"])

    assert seen["url"] == "http://mem/mcp"
    assert seen["auth"] == "Bearer test-token"
    assert seen["body"]["method"] == "tools/call"
    assert seen["body"]["params"]["name"] == "memory_write"
    assert seen["body"]["params"]["arguments"] == {
        "content": "hello",
        "type": "note",
        "tags": ["kite"],
    }
    assert out == {"id": "mem-123", "embedding_status": "success"}


def test_memory_search_passes_query_and_limit():
    def handler(req: httpx.Request) -> httpx.Response:
        args = json.loads(req.content)["params"]["arguments"]
        assert args == {"query": "nifty", "limit": 5}
        return _mcp_ok({"results": []})

    client, _ = _client(handler)
    assert client.memory_search("nifty", limit=5) == {"results": []}


def test_jsonrpc_error_envelope_raises_tool_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": -32000, "message": "insufficient scope", "data": {}},
            },
        )

    client, _ = _client(handler)
    with pytest.raises(MemsysToolError) as exc:
        client.memory_write("x")
    assert exc.value.code == -32000


def test_401_triggers_one_refresh_and_retry():
    calls = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(401, text="expired")
        return _mcp_ok({"id": "after-retry"})

    tok_calls = {"n": 0}

    class RefreshableToken:
        def get_token(self) -> str:
            tok_calls["n"] += 1
            return f"tok-{tok_calls['n']}"

        def invalidate(self) -> None:
            pass  # get_token already returns a fresh value each call

    http = httpx.Client(transport=httpx.MockTransport(handler))
    client = MemsysClient("http://mem", RefreshableToken(), http=http)
    out = client.memory_write("x")

    assert calls["n"] == 2  # first 401, retried once
    assert out == {"id": "after-retry"}


def test_persistent_401_raises_auth_error():
    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(401, text="nope")

    client, _ = _client(handler)
    with pytest.raises(MemsysAuthError):
        client.memory_write("x")


def test_client_credentials_token_is_cached_until_leeway():
    fake_time = {"t": 1000.0}
    hits = {"n": 0}

    def handler(req: httpx.Request) -> httpx.Response:
        hits["n"] += 1
        assert "grant_type=client_credentials" in req.content.decode()
        return httpx.Response(200, json={"access_token": "abc", "expires_in": 3600})

    http = httpx.Client(transport=httpx.MockTransport(handler))
    provider = ClientCredentialsTokenProvider(
        token_url="http://auth/oauth2/token",
        client_id="cid",
        client_secret="sec",
        scopes="s/memory.read s/memory.write",
        http=http,
        leeway_seconds=300,
        clock=lambda: fake_time["t"],
    )

    assert provider.get_token() == "abc"
    fake_time["t"] += 100  # well within (3600 - 300) leeway window
    assert provider.get_token() == "abc"
    assert hits["n"] == 1  # cached, no second network call

    fake_time["t"] += 3600  # now past expiry → refetch
    assert provider.get_token() == "abc"
    assert hits["n"] == 2
