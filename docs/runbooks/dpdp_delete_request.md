# DPDP Delete Request (Right to Erasure)

## Purpose

Fulfill a user's DPDP "right to erasure" (also called "right to be forgotten"). Irreversibly delete all tenant data: memories, audit log, identities, consents, and the tenant record itself. Optionally skip the 24-hour grace period if the user explicitly waives it.

## Prerequisites

- `MEM_MCP_DB_MAINT_DSN` environment variable set
- Tenant email address or UUID
- Confirmation from the user (written email, form, or documented request)
- Optional: explicit waiver of the 24-hour grace period (if you want to delete immediately)

## Steps

### 1. Identify the tenant and document the request

```bash
export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, email, status FROM tenants WHERE email = 'user@example.com';
"
```

Save the tenant UUID and email. Create a record of the request (date, method, confirmation source).

### 2. Option A: Graceful Deletion (24-hour cooling-off period)

This is the default. Set the tenant to `pending_deletion` with a cancel token.

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  UPDATE tenants
  SET status = 'pending_deletion',
      deletion_requested_at = now(),
      deletion_cancel_token_hash = gen_random_uuid()::text,
      updated_at = now()
  WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
  RETURNING id, email, status, deletion_requested_at, deletion_cancel_token_hash;
"
```

**What happens next:**
- Tenant is now in `pending_deletion` state; they cannot sign in or access memories
- A background job (or cron task) runs every hour and checks for tenants in `pending_deletion` whose 24-hour window has passed
- After 24 hours, the job calls `lifecycle.request_closure()` (see Option B)
- If the user changes their mind within 24 hours, they contact support with the cancel token to stop the deletion

### 2. Option B: Immediate Deletion (User Explicitly Waives Grace Period)

Only if the user has explicitly requested this in writing. Use Python REPL or job runner:

```bash
python3 << 'EOF'
import asyncio
from mem_mcp.lifecycle import request_closure

async def delete_now():
    tenant_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"
    result = await request_closure(
        tenant_id=tenant_id,
        skip_grace_period=True,
        reason="User explicit waiver - DPDP right to erasure"
    )
    print(f"Deletion completed: {result}")

asyncio.run(delete_now())
EOF
```

Or, trigger the deletion manually via SQL (nuclear option):

```bash
psql "$MEM_MCP_DB_MAINT_DSN" << 'EOF'
BEGIN;

-- Tenant ID
SET LOCAL app.current_tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

-- Delete all memories (RLS will enforce tenant isolation)
DELETE FROM memories WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

-- Delete audit log
DELETE FROM audit_log WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

-- Delete identities
DELETE FROM tenant_identities WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

-- Delete OAuth consents
DELETE FROM oauth_consents WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

-- Delete usage tracking
DELETE FROM tenant_daily_usage WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

-- Mark tenant as deleted (soft delete first)
UPDATE tenants
SET status = 'deleted', updated_at = now()
WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

-- If you want true hard delete (rare; only if DPDP explicitly requires it):
-- DELETE FROM tenants WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';

COMMIT;
EOF
```

### 3. Revoke Cognito access

Whether graceful or immediate, revoke the user's Cognito sessions:

```bash
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
  --output text)

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT cognito_sub FROM tenant_identities
  WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
"

# For each cognito_sub:
aws cognito-idp admin-user-global-sign-out \
  --user-pool-id "$USER_POOL_ID" \
  --username "us-south-1:12345678-1234-1234-123456" \
  --region ap-south-1
```

## Verification

**For graceful deletion (24-hour window):**

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, email, status, deletion_requested_at, deletion_cancel_token_hash
  FROM tenants
  WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';
"
```

Should show `status = 'pending_deletion'` and `deletion_requested_at` set to now.

**For immediate deletion:**

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, email, status FROM tenants
  WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';
"
```

Should show `status = 'deleted'`. Verify that memories, audit_log, and identities are empty:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT COUNT(*) FROM memories WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';
  SELECT COUNT(*) FROM audit_log WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';
"
```

Both should return `0`.

## Rollback

**For graceful deletion (within 24 hours):**

If the user wants to cancel, they provide the cancel token. Verify it, then:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  UPDATE tenants
  SET status = 'active', deletion_requested_at = NULL, deletion_cancel_token_hash = NULL
  WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
  RETURNING id, email, status;
"
```

**For immediate deletion:**

No rollback. Data is gone. Restore from backup if needed (see [restore_from_backup.md](restore_from_backup.md)), but this recreates the entire database, not a single user. Consider this a one-way operation.

## Notes

- **Graceful deletion is recommended:** Gives users a chance to change their mind and complies with good UX practice.
- **Cognito user pool:** We intentionally do NOT delete from Cognito during deletion (per DPDP clarifications). The pool record remains but is inaccessible to the user because the tenant record no longer exists and the PreSignUp Lambda will deny re-signup.
- **Audit trail:** The deletion itself is NOT logged in audit_log (which is also deleted); you must rely on CloudWatch and your external audit trail (e.g., SSM Parameter Store audit).
- **Backups:** S3 backups are not automatically deleted. If you want to keep them for compliance, they persist unless manually removed. If you want to expunge them too, see [restore_from_backup.md](restore_from_backup.md) notes on S3 object deletion.

See also: [suspend_tenant.md](suspend_tenant.md) (temporary revocation), [dpdp_export_request.md](dpdp_export_request.md) (export before delete).
