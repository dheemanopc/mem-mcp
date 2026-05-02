"""JSON-RPC 2.0 error model for mem-mcp tools.

Per spec §9.4:
  -32602  validation errors (data.errors=[{path, message}])
  -32000  application errors (insufficient_scope | quota_exceeded | rate_limited |
                              account_suspended | account_deletion_pending |
                              embedding_unavailable | unauthorized_client)
  -32603  server errors (safe message; full stack only in CloudWatch)

JSON-RPC -32600 (invalid request) and -32601 (method not found) are
constructed inline by the transport.
"""

from __future__ import annotations

from typing import Any, Literal

JsonRpcErrorCode = Literal[
    -32700,  # parse_error (transport-level)
    -32600,  # invalid_request
    -32601,  # method_not_found
    -32602,  # invalid_params (Pydantic-driven)
    -32603,  # internal_error (server bug)
    -32000,  # application_error (with .data.code distinguishing)
]


class JsonRpcError(Exception):
    """Typed exception bridge to JSON-RPC error envelopes.

    Tool implementations raise this; the transport catches and serializes.
    """

    def __init__(
        self,
        code: JsonRpcErrorCode,
        message: str,
        *,
        data: dict[str, Any] | None = None,
    ) -> None:
        self.code: JsonRpcErrorCode = code
        self.message = message
        self.data = data
        super().__init__(message)

    def to_envelope(self, request_id: str | int | None) -> dict[str, Any]:
        envelope: dict[str, Any] = {
            "jsonrpc": "2.0",
            "id": request_id,
            "error": {"code": self.code, "message": self.message},
        }
        if self.data is not None:
            envelope["error"]["data"] = self.data
        return envelope


def to_jsonrpc_error_response(
    request_id: str | int | None,
    code: JsonRpcErrorCode,
    message: str,
    *,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a JSON-RPC error envelope. Convenience wrapper around JsonRpcError.to_envelope."""
    return JsonRpcError(code, message, data=data).to_envelope(request_id)
