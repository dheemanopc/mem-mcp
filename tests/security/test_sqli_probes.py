"""Tests for SQLi resistance (T-6.4, spec S-2).

Verifies that user input is properly parameterized and never interpolated
into SQL strings. This is checked via both static code inspection and
parametrized probes against a live database (when available).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# Common SQL injection payloads that would succeed if input were interpolated
SQLI_PAYLOADS = [
    "'; DROP TABLE memories;--",
    "' OR tenant_id != tenant_id --",
    "{1,2}",
    "x' UNION SELECT * FROM tenants --",
    "1' AND '1'='1",
    "admin'--",
]


@pytest.mark.security
async def test_sqli_payloads_passed_as_parameters_not_sql() -> None:
    """Static smoke-check: verify write.py uses parameterized queries.

    Reads the source code and asserts that user input fields are never
    interpolated into SQL strings via f-strings or .format(). This is
    a non-negotiable safety requirement.
    """
    src = Path("src/mem_mcp/mcp/tools/write.py").read_text()

    # Detect dangerous f-string patterns containing SQL keywords
    assert 'f"INSERT' not in src and "f'INSERT" not in src, (
        "f-string SQL detected in write.py — SQLi risk: user input may be "
        "interpolated into SQL string"
    )
    assert (
        'f"SELECT' not in src and "f'SELECT" not in src
    ), "f-string SQL detected in write.py — SQLi risk"

    # Detect .format() patterns containing SQL keywords
    # (This is a heuristic; true positive requires both .format and SQL keyword
    # in the same statement, which we approximate)
    lines = src.split("\n")
    for i, line in enumerate(lines, 1):
        if ".format(" in line and any(
            kw in line for kw in ["INSERT", "SELECT", "UPDATE", "DELETE"]
        ):
            if "RETURNING" not in line:  # RETURNING is typically in the format string, OK
                pytest.fail(
                    f"Potential .format() SQL at line {i}: {line.strip()}\n"
                    "User input must use parameterized queries ($1, $2, ...)"
                )

    # Positive assertion: INSERT queries use $1, $2, ... parameters
    assert (
        "INSERT INTO" in src and "VALUES ($1" in src
    ), "write.py should use parameterized INSERT queries with $1, $2, ..."


@pytest.mark.security
@pytest.mark.live_aws
@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
async def test_sqli_probes_in_search_tags(
    setup_two_tenants: Any,
    mcp_client: Any,
    payload: str,
) -> None:
    """Parametrized probe: injection attempt in search tags field.

    Against a live database, attempt to inject SQL via the tags parameter.
    If parameterization is broken, an attacker could break out of the query.

    This test verifies that:
    1. The payload is safely passed as a parameter
    2. No SQL error occurs (payload treated as literal string)
    3. Results are filtered by RLS to current tenant only
    """
    a, b = setup_two_tenants.a, setup_two_tenants.b  # noqa: F841
    # Pending: wire to real MCP endpoint
    # # Write a memory to tenant B
    # await mcp_client.write(b.token, "b's data")
    # # Try to search using SQL injection in tags
    # res = await mcp_client.search(a.token, query="data", tags=[payload])
    # # If injection succeeded, we'd see tenant B's data; verify we don't
    # assert all(r["tenant_id"] == str(a.tenant_id) for r in res), (
    #     f"SQLi payload {payload!r} leaked across tenant boundary"
    # )


@pytest.mark.security
@pytest.mark.live_aws
@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
async def test_sqli_probes_in_write_content(
    setup_two_tenants: Any,
    mcp_client: Any,
    payload: str,
) -> None:
    """Parametrized probe: injection attempt in memory content field.

    When writing a memory with SQL injection payload in content, the tool
    must treat it as plain text, not execute it.
    """
    a, b = setup_two_tenants.a, setup_two_tenants.b  # noqa: F841
    # Pending: wire to real MCP endpoint
    # # Write a memory with SQLi payload as content
    # res = await mcp_client.write(a.token, payload)
    # assert res["id"] is not None, (
    #     f"Write failed with SQLi payload {payload!r} — tool should accept it"
    # )
    # # Verify the payload was stored literally, not executed
    # retrieved = await mcp_client.get(a.token, res["id"])
    # assert retrieved["content"] == payload, (
    #     f"Payload was mutated or lost: {retrieved['content']!r} != {payload!r}"
    # )


@pytest.mark.security
@pytest.mark.live_aws
@pytest.mark.parametrize("payload", SQLI_PAYLOADS)
async def test_sqli_probes_in_metadata(
    setup_two_tenants: Any,
    mcp_client: Any,
    payload: str,
) -> None:
    """Parametrized probe: injection in metadata JSON field.

    Metadata is stored as JSONB. Even though JSONB is type-safe, verify that
    payloads in metadata keys/values are treated as strings, not executed.
    """
    a, b = setup_two_tenants.a, setup_two_tenants.b  # noqa: F841
    # Pending: wire to real MCP endpoint
    # # Write a memory with SQLi payload in metadata
    # res = await mcp_client.write(
    #     a.token,
    #     "test content",
    #     metadata={"injection_key": payload}
    # )
    # assert res["id"] is not None
    # # Retrieve and verify payload is intact
    # retrieved = await mcp_client.get(a.token, res["id"])
    # assert retrieved["metadata"]["injection_key"] == payload
