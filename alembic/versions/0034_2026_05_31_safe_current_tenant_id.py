"""Introduce safe_current_tenant_id() and swap every bare GUC-cast site.

This closes the systemic RLS-cast cluster documented in memsys:407fbf20
and re-confirmed in memsys:96464537. The recurring defect:

  current_setting('app.current_tenant_id', true)::uuid

crashes with `invalid input syntax for type uuid: ""` whenever the GUC
holds the empty string — which happens on any mem_app pool connection
recycled from a previously rolled-back tenant_tx (PG custom-GUC quirk:
SET LOCAL + ROLLBACK resets to '', not NULL).

Earlier fixes patched individual sites (migrations 0019, 0029, 0031,
0033) but each one required catching the bug in flight. The cluster
audit on prod 2026-05-31 found 18 bare-cast policies still live across
8 tables — none currently bitten, but all latent.

Strategy: single SECURITY DEFINER helper everyone uses.

  CREATE FUNCTION safe_current_tenant_id() RETURNS uuid
    AS $$ SELECT NULLIF(current_setting(...), '')::uuid $$;

Replace every `current_setting('app.current_tenant_id', true)::uuid`
with `safe_current_tenant_id()`. Now:
- Any future RLS policy author writes `tenant_id = safe_current_tenant_id()`
  by reflex; the bare-cast pattern dies.
- A grep for `current_setting.*tenant_id.*::uuid` outside this helper
  becomes a lint signal — anyone reintroducing the bare-cast is visible.

Sites swept (from prod pg_policy on 2026-05-31):
  async_write_queue_insert
  kite_intents (select/insert/update/delete)
  memories_insert / memories_update / memories_delete
  memory_clusters (select/insert/delete)
  team_members_select / team_members_insert / team_members_delete
  teams_insert / teams_select
  tenant_daily_usage (ALL, USING + WITH CHECK)
  tenant_skill_credentials (ALL, USING + WITH CHECK)

Policies already NULLIF-wrapped by prior migrations are left untouched:
  memories_select, async_write_queue_select, async_write_queue_update
  current_user_active_team_ids, current_user_admin_team_ids (fns)

Revision ID: 0034_safe_current_tenant_id
Revises: 0033_nullif_memories_rls
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0034_safe_current_tenant_id"
down_revision = "0033_nullif_memories_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Helper. STABLE because GUC doesn't change inside a statement.
    # SECURITY DEFINER so callers don't need any special grants.
    # Owned by the migration role; runs as that role; grants EXECUTE to PUBLIC
    # so RLS policies on tables with no TO clause (PUBLIC-applicable) still work.
    op.execute(
        sa.text("""
