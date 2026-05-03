"""Unit tests for the tenant-scope linter (T-6.1)."""

from __future__ import annotations

import sys
from pathlib import Path

# Add tools/ to path to import the linter
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "tools"))

from lint_tenant_scope import lint_file, main  # type: ignore[import-not-found]


class TestLintTenantScope:
    """Tests for tenant-scope linter."""

    def test_clean_code_passes(self, tmp_path: Path) -> None:
        """Valid code with tenant_id filter should pass."""
        code = """
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.fetch(
            "SELECT * FROM memories WHERE tenant_id = $1", tenant_id
        )
"""
        test_file = tmp_path / "test_clean.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        assert len(violations) == 0

    def test_missing_tenant_id_flagged(self, tmp_path: Path) -> None:
        """Missing tenant_id filter should be flagged."""
        code = """
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.fetch(
            "SELECT * FROM memories WHERE id = $1", memory_id
        )
"""
        test_file = tmp_path / "test_violation.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        assert len(violations) == 1
        assert violations[0].table == "memories"
        assert violations[0].method == "fetch"

    def test_jobs_directory_exempt(self, tmp_path: Path) -> None:
        """Code in src/mem_mcp/jobs/ should be exempt."""
        jobs_dir = tmp_path / "src" / "mem_mcp" / "jobs"
        jobs_dir.mkdir(parents=True)
        code = """
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.fetch("SELECT * FROM memories WHERE id = $1", memory_id)
"""
        test_file = jobs_dir / "test_job.py"
        test_file.write_text(code)
        # Simulate the path as it would appear in the linter
        violations = lint_file(test_file)
        # Files in jobs/ should not be checked by main(), but lint_file doesn't filter by path
        # So this test verifies behavior when run directly on jobs files
        # The exemption happens in main(), not lint_file
        assert len(violations) == 1  # lint_file doesn't know about exemption

    def test_non_per_tenant_table_ignored(self, tmp_path: Path) -> None:
        """Non-per-tenant tables should be ignored."""
        code = """
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.fetch("SELECT * FROM tenants WHERE id = $1", tenant_id)
"""
        test_file = tmp_path / "test_public.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        assert len(violations) == 0

    def test_join_with_tenant_id_passes(self, tmp_path: Path) -> None:
        """JOIN with tenant_id should pass."""
        code = '''
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.fetch(
            """SELECT * FROM memories m
               JOIN tenants t ON m.tenant_id = t.id
               WHERE m.tenant_id = $1""",
            tenant_id
        )
'''
        test_file = tmp_path / "test_join.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        assert len(violations) == 0

    def test_dynamic_sql_skipped(self, tmp_path: Path) -> None:
        """Dynamic SQL (not string literals) should be skipped."""
        code = """
import asyncpg

async def example(pool: asyncpg.Pool, query: str) -> None:
    async with pool.acquire() as conn:
        await conn.fetch(query_var)
"""
        test_file = tmp_path / "test_dynamic.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        # Dynamic SQL is skipped, not flagged
        assert len(violations) == 0

    def test_planted_regression_in_real_codebase(self, tmp_path: Path) -> None:
        """Insert a violation in a temp directory and verify linter catches it."""
        code = """
import asyncpg

async def bad_query(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "UPDATE feedback SET resolved = true WHERE id = $1", feedback_id
        )
"""
        test_file = tmp_path / "violation.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        assert len(violations) == 1
        assert violations[0].table == "feedback"
        assert "UPDATE feedback" in violations[0].sql_excerpt

    def test_real_codebase_is_clean(self) -> None:
        """Run linter on actual src/mem_mcp, should exit 0."""
        ec = main(["lint_tenant_scope.py", "src/mem_mcp"])
        assert ec == 0

    def test_system_tx_context_allows_missing_tenant_id(self, tmp_path: Path) -> None:
        """Queries inside system_tx should not be flagged."""
        code = """
import asyncpg
from mem_mcp.db import system_tx

async def example(pool: asyncpg.Pool) -> None:
    async with system_tx(pool) as conn:
        await conn.execute(
            "INSERT INTO oauth_clients (id) VALUES ($1)", client_id
        )
"""
        test_file = tmp_path / "test_system_tx.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        # Queries in system_tx are exempted by the linter logic
        assert len(violations) == 0

    def test_tenant_tx_context_requires_tenant_id(self, tmp_path: Path) -> None:
        """Queries inside tenant_tx should still require tenant_id filter."""
        code = """
import asyncpg
from mem_mcp.db import tenant_tx
from uuid import UUID

async def example(pool: asyncpg.Pool, tenant_id: UUID) -> None:
    async with tenant_tx(pool, tenant_id) as conn:
        await conn.execute(
            "UPDATE memories SET archived = true WHERE id = $1", memory_id
        )
"""
        test_file = tmp_path / "test_tenant_tx.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        # tenant_tx doesn't exempt the check; explicit tenant_id filter is still required
        assert len(violations) == 1
        assert violations[0].table == "memories"

    def test_multiple_per_tenant_tables_in_one_query(self, tmp_path: Path) -> None:
        """Query touching multiple per-tenant tables is flagged once."""
        code = '''
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """UPDATE memories SET archived = true
               WHERE id IN (SELECT memory_id FROM feedback)"""
        )
'''
        test_file = tmp_path / "test_multi.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        # Should flag at least one of the tables
        assert len(violations) >= 1
        table_names = {v.table for v in violations}
        assert "memories" in table_names or "feedback" in table_names

    def test_insert_into_per_tenant_without_tenant_id_flagged(self, tmp_path: Path) -> None:
        """INSERT without tenant_id should be flagged."""
        code = '''
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            """INSERT INTO tenant_identities (cognito_sub, email, provider)
               VALUES ($1, $2, $3)"""
        )
'''
        test_file = tmp_path / "test_insert.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        assert len(violations) == 1
        assert violations[0].table == "tenant_identities"

    def test_fetchval_flagged(self, tmp_path: Path) -> None:
        """fetchval() should be checked like fetch()."""
        code = """
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        count = await conn.fetchval("SELECT COUNT(*) FROM audit_log WHERE severity = $1", 'error')
"""
        test_file = tmp_path / "test_fetchval.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        assert len(violations) == 1
        assert violations[0].method == "fetchval"
        assert violations[0].table == "audit_log"

    def test_fetchrow_flagged(self, tmp_path: Path) -> None:
        """fetchrow() should be checked."""
        code = """
import asyncpg

async def example(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT * FROM web_sessions WHERE token = $1", token)
"""
        test_file = tmp_path / "test_fetchrow.py"
        test_file.write_text(code)
        violations = lint_file(test_file)
        assert len(violations) == 1
        assert violations[0].method == "fetchrow"
        assert violations[0].table == "web_sessions"
