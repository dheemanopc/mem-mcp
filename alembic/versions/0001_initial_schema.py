"""Initial schema migration for mem-mcp.

This migration creates the full DDL for v1, including:
- Tenants and tenant identities
- OAuth clients and consents
- Memories with RLS
- Tenant usage tracking
- Audit log
- Supporting tables (link_state, web_sessions, feedback)

References:
- MEMORY_MCP_BUILD_PLAN_V2.md §8.3 (full DDL spec)
- MEMORY_MCP_LLD_V1.md §5 (RLS policy, v1 deltas)
"""

from __future__ import annotations

import sqlalchemy as sa

from alembic import op

revision = "0001_initial_schema"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """Apply full DDL schema."""
    # Extensions
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
    op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS pg_trgm"))

    # ==========================================================================
    # TENANTS
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE tenants (
            id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            email                       TEXT UNIQUE NOT NULL,
            display_name                TEXT,
            status                      TEXT NOT NULL DEFAULT 'active'
                                        CHECK (status IN ('active','suspended','pending_deletion','deleted')),
            tier                        TEXT NOT NULL DEFAULT 'premium'
                                        CHECK (tier IN ('standard','premium','gold','platinum')),
            limits_override             JSONB,
            retention_days              INT NOT NULL DEFAULT 365 CHECK (retention_days BETWEEN 7 AND 3650),
            deletion_requested_at       TIMESTAMPTZ,
            deletion_cancel_token_hash  TEXT,
            metadata                    JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX idx_tenants_status ON tenants(status)
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_tenants_pending_deletion ON tenants(deletion_requested_at)
            WHERE status = 'pending_deletion'
        """)
    )

    # ==========================================================================
    # TENANT IDENTITIES
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE tenant_identities (
            id                  UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            cognito_sub         TEXT UNIQUE NOT NULL,
            cognito_username    TEXT,
            provider            TEXT NOT NULL CHECK (provider IN ('google','cognito')),
            provider_user_id    TEXT,
            email               TEXT NOT NULL,
            is_primary          BOOLEAN NOT NULL DEFAULT false,
            linked_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at        TIMESTAMPTZ
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX idx_identities_tenant ON tenant_identities(tenant_id)
        """)
    )
    op.execute(
        sa.text("""
        CREATE UNIQUE INDEX idx_identities_one_primary
            ON tenant_identities(tenant_id) WHERE is_primary
        """)
    )

    # ==========================================================================
    # INVITED EMAILS
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE invited_emails (
            email           TEXT PRIMARY KEY,
            invited_by      TEXT,
            invited_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            consumed_at     TIMESTAMPTZ,
            notes           TEXT
        )
        """)
    )

    # ==========================================================================
    # ALLOWED SOFTWARE
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE allowed_software (
            software_id     TEXT PRIMARY KEY,
            display_name    TEXT NOT NULL,
            vendor          TEXT NOT NULL,
            verified        BOOLEAN NOT NULL DEFAULT false,
            notes           TEXT,
            status          TEXT NOT NULL DEFAULT 'allowed'
                            CHECK (status IN ('allowed','blocked','pending_review','revoked')),
            added_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
            added_by        TEXT,
            review_payload  JSONB
        )
        """)
    )

    # ==========================================================================
    # OAUTH CLIENTS
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE oauth_clients (
            id                              TEXT PRIMARY KEY,
            tenant_id                       UUID REFERENCES tenants(id) ON DELETE SET NULL,
            software_id                     TEXT REFERENCES allowed_software(software_id),
            client_name                     TEXT,
            redirect_uris                   TEXT[] NOT NULL,
            scope                           TEXT NOT NULL,
            registration_payload            JSONB NOT NULL,
            registration_access_token_hash  TEXT,
            review_status                   TEXT NOT NULL DEFAULT 'auto_allowed'
                                            CHECK (review_status IN ('auto_allowed','pending_review',
                                                  'agent_approved','agent_rejected',
                                                  'human_approved','human_rejected')),
            review_notes                    JSONB,
            disabled                        BOOLEAN NOT NULL DEFAULT false,
            created_at                      TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_used_at                    TIMESTAMPTZ,
            deleted_at                      TIMESTAMPTZ
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX idx_oauth_clients_tenant ON oauth_clients(tenant_id)
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_oauth_clients_software ON oauth_clients(software_id)
        """)
    )

    # ==========================================================================
    # OAUTH CONSENTS
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE oauth_consents (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            client_id       TEXT NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
            scopes          TEXT NOT NULL,
            granted_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            revoked_at      TIMESTAMPTZ,
            UNIQUE (tenant_id, client_id)
        )
        """)
    )
    # v2-ready: created in v1 but no writer; consent screen dropped in v1 (see LLD §0)

    op.execute(
        sa.text("""
        CREATE INDEX idx_consents_tenant ON oauth_consents(tenant_id)
        """)
    )

    # ==========================================================================
    # MEMORIES (main content table with RLS)
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE memories (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            content         TEXT NOT NULL CHECK (length(content) BETWEEN 1 AND 32768),
            content_hash    TEXT NOT NULL,
            content_tsv     TSVECTOR GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
            embedding       VECTOR(1024) NOT NULL,
            embedding_norm  REAL,
            source_client_id TEXT REFERENCES oauth_clients(id),
            source_kind     TEXT NOT NULL CHECK (source_kind IN
                            ('claude_code','claude_chat','chatgpt','api','backfill','web_ui')),
            type            TEXT NOT NULL DEFAULT 'note'
                            CHECK (type IN ('note','decision','fact','snippet','question')),
            tags            TEXT[] NOT NULL DEFAULT '{}',
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            version         INT NOT NULL DEFAULT 1,
            supersedes      UUID REFERENCES memories(id),
            superseded_by   UUID REFERENCES memories(id),
            is_current      BOOLEAN NOT NULL DEFAULT true,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            deleted_at      TIMESTAMPTZ
        )
        """)
    )

    op.execute(
        sa.text("""
        ALTER TABLE memories ENABLE ROW LEVEL SECURITY
        """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE memories FORCE ROW LEVEL SECURITY
        """)
    )

    op.execute(
        sa.text("""
        CREATE POLICY memories_tenant_isolation ON memories
            USING      (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX idx_memories_tenant_active
            ON memories(tenant_id, created_at DESC)
            WHERE deleted_at IS NULL AND is_current = true
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_memories_tags
            ON memories USING GIN(tags) WHERE deleted_at IS NULL AND is_current = true
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_memories_tsv
            ON memories USING GIN(content_tsv) WHERE deleted_at IS NULL AND is_current = true
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_memories_embedding
            ON memories USING hnsw (embedding vector_cosine_ops)
            WITH (m=16, ef_construction=64)
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_memories_hash
            ON memories(tenant_id, content_hash)
            WHERE deleted_at IS NULL AND is_current = true
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_memories_supersedes
            ON memories(supersedes) WHERE supersedes IS NOT NULL
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_memories_type
            ON memories(tenant_id, type) WHERE deleted_at IS NULL AND is_current = true
        """)
    )

    # ==========================================================================
    # TENANT DAILY USAGE (quota tracking with RLS)
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE tenant_daily_usage (
            tenant_id      UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            usage_date     DATE NOT NULL,
            embed_tokens   BIGINT NOT NULL DEFAULT 0,
            writes_count   INT NOT NULL DEFAULT 0,
            reads_count    INT NOT NULL DEFAULT 0,
            deletes_count  INT NOT NULL DEFAULT 0,
            PRIMARY KEY (tenant_id, usage_date)
        )
        """)
    )

    op.execute(
        sa.text("""
        ALTER TABLE tenant_daily_usage ENABLE ROW LEVEL SECURITY
        """)
    )
    op.execute(
        sa.text("""
        ALTER TABLE tenant_daily_usage FORCE ROW LEVEL SECURITY
        """)
    )
    op.execute(
        sa.text("""
        CREATE POLICY usage_tenant_isolation ON tenant_daily_usage
            USING      (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
            WITH CHECK (tenant_id = current_setting('app.current_tenant_id', true)::uuid)
        """)
    )

    # ==========================================================================
    # RATE LIMITS
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE rate_limits (
            key             TEXT PRIMARY KEY,
            bucket_start    TIMESTAMPTZ NOT NULL,
            count           INT NOT NULL DEFAULT 0
        )
        """)
    )

    # ==========================================================================
    # AUDIT LOG (append-only)
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE audit_log (
            id                  BIGSERIAL PRIMARY KEY,
            tenant_id           UUID,
            actor_client_id     TEXT,
            actor_identity_id   UUID REFERENCES tenant_identities(id),
            action              TEXT NOT NULL,
            target_id           UUID,
            target_kind         TEXT,
            ip_address          INET,
            user_agent          TEXT,
            request_id          TEXT,
            result              TEXT NOT NULL CHECK (result IN ('success','denied','error')),
            error_code          TEXT,
            details             JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX idx_audit_tenant_time ON audit_log(tenant_id, created_at DESC)
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_audit_action_time ON audit_log(action, created_at DESC)
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_audit_request_id ON audit_log(request_id)
        """)
    )

    # ==========================================================================
    # LINK STATE (signed state for cross-IdP linking)
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE link_state (
            nonce           TEXT PRIMARY KEY,
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            expires_at      TIMESTAMPTZ NOT NULL,
            consumed_at     TIMESTAMPTZ,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX idx_link_state_expires ON link_state(expires_at)
        """)
    )

    # ==========================================================================
    # WEB SESSIONS
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE web_sessions (
            session_hash    TEXT PRIMARY KEY,
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            identity_id     UUID NOT NULL REFERENCES tenant_identities(id) ON DELETE CASCADE,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
            last_seen_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
            expires_at      TIMESTAMPTZ NOT NULL,
            user_agent      TEXT,
            ip_address      INET,
            revoked_at      TIMESTAMPTZ
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX idx_sessions_tenant ON web_sessions(tenant_id)
        """)
    )
    op.execute(
        sa.text("""
        CREATE INDEX idx_sessions_expires ON web_sessions(expires_at)
        """)
    )

    # ==========================================================================
    # FEEDBACK
    # ==========================================================================
    op.execute(
        sa.text("""
        CREATE TABLE feedback (
            id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id       UUID NOT NULL REFERENCES tenants(id) ON DELETE CASCADE,
            client_id       TEXT REFERENCES oauth_clients(id),
            text            TEXT NOT NULL CHECK (length(text) BETWEEN 1 AND 4096),
            metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
            created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """)
    )

    op.execute(
        sa.text("""
        CREATE INDEX idx_feedback_tenant_time ON feedback(tenant_id, created_at DESC)
        """)
    )

    # ==========================================================================
    # GRANTS TO mem_app ROLE
    # ==========================================================================
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON tenants TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_identities TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT ON invited_emails TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT UPDATE (consumed_at) ON invited_emails TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT ON allowed_software TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_clients TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON oauth_consents TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON memories TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON tenant_daily_usage TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON rate_limits TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT INSERT ON audit_log TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT USAGE ON SEQUENCE audit_log_id_seq TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON link_state TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT, UPDATE, DELETE ON web_sessions TO mem_app
        """)
    )
    op.execute(
        sa.text("""
        GRANT SELECT, INSERT ON feedback TO mem_app
        """)
    )