CREATE OR REPLACE FUNCTION safe_current_tenant_id() RETURNS uuid
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT NULLIF(current_setting('app.current_tenant_id', true), '')::uuid
$$;
        """)
    )
    op.execute(sa.text("GRANT EXECUTE ON FUNCTION safe_current_tenant_id() TO PUBLIC"))

    # ---- async_write_queue ----
    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_insert ON async_write_queue"))
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_insert ON async_write_queue
            FOR INSERT TO mem_app
            WITH CHECK (tenant_id = safe_current_tenant_id())
        """)
    )

    # ---- kite_intents (4 policies, applied to PUBLIC) ----
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_select ON kite_intents"))
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_select ON kite_intents
            FOR SELECT
            USING (tenant_id = safe_current_tenant_id())
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_insert ON kite_intents"))
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_insert ON kite_intents
            FOR INSERT
            WITH CHECK (tenant_id = safe_current_tenant_id())
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_update ON kite_intents"))
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_update ON kite_intents
            FOR UPDATE
            USING (tenant_id = safe_current_tenant_id())
            WITH CHECK (tenant_id = safe_current_tenant_id())
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_delete ON kite_intents"))
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_delete ON kite_intents
            FOR DELETE
            USING (tenant_id = safe_current_tenant_id())
        """)
    )

    # ---- memories (insert, update, delete — select already done in 0033) ----
    op.execute(sa.text("DROP POLICY IF EXISTS memories_insert ON memories"))
    op.execute(
        sa.text("""
        CREATE POLICY memories_insert ON memories
            FOR INSERT TO mem_app
            WITH CHECK (tenant_id = safe_current_tenant_id())
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS memories_update ON memories"))
    op.execute(
        sa.text("""
        CREATE POLICY memories_update ON memories
            FOR UPDATE TO mem_app
            USING (tenant_id = safe_current_tenant_id())
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS memories_delete ON memories"))
    op.execute(
        sa.text("""
        CREATE POLICY memories_delete ON memories
            FOR DELETE TO mem_app
            USING (tenant_id = safe_current_tenant_id())
        """)
    )

    # ---- memory_clusters (3 policies, applied to PUBLIC) ----
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_select ON memory_clusters"))
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_select ON memory_clusters
            FOR SELECT
            USING (tenant_id = safe_current_tenant_id())
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_insert ON memory_clusters"))
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_insert ON memory_clusters
            FOR INSERT
            WITH CHECK (tenant_id = safe_current_tenant_id())
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_delete ON memory_clusters"))
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_delete ON memory_clusters
            FOR DELETE
            USING (tenant_id = safe_current_tenant_id())
        """)
    )

    # ---- team_members (3 policies, mem_app, preserving OR clauses) ----
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_select ON team_members"))
    op.execute(
        sa.text("""
        CREATE POLICY team_members_select ON team_members
            FOR SELECT TO mem_app
            USING (
                tenant_id = safe_current_tenant_id()
                OR team_id = ANY (current_user_active_team_ids())
            )
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_insert ON team_members"))
    op.execute(
        sa.text("""
        CREATE POLICY team_members_insert ON team_members
            FOR INSERT TO mem_app
            WITH CHECK (
                added_by_tenant_id = safe_current_tenant_id()
                AND team_id = ANY (current_user_admin_team_ids())
            )
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_delete ON team_members"))
    op.execute(
        sa.text("""
        CREATE POLICY team_members_delete ON team_members
            FOR DELETE TO mem_app
            USING (
                tenant_id = safe_current_tenant_id()
                OR team_id = ANY (current_user_admin_team_ids())
            )
        """)
    )

    # ---- teams (2 policies, mem_app, preserving complex expressions) ----
    op.execute(sa.text("DROP POLICY IF EXISTS teams_insert ON teams"))
    op.execute(
        sa.text("""
        CREATE POLICY teams_insert ON teams
            FOR INSERT TO mem_app
            WITH CHECK (created_by_tenant_id = safe_current_tenant_id())
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS teams_select ON teams"))
    op.execute(
        sa.text("""
        CREATE POLICY teams_select ON teams
            FOR SELECT TO mem_app
            USING (
                deleted_at IS NULL
                AND (
                    id = ANY (current_user_active_team_ids())
                    OR created_by_tenant_id = safe_current_tenant_id()
                )
            )
        """)
    )

    # ---- tenant_daily_usage (single ALL policy, USING + WITH CHECK, PUBLIC) ----
    op.execute(sa.text("DROP POLICY IF EXISTS usage_tenant_isolation ON tenant_daily_usage"))
    op.execute(
        sa.text("""
        CREATE POLICY usage_tenant_isolation ON tenant_daily_usage
            USING (tenant_id = safe_current_tenant_id())
            WITH CHECK (tenant_id = safe_current_tenant_id())
        """)
    )

    # ---- tenant_skill_credentials (single ALL policy, USING + WITH CHECK, PUBLIC) ----
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS tenant_skill_credentials_tenant_isolation "
            "ON tenant_skill_credentials"
        )
    )
    op.execute(
        sa.text("""
        CREATE POLICY tenant_skill_credentials_tenant_isolation ON tenant_skill_credentials
            USING (tenant_id = safe_current_tenant_id())
            WITH CHECK (tenant_id = safe_current_tenant_id())
        """)
    )


def downgrade() -> None:
    # Revert each policy to the bare-cast form (matches prod state at 2026-05-31
    # pre-migration). Helper function is dropped last. This is destructive —
    # any future policies that have started using the helper will break. Do NOT
    # run downgrade without auditing those callers.

    # async_write_queue_insert
    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_insert ON async_write_queue"))
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_insert ON async_write_queue
            FOR INSERT TO mem_app
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    # kite_intents
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_select ON kite_intents"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_select ON kite_intents FOR SELECT "
            "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_insert ON kite_intents"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_insert ON kite_intents FOR INSERT "
            "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_update ON kite_intents"))
    op.execute(
        sa.text("""
        CREATE POLICY tenant_isolation_update ON kite_intents FOR UPDATE
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_delete ON kite_intents"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_delete ON kite_intents FOR DELETE "
            "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )

    # memories
    op.execute(sa.text("DROP POLICY IF EXISTS memories_insert ON memories"))
    op.execute(
        sa.text(
            "CREATE POLICY memories_insert ON memories FOR INSERT TO mem_app "
            "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS memories_update ON memories"))
    op.execute(
        sa.text(
            "CREATE POLICY memories_update ON memories FOR UPDATE TO mem_app "
            "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS memories_delete ON memories"))
    op.execute(
        sa.text(
            "CREATE POLICY memories_delete ON memories FOR DELETE TO mem_app "
            "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )

    # memory_clusters
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_select ON memory_clusters"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_select ON memory_clusters FOR SELECT "
            "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_insert ON memory_clusters"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_insert ON memory_clusters FOR INSERT "
            "WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS tenant_isolation_delete ON memory_clusters"))
    op.execute(
        sa.text(
            "CREATE POLICY tenant_isolation_delete ON memory_clusters FOR DELETE "
            "USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)"
        )
    )

    # team_members
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_select ON team_members"))
    op.execute(
        sa.text("""
        CREATE POLICY team_members_select ON team_members FOR SELECT TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR team_id = ANY (current_user_active_team_ids())
            )
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_insert ON team_members"))
    op.execute(
        sa.text("""
        CREATE POLICY team_members_insert ON team_members FOR INSERT TO mem_app
            WITH CHECK (
                added_by_tenant_id = current_setting('app.current_tenant_id', true)::uuid
                AND team_id = ANY (current_user_admin_team_ids())
            )
        """)
    )
    op.execute(sa.text("DROP POLICY IF EXISTS team_members_delete ON team_members"))
    op.execute(
        sa.text("""
        CREATE POLICY team_members_delete ON team_members FOR DELETE TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR team_id = ANY (current_user_admin_team_ids())
            )
        """)
    )

    # teams
    op.execute(sa.text("DROP POLICY IF EXISTS teams_insert ON teams"))
    op.execute(
        sa.text(
            "CREATE POLICY teams_insert ON teams FOR INSERT TO mem_app "
            "WITH CHECK (created_by_tenant_id = "
            "current_setting('app.current_tenant_id', true)::uuid)"
        )
    )
    op.execute(sa.text("DROP POLICY IF EXISTS teams_select ON teams"))
    op.execute(
        sa.text("""
        CREATE POLICY teams_select ON teams FOR SELECT TO mem_app
            USING (
                deleted_at IS NULL
                AND (
                    id = ANY (current_user_active_team_ids())
                    OR created_by_tenant_id =
                        current_setting('app.current_tenant_id', true)::uuid
                )
            )
        """)
    )

    # tenant_daily_usage
    op.execute(sa.text("DROP POLICY IF EXISTS usage_tenant_isolation ON tenant_daily_usage"))
    op.execute(
        sa.text("""
        CREATE POLICY usage_tenant_isolation ON tenant_daily_usage
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    # tenant_skill_credentials
    op.execute(
        sa.text(
            "DROP POLICY IF EXISTS tenant_skill_credentials_tenant_isolation "
            "ON tenant_skill_credentials"
        )
    )
    op.execute(
        sa.text("""
        CREATE POLICY tenant_skill_credentials_tenant_isolation ON tenant_skill_credentials
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    # Helper dropped last.
    op.execute(sa.text("DROP FUNCTION IF EXISTS safe_current_tenant_id()"))
