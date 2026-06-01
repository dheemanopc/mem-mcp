"""Introduce can_access_team_resource() helper for write-time access checks.

This closes the read/write asymmetry caught in memsys bug fab23ec5 (PMO DO
surfacing bf7b3379): the write-time references-validator (and its sibling
filter sites) gated access on user_effective_team_access membership only,
with no same-tenant escape clause. The read path (memories_select RLS,
migration 0033) has TWO OR-clauses — same-tenant first, then team-membership
second. After migration 0026 backfilled memories.team_id non-NULL on every
row, the validator's UEA-only gate fires universally and rejects writes
against own-tenant memories whose team isn't in the caller's UEA.

This helper codifies the same predicate as the read RLS: same-tenant OR
team-membership. Every write-time access check that previously did
``SELECT 1 FROM user_effective_team_access WHERE user_id=$1 AND
resource_team_id=$2'' should call this helper instead, passing the target
memory's tenant_id alongside its team_id.

The team-membership clause uses UEA (DAG-reachable per Spec 5) rather than
current_user_active_team_ids() (direct team_members membership) so the
writer is strictly more permissive than the reader on the second clause.
Per DA ratification f74663a6 §A(2): this is acceptable — the writer must
not be stricter than the reader, and eventual convergence (reader adopting
UEA) is tracked as a separate follow-up.

Strategy mirrors safe_current_tenant_id() from migration 0034:
- LANGUAGE sql STABLE SECURITY DEFINER
- SET search_path = public
- Explicit GRANT EXECUTE to mem_app (NOT PUBLIC — per Reviewer §C-amend-1)

Revision ID: 0035_can_access_team_resource
Revises: 0034_safe_current_tenant_id
Create Date: 2026-06-01
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0035_can_access_team_resource"
down_revision = "0034_safe_current_tenant_id"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # The p_resource_team IS NULL clause is legacy pre-0026 backwards-compat;
    # post-0026 no memory has team_id IS NULL at write time, so this branch
    # is unreachable via the normal write path. Kept for defence-in-depth
    # and for the LEFT JOIN fallback in get_outbound_refs / get_inbound_refs
    # when a target/source memory has been hard-deleted.
    #
    # GRANT scope is mem_app-only; tighten further if mem_readonly is ever
    # introduced and needs to call this.
    op.execute(
        sa.text("""
CREATE OR REPLACE FUNCTION can_access_team_resource(
    p_caller_tenant uuid,
    p_resource_tenant uuid,
    p_resource_team uuid
) RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = public
AS $$
    SELECT
        p_resource_team IS NULL
        OR p_caller_tenant = p_resource_tenant
        OR EXISTS (
            SELECT 1 FROM user_effective_team_access
             WHERE user_id = p_caller_tenant
               AND resource_team_id = p_resource_team
        )
$$;
        """)
    )
    op.execute(
        sa.text(
            "GRANT EXECUTE ON FUNCTION "
            "can_access_team_resource(uuid, uuid, uuid) TO mem_app"
        )
    )


def downgrade() -> None:
    op.execute(
        sa.text("DROP FUNCTION IF EXISTS can_access_team_resource(uuid, uuid, uuid)")
    )