def downgrade() -> None:
    """Drop all tables in reverse FK order, then extensions."""
    # Drop tables in reverse dependency order
    op.execute(sa.text("DROP TABLE IF EXISTS feedback"))
    op.execute(sa.text("DROP TABLE IF EXISTS web_sessions"))
    op.execute(sa.text("DROP TABLE IF EXISTS link_state"))
    op.execute(sa.text("DROP TABLE IF EXISTS audit_log"))
    op.execute(sa.text("DROP TABLE IF EXISTS rate_limits"))
    op.execute(sa.text("DROP TABLE IF EXISTS tenant_daily_usage"))
    op.execute(sa.text("DROP TABLE IF EXISTS memories"))
    op.execute(sa.text("DROP TABLE IF EXISTS oauth_consents"))
    op.execute(sa.text("DROP TABLE IF EXISTS oauth_clients"))
    op.execute(sa.text("DROP TABLE IF EXISTS allowed_software"))
    op.execute(sa.text("DROP TABLE IF EXISTS invited_emails"))
    op.execute(sa.text("DROP TABLE IF EXISTS tenant_identities"))
    op.execute(sa.text("DROP TABLE IF EXISTS tenants"))

    # Drop extensions
    op.execute(sa.text("DROP EXTENSION IF EXISTS pg_trgm"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS vector"))
    op.execute(sa.text("DROP EXTENSION IF EXISTS pgcrypto"))
