"""async_write_queue table for memory_write_async fire-and-forget pipeline.

Per spec memsys:f28c4dab ratified via memsys:26610ba6 with worker proposal
memsys:80b04691. Two design choices that deviate from spec letter were
explicitly blessed by owner during ratification:

  1. Resulting memory carries created_at = submitted_at (queued-at), NOT
     drain-time, preserving the per-tenant ordering invariant.
  2. Drain runs as a Type=simple long-running daemon, NOT a 1-second
     oneshot timer — oneshot overhead at 1s cadence exceeds the work.
     See deploy/systemd/mem-mcp-async-write-drain.service.

State machine: queued → in_flight → succeeded | failed
- queued: just submitted by memory_write_async; awaiting drain pick.
- in_flight: drain has claimed via FOR UPDATE SKIP LOCKED + state UPDATE.
- succeeded: drain ran memory_write logic; result_memory_id captured.
- failed: drain hit an error; error_code + error_message captured;
  notice queued to caller's tenant via NoticeClient.

Retry: v1 = single-attempt. attempts column defined for future retry
work; v1 implementation does not increment beyond first attempt.

Revision ID: 0028_async_write_queue
Revises: 0027_teams_select_creator
Create Date: 2026-05-31
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0028_async_write_queue"
down_revision = "0027_teams_select_creator"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        sa.text("""
        CREATE TABLE async_write_queue (
            request_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id            UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            team_id              UUID,
            payload              JSONB NOT NULL,
            submitted_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
            state                TEXT NOT NULL DEFAULT 'queued'
                                 CHECK (state IN ('queued','in_flight','succeeded','failed')),
            attempts             INT NOT NULL DEFAULT 0,
            last_attempted_at    TIMESTAMPTZ,
            error_code           TEXT,
            error_message        TEXT,
            completed_at         TIMESTAMPTZ,
            result_memory_id     UUID
        )
    """)
    )

    # Drain query: SELECT ... WHERE state='queued' ORDER BY (tenant_id, submitted_at).
    # Partial index excludes terminal states (succeeded/failed) which accumulate
    # but aren't drained.
    op.execute(
        sa.text("""
        CREATE INDEX async_write_queue_drain_idx
            ON async_write_queue (state, submitted_at)
            WHERE state IN ('queued','in_flight')
    """)
    )

    # Per-tenant ordering observability + caller queue-depth queries.
    op.execute(
        sa.text("""
        CREATE INDEX async_write_queue_tenant_idx
            ON async_write_queue (tenant_id, submitted_at DESC)
    """)
    )

    # RLS: caller sees only their own queue rows. Drain runs as mem_app under
    # tenant_tx (per blessed Decision 3 in proposal 80b04691), so the policy
    # needs to allow drain reads via current_tenant_id GUC like other tables.
    op.execute(sa.text("ALTER TABLE async_write_queue ENABLE ROW LEVEL SECURITY"))
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_select ON async_write_queue
            FOR SELECT TO mem_app
            USING (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    """)
    )
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_insert ON async_write_queue
            FOR INSERT TO mem_app
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
    """)
    )
    # Drain UPDATE: drain runs under system_tx (NO tenant set) for the
    # cross-tenant SELECT...FOR UPDATE SKIP LOCKED, then re-enters tenant_tx
    # for the per-row memory_write logic. The drain UPDATE policy must
    # therefore allow system_tx (empty GUC) too. Express as: allow if
    # tenant_id matches GUC OR GUC is empty (system_tx case).
    op.execute(
        sa.text("""
        CREATE POLICY async_write_queue_update ON async_write_queue
            FOR UPDATE TO mem_app
            USING (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR current_setting('app.current_tenant_id', true) = ''
            )
            WITH CHECK (
                tenant_id = current_setting('app.current_tenant_id', true)::uuid
                OR current_setting('app.current_tenant_id', true) = ''
            )
    """)
    )

    op.execute(sa.text("GRANT SELECT, INSERT, UPDATE, DELETE ON async_write_queue TO mem_app"))


def downgrade() -> None:
    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_update ON async_write_queue"))
    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_insert ON async_write_queue"))
    op.execute(sa.text("DROP POLICY IF EXISTS async_write_queue_select ON async_write_queue"))
    op.execute(sa.text("DROP TABLE IF EXISTS async_write_queue"))
