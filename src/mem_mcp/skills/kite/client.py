"""Async Kite Connect REST client.

Thin wrapper over httpx.AsyncClient that knows the auth-header shape and
Kite's standard response envelope (``{"status": ..., "data": ..., "message": ...}``).

Per-call credentials are taken as explicit parameters (api_key + access_token)
so the same client instance is safe to use across tenants.
"""

from __future__ import annotations

import csv
import io
from collections.abc import Mapping
from typing import Any, cast

import httpx

from mem_mcp.skills.kite.auth import KITE_API_BASE_URL


class KiteApiError(Exception):
    """Raised when Kite returns status=error or a non-200 HTTP code."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        error_type: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.error_type = error_type


class KiteClient:
    """Async wrapper around the Kite Connect REST API.

    Construct without args for production (lazy per-call httpx clients) or with
    an injected ``http`` for tests.
    """

    def __init__(
        self,
        http: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
        base_url: str = KITE_API_BASE_URL,
    ) -> None:
        self._injected_http = http
        self._timeout = timeout
        self._base_url = base_url.rstrip("/")

    @staticmethod
    def _auth_headers(api_key: str, access_token: str) -> dict[str, str]:
        return {
            "X-Kite-Version": "3",
            "Authorization": f"token {api_key}:{access_token}",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        access_token: str,
        params: list[tuple[str, str]] | Mapping[str, str] | None = None,
        data: Mapping[str, Any] | None = None,
    ) -> Any:
        url = f"{self._base_url}{path}"
        headers = self._auth_headers(api_key, access_token)

        async def _send(client: httpx.AsyncClient) -> Any:
            resp = await client.request(
                method,
                url,
                headers=headers,
                params=params,  # type: ignore[arg-type]
                data=data,
                timeout=self._timeout,
            )
            try:
                body = resp.json()
            except Exception as exc:
                raise KiteApiError(
                    f"non-JSON response from Kite ({method} {path}, status {resp.status_code})",
                    status_code=resp.status_code,
                ) from exc
            if resp.status_code != 200 or body.get("status") != "success":
                raise KiteApiError(
                    body.get("message", f"Kite error ({method} {path}, status {resp.status_code})"),
                    status_code=resp.status_code,
                    error_type=body.get("error_type"),
                )
            return body.get("data")

        if self._injected_http is not None:
            return await _send(self._injected_http)
        async with httpx.AsyncClient() as client:
            return await _send(client)

    async def _request_text(
        self,
        method: str,
        path: str,
        *,
        api_key: str,
        access_token: str,
        params: list[tuple[str, str]] | Mapping[str, str] | None = None,
    ) -> str:
        """Make a request and return raw text (for CSV responses)."""
        url = f"{self._base_url}{path}"
        headers = self._auth_headers(api_key, access_token)

        async def _send(client: httpx.AsyncClient) -> str:
            resp = await client.request(
                method,
                url,
                headers=headers,
                params=params,  # type: ignore[arg-type]
                timeout=self._timeout,
            )
            if resp.status_code != 200:
                raise KiteApiError(
                    f"Kite error ({method} {path}, status {resp.status_code})",
                    status_code=resp.status_code,
                )
            return resp.text

        if self._injected_http is not None:
            return await _send(self._injected_http)
        async with httpx.AsyncClient() as client:
            return await _send(client)

    # ---------- portfolio ----------

    async def get_holdings(self, *, api_key: str, access_token: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request(
                "GET", "/portfolio/holdings", api_key=api_key, access_token=access_token
            ),
        )

    async def get_positions(self, *, api_key: str, access_token: str) -> dict[str, Any]:
        return cast(
            dict[str, Any],
            await self._request(
                "GET", "/portfolio/positions", api_key=api_key, access_token=access_token
            ),
        )

    # ---------- user / margins ----------

    async def get_margins(
        self, *, api_key: str, access_token: str, segment: str | None = None
    ) -> dict[str, Any]:
        path = "/user/margins" if segment is None else f"/user/margins/{segment}"
        return cast(
            dict[str, Any],
            await self._request("GET", path, api_key=api_key, access_token=access_token),
        )

    # ---------- market data ----------

    async def get_quote(
        self, *, api_key: str, access_token: str, instruments: list[str]
    ) -> dict[str, Any]:
        params: list[tuple[str, str]] = [("i", inst) for inst in instruments]
        return cast(
            dict[str, Any],
            await self._request(
                "GET", "/quote", api_key=api_key, access_token=access_token, params=params
            ),
        )

    async def get_historical_data(
        self,
        *,
        api_key: str,
        access_token: str,
        instrument_token: int,
        interval: str,
        from_date: str,
        to_date: str,
        continuous: bool = False,
        oi: bool = False,
    ) -> dict[str, Any]:
        path = f"/instruments/historical/{instrument_token}/{interval}"
        params: dict[str, str] = {"from": from_date, "to": to_date}
        if continuous:
            params["continuous"] = "1"
        if oi:
            params["oi"] = "1"
        return cast(
            dict[str, Any],
            await self._request(
                "GET", path, api_key=api_key, access_token=access_token, params=params
            ),
        )

    async def get_instruments(
        self, *, api_key: str, access_token: str, exchange: str = "NSE"
    ) -> list[dict[str, Any]]:
        """Fetch instruments master for given exchange.

        Returns CSV parsed as list of dicts with at minimum:
        - instrument_token (int)
        - tradingsymbol (str)
        - exchange (str)
        and other Kite standard columns.
        """
        path = f"/instruments/{exchange}"
        text = await self._request_text("GET", path, api_key=api_key, access_token=access_token)

        # Parse CSV
        reader = csv.DictReader(io.StringIO(text))
        result = []
        for row in reader:
            # Cast instrument_token to int
            if "instrument_token" in row:
                try:
                    row["instrument_token"] = int(row["instrument_token"])
                except (ValueError, TypeError):
                    pass  # Leave as string if conversion fails
            result.append(row)

        return result

    # ---------- orders ----------

    async def get_orders(self, *, api_key: str, access_token: str) -> list[dict[str, Any]]:
        return cast(
            list[dict[str, Any]],
            await self._request("GET", "/orders", api_key=api_key, access_token=access_token),
        )

    async def place_order(
        self,
        *,
        api_key: str,
        access_token: str,
        variety: str,
        order_params: dict[str, Any],
    ) -> dict[str, Any]:
        """variety is one of: regular, amo, co, iceberg, auction.

        order_params is a flat dict with Kite's form fields:
        tradingsymbol, exchange, transaction_type, quantity, product,
        order_type, price, validity, trigger_price, etc.
        """
        path = f"/orders/{variety}"
        return cast(
            dict[str, Any],
            await self._request(
                "POST",
                path,
                api_key=api_key,
                access_token=access_token,
                data=order_params,
            ),
        )

    async def cancel_order(
        self,
        *,
        api_key: str,
        access_token: str,
        variety: str,
        order_id: str,
    ) -> dict[str, Any]:
        path = f"/orders/{variety}/{order_id}"
        return cast(
            dict[str, Any],
            await self._request("DELETE", path, api_key=api_key, access_token=access_token),
        )
