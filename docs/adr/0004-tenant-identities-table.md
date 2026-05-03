# ADR 0004: Separate tenant_identities table

## Status

Accepted (2026-04-18)

## Context

mem-mcp supports multiple identity providers (initially Cognito; later Google, GitHub, etc.). Each tenant is a logical user who may sign in via multiple OAuth identity providers.

The schema must cleanly separate:
1. **Tenant**: the lifecycle entity (a single user across all their identities)
2. **Tenant Identity**: the authentication entity (a single OAuth credential from a specific IdP)

This separation allows:
- A tenant to link/unlink identities at runtime (e.g., "sign in with Google" → "also sign in with GitHub")
- IdP-agnostic identity resolution (JWT subject claim → tenant_id lookup)
- Easy v2 support for new IdPs without schema migration

## Decision

Create two tables:

```sql
CREATE TABLE tenants (
  tenant_id UUID PRIMARY KEY,
  created_at TIMESTAMP NOT NULL,
  status VARCHAR(20) NOT NULL CHECK (status IN ('active', 'suspended', 'closed'))
);

CREATE TABLE tenant_identities (
  identity_id UUID PRIMARY KEY,
  tenant_id UUID NOT NULL REFERENCES tenants(tenant_id),
  idp_type VARCHAR(50) NOT NULL,  -- 'cognito', 'google', 'github'
  idp_sub VARCHAR(500) NOT NULL,   -- OAuth subject claim
  created_at TIMESTAMP NOT NULL,
  UNIQUE(idp_type, idp_sub)
);
```

Lookups: JWT arrives → extract `idp_type` and `sub` → query `tenant_identities` → get `tenant_id` → RLS applies.

## Consequences

### Positive
- Clean separation of concerns (lifecycle vs. auth)
- Link/unlink identities without tenant state change
- Trivial to add new IdPs: insert new `tenant_identities` row
- UNIQUE(idp_type, idp_sub) prevents duplicate identities from the same provider

### Negative
- One extra table to manage
- Every token validation requires a JOIN (negligible; indexed on `(idp_type, idp_sub)`)

### Risks accepted
- Migration complexity if we later need to merge/split tenants. Mitigation: document merge procedure in operations runbook.

## Alternatives considered

- **Single table (tenants only, store idp_sub as JSON)**: Rejected. Loses the UNIQUE constraint and makes identity lookup slower.
- **EAV table for identities**: Rejected. Over-normalized; adds query complexity without benefit.
