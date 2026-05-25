"""Team management handlers: CRUD operations, member management, invites."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastapi import APIRouter, Cookie, HTTPException
from pydantic import BaseModel, Field

from mem_mcp.db.tenant_tx import system_tx
from mem_mcp.web.sessions import lookup_session

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]


class TeamCreateBody(BaseModel):
    """Request body for POST /api/web/teams."""

    name: str = Field(..., min_length=1, max_length=255)
    workspace_domain: str | None = None


class TeamPatchBody(BaseModel):
    """Request body for PATCH /api/web/teams/{id}."""

    name: str = Field(..., min_length=1, max_length=255)


class MemberAddBody(BaseModel):
    """Request body for POST /api/web/teams/{id}/members."""

    tenant_id: UUID | None = None
    invited_email: str | None = None
    role: str = Field(default="member", pattern="^(admin|member|viewer)$")


class MemberPatchBody(BaseModel):
    """Request body for PATCH /api/web/teams/{id}/members/{tenant_id}."""

    role: str = Field(..., pattern="^(admin|member|viewer)$")


def make_teams_router(
    *,
    pool: asyncpg.Pool,
    audit: Any,  # AuditLogger Protocol
) -> APIRouter:
    """Factory for teams router."""
    router = APIRouter()

    @router.post("/api/web/teams")
    async def create_team(
        body: TeamCreateBody,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Create a new team.

        Workspace users with workspace_domain set can only create workspace teams.
        Personal users can only create personal teams.
        """
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        async with system_tx(pool) as conn:
            # Get caller's identity info
            identity = await conn.fetchrow(
                "SELECT workspace_domain FROM tenant_identities WHERE tenant_id = $1 AND is_primary",
                sess.tenant_id,
            )
            if not identity:
                raise HTTPException(status_code=400, detail="no primary identity")

            caller_workspace_domain = identity["workspace_domain"]

            # Resolve workspace_domain for the team
            team_workspace_domain = body.workspace_domain
            if caller_workspace_domain:
                # Workspace user must match or omit domain
                if (
                    body.workspace_domain is not None
                    and body.workspace_domain != caller_workspace_domain
                ):
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "cross_workspace_team_create_denied",
                            "message": "Your workspace domain does not match the requested team domain",
                        },
                    )
                # Auto-set workspace_domain if omitted
                if team_workspace_domain is None:
                    team_workspace_domain = caller_workspace_domain
            else:
                # Personal user cannot create workspace teams
                if body.workspace_domain is not None:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "workspace_team_by_personal_user_denied",
                            "message": "Personal users cannot create workspace teams",
                        },
                    )

            # Create team
            team = await conn.fetchrow(
                """
                INSERT INTO teams (name, workspace_domain, created_by_tenant_id)
                VALUES ($1, $2, $3)
                RETURNING id, name, workspace_domain, created_at
                """,
                body.name,
                team_workspace_domain,
                sess.tenant_id,
            )

            # Add creator as admin member
            await conn.execute(
                """
                INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
                VALUES ($1, $2, $3, $4, $5)
                """,
                team["id"],
                sess.tenant_id,
                "admin",
                "active",
                sess.tenant_id,
            )

        return {
            "team": dict(team),
            "your_role": "admin",
        }

    @router.get("/api/web/teams")
    async def list_teams(
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """List all teams the caller is a member of."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        async with system_tx(pool) as conn:
            rows = await conn.fetch(
                """
                SELECT t.id, t.name, t.workspace_domain, t.created_at,
                       tm.role, tm.added_at
                FROM teams t
                INNER JOIN team_members tm ON t.id = tm.team_id
                WHERE tm.tenant_id = $1 AND tm.status = 'active' AND t.deleted_at IS NULL
                ORDER BY tm.added_at DESC
                """,
                sess.tenant_id,
            )

        teams = [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "workspace_domain": r["workspace_domain"],
                "created_at": r["created_at"].isoformat(),
                "your_role": r["role"],
            }
            for r in rows
        ]

        return {"teams": teams}

    @router.get("/api/web/teams/{team_id}")
    async def get_team(
        team_id: str,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Get team details (RLS-gated; 404 if not a member)."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        try:
            team_uuid = UUID(team_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid team_id") from e

        async with system_tx(pool) as conn:
            # Verify caller is a member
            member_row = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                sess.tenant_id,
            )
            if not member_row:
                raise HTTPException(status_code=404, detail="team not found or not a member")

            team = await conn.fetchrow(
                """
                SELECT id, name, workspace_domain, created_at
                FROM teams
                WHERE id = $1 AND deleted_at IS NULL
                """,
                team_uuid,
            )
            if not team:
                raise HTTPException(status_code=404, detail="team not found")

            # Count active members
            member_count_row = await conn.fetchval(
                """
                SELECT COUNT(*) FROM team_members
                WHERE team_id = $1 AND status = 'active'
                """,
                team_uuid,
            )

        return {
            "team": {
                "id": str(team["id"]),
                "name": team["name"],
                "workspace_domain": team["workspace_domain"],
                "created_at": team["created_at"].isoformat(),
            },
            "your_role": member_row["role"],
            "member_count": member_count_row,
        }

    @router.patch("/api/web/teams/{team_id}")
    async def patch_team(
        team_id: str,
        body: TeamPatchBody,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Update team name (admin only, RLS-gated)."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        try:
            team_uuid = UUID(team_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid team_id") from e

        async with system_tx(pool) as conn:
            # Check admin access
            member_row = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                sess.tenant_id,
            )
            if not member_row or member_row["role"] != "admin":
                raise HTTPException(status_code=403, detail="not authorized")

            updated = await conn.fetchrow(
                """
                UPDATE teams SET name = $1, updated_at = NOW()
                WHERE id = $2 AND deleted_at IS NULL
                RETURNING id, name, workspace_domain, created_at
                """,
                body.name,
                team_uuid,
            )

        return {
            "team": dict(updated),
            "your_role": member_row["role"],
        }

    @router.delete("/api/web/teams/{team_id}")
    async def delete_team(
        team_id: str,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Soft-delete team and orphan its memories (admin only)."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        try:
            team_uuid = UUID(team_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid team_id") from e

        async with system_tx(pool) as conn:
            # Check admin access
            member_row = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                sess.tenant_id,
            )
            if not member_row or member_row["role"] != "admin":
                raise HTTPException(status_code=403, detail="not authorized")

            # Single transaction: orphan memories then soft-delete team
            async with conn.transaction():
                await conn.execute(
                    """
                    UPDATE memories
                    SET visibility = 'private', team_id = NULL
                    WHERE team_id = $1
                    """,
                    team_uuid,
                )
                await conn.execute(
                    """
                    UPDATE teams SET deleted_at = NOW()
                    WHERE id = $1
                    """,
                    team_uuid,
                )

        return {"deleted": True}

    @router.post("/api/web/teams/{team_id}/members")
    async def add_member(
        team_id: str,
        body: MemberAddBody,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Add member to team by tenant_id or email invite."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        try:
            team_uuid = UUID(team_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid team_id") from e

        # Exactly one of tenant_id or invited_email must be provided
        if (body.tenant_id is None and body.invited_email is None) or (
            body.tenant_id is not None and body.invited_email is not None
        ):
            raise HTTPException(
                status_code=400,
                detail="exactly one of tenant_id or invited_email required",
            )

        async with system_tx(pool) as conn:
            # Check caller is admin
            caller_member = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                sess.tenant_id,
            )
            if not caller_member or caller_member["role"] != "admin":
                raise HTTPException(status_code=403, detail="not authorized")

            # Get team info for domain validation
            team = await conn.fetchrow(
                "SELECT workspace_domain FROM teams WHERE id = $1 AND deleted_at IS NULL",
                team_uuid,
            )
            if not team:
                raise HTTPException(status_code=404, detail="team not found")

            if body.tenant_id:
                # Add existing user — validate domain rules
                target_identity = await conn.fetchrow(
                    """
                    SELECT workspace_domain FROM tenant_identities
                    WHERE tenant_id = $1 AND is_primary
                    """,
                    body.tenant_id,
                )
                if not target_identity:
                    raise HTTPException(status_code=400, detail="target user not found")

                target_domain = target_identity["workspace_domain"]
                team_domain = team["workspace_domain"]

                # Validate domain match
                if team_domain:
                    # Workspace team: target must share domain
                    if target_domain != team_domain:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "member_domain_mismatch",
                                "message": f"Member domain {target_domain} does not match team domain {team_domain}",
                            },
                        )
                else:
                    # Personal team: target must have no domain
                    if target_domain is not None:
                        raise HTTPException(
                            status_code=400,
                            detail={
                                "code": "member_has_workspace_domain",
                                "message": "Personal teams cannot include workspace users",
                            },
                        )

                # Insert member
                try:
                    await conn.execute(
                        """
                        INSERT INTO team_members (team_id, tenant_id, role, status, added_by_tenant_id)
                        VALUES ($1, $2, $3, $4, $5)
                        ON CONFLICT DO NOTHING
                        """,
                        team_uuid,
                        body.tenant_id,
                        body.role,
                        "active",
                        sess.tenant_id,
                    )
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"failed to add member: {e}") from e

            else:
                # Email invite — domain validation deferred to sign-in
                try:
                    await conn.execute(
                        """
                        INSERT INTO team_invites (team_id, email, role, invited_by_tenant_id)
                        VALUES ($1, $2, $3, $4)
                        ON CONFLICT (team_id, email) DO UPDATE
                            SET status = 'pending', role = EXCLUDED.role
                        """,
                        team_uuid,
                        (body.invited_email or "").lower(),
                        body.role,
                        sess.tenant_id,
                    )
                except Exception as e:
                    raise HTTPException(
                        status_code=400, detail=f"failed to create invite: {e}"
                    ) from e

        return {"success": True}

    @router.get("/api/web/teams/{team_id}/members")
    async def list_members(
        team_id: str,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """List team members and pending invites."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        try:
            team_uuid = UUID(team_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid team_id") from e

        async with system_tx(pool) as conn:
            # Verify caller is a member
            member_row = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                sess.tenant_id,
            )
            if not member_row:
                raise HTTPException(status_code=404, detail="team not found or not a member")

            # Fetch active members with identity info
            members = await conn.fetch(
                """
                SELECT tm.tenant_id, tm.role, tm.status, tm.added_at,
                       ti.email, ti.provider, ti.id as identity_id
                FROM team_members tm
                LEFT JOIN tenant_identities ti ON tm.tenant_id = ti.tenant_id AND ti.is_primary
                WHERE tm.team_id = $1 AND tm.status = 'active'
                ORDER BY tm.added_at
                """,
                team_uuid,
            )

            # Fetch pending invites
            invites = await conn.fetch(
                """
                SELECT email, role, invited_at, invited_by_tenant_id
                FROM team_invites
                WHERE team_id = $1 AND status = 'pending'
                ORDER BY invited_at
                """,
                team_uuid,
            )

        member_list = [
            {
                "tenant_id": str(m["tenant_id"]),
                "role": m["role"],
                "status": m["status"],
                "added_at": m["added_at"].isoformat(),
                "identity_email": m["email"],
                "provider": m["provider"],
            }
            for m in members
        ]

        invite_list = [
            {
                "email": i["email"],
                "role": i["role"],
                "invited_at": i["invited_at"].isoformat(),
                "invited_by_tenant_id": str(i["invited_by_tenant_id"]),
            }
            for i in invites
        ]

        return {"members": member_list, "invites": invite_list}

    @router.patch("/api/web/teams/{team_id}/members/{target_tenant_id}")
    async def patch_member(
        team_id: str,
        target_tenant_id: str,
        body: MemberPatchBody,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Change member role (admin only)."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        try:
            team_uuid = UUID(team_id)
            target_uuid = UUID(target_tenant_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid team_id or tenant_id") from e

        async with system_tx(pool) as conn:
            # Check caller is admin
            caller_member = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                sess.tenant_id,
            )
            if not caller_member or caller_member["role"] != "admin":
                raise HTTPException(status_code=403, detail="not authorized")

            # Check last admin constraint
            if body.role != "admin":
                admin_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM team_members
                    WHERE team_id = $1 AND role = 'admin' AND status = 'active'
                    """,
                    team_uuid,
                )
                if admin_count == 1:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "last_admin_protected",
                            "message": "Cannot demote the last admin of the team",
                        },
                    )

            # Update role
            await conn.execute(
                """
                UPDATE team_members
                SET role = $1
                WHERE team_id = $2 AND tenant_id = $3
                """,
                body.role,
                team_uuid,
                target_uuid,
            )

        return {"success": True}

    @router.delete("/api/web/teams/{team_id}/members/{target_tenant_id}")
    async def remove_member(
        team_id: str,
        target_tenant_id: str,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Remove member from team."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        try:
            team_uuid = UUID(team_id)
            target_uuid = UUID(target_tenant_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid team_id or tenant_id") from e

        async with system_tx(pool) as conn:
            # Get caller and target info
            caller_member = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                sess.tenant_id,
            )
            if not caller_member:
                raise HTTPException(status_code=403, detail="not authorized")

            target_member = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                target_uuid,
            )
            if not target_member:
                raise HTTPException(status_code=404, detail="member not found")

            # Check permissions: admin or removing self
            if caller_member["role"] != "admin" and sess.tenant_id != target_uuid:
                raise HTTPException(status_code=403, detail="not authorized")

            # Check last admin constraint
            if target_member["role"] == "admin":
                admin_count = await conn.fetchval(
                    """
                    SELECT COUNT(*) FROM team_members
                    WHERE team_id = $1 AND role = 'admin' AND status = 'active'
                    """,
                    team_uuid,
                )
                if admin_count == 1:
                    raise HTTPException(
                        status_code=400,
                        detail={
                            "code": "last_admin_protected",
                            "message": "Cannot remove the last admin of the team",
                        },
                    )

            # Remove member
            await conn.execute(
                """
                DELETE FROM team_members
                WHERE team_id = $1 AND tenant_id = $2
                """,
                team_uuid,
                target_uuid,
            )

        return {"deleted": True}

    @router.delete("/api/web/teams/{team_id}/invites/{email}")
    async def cancel_invite(
        team_id: str,
        email: str,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Cancel a pending invite (admin only)."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session")

        try:
            team_uuid = UUID(team_id)
        except ValueError as e:
            raise HTTPException(status_code=400, detail="invalid team_id") from e

        async with system_tx(pool) as conn:
            # Check caller is admin
            caller_member = await conn.fetchrow(
                """
                SELECT role FROM team_members
                WHERE team_id = $1 AND tenant_id = $2 AND status = 'active'
                """,
                team_uuid,
                sess.tenant_id,
            )
            if not caller_member or caller_member["role"] != "admin":
                raise HTTPException(status_code=403, detail="not authorized")

            # Delete invite
            deleted = await conn.execute(
                """
                DELETE FROM team_invites
                WHERE team_id = $1 AND LOWER(email) = LOWER($2)
                """,
                team_uuid,
                email,
            )

        if deleted == "DELETE 0":
            raise HTTPException(status_code=404, detail="invite not found")

        return {"deleted": True}

    @router.get("/api/web/users/search")
    async def search_users(
        q: str | None = None,
        domain: str | None = None,
        exclude_team_id: str | None = None,
        mem_session: str | None = Cookie(default=None),
    ) -> dict[str, Any]:
        """Search workspace users by email prefix (for autocomplete)."""
        if not mem_session:
            raise HTTPException(status_code=401, detail="not authenticated")
        sess = await lookup_session(pool, mem_session)
        if sess is None:
            raise HTTPException(status_code=401, detail="invalid session") from None

        if not domain:
            raise HTTPException(status_code=400, detail="domain parameter required")

        if not q:
            q = ""

        # Verify caller's domain matches requested domain
        async with system_tx(pool) as conn:
            caller_identity = await conn.fetchrow(
                """
                SELECT workspace_domain FROM tenant_identities
                WHERE tenant_id = $1 AND is_primary
                """,
                sess.tenant_id,
            )

            if not caller_identity or caller_identity["workspace_domain"] != domain:
                raise HTTPException(status_code=403, detail="domain mismatch")

            # Parse exclude_team_id if provided
            exclude_team_uuid = None
            if exclude_team_id:
                try:
                    exclude_team_uuid = UUID(exclude_team_id)
                except ValueError as e:
                    raise HTTPException(status_code=400, detail="invalid exclude_team_id") from e

            # Search users in the workspace
            query_base = """
                SELECT DISTINCT ti.tenant_id, ti.email, ti.provider
                FROM tenant_identities ti
                WHERE ti.workspace_domain = $1
                    AND LOWER(ti.email) LIKE LOWER($2)
                    AND ti.is_primary
            """
            params: list[Any] = [domain, f"{q}%"]

            # Add exclusion if specified
            if exclude_team_uuid:
                query_base += """
                    AND ti.tenant_id NOT IN (
                        SELECT tenant_id FROM team_members
                        WHERE team_id = $3 AND status = 'active'
                    )
                """
                params.append(exclude_team_uuid)

            query_base += " ORDER BY ti.email LIMIT 20"

            rows = await conn.fetch(query_base, *params)

        users = [
            {
                "tenant_id": str(r["tenant_id"]),
                "email": r["email"],
                "provider": r["provider"],
            }
            for r in rows
        ]

        return {"users": users}

    return router
