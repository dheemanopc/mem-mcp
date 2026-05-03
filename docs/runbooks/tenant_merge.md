# Tenant Merge

## Purpose

Merge two tenant records into one. This handles the case where a user accidentally created two accounts with different emails/identities but wants to consolidate them (e.g., `alice@example.com` and `alice.personal@example.com` both created accounts for the same person). The source tenant's memories, audit log, and identities are moved to the target tenant, and the source is deactivated.

## Prerequisites

- `MEM_MCP_DB_MAINT_DSN` environment variable set
- Explicit written confirmation from the user
- Identification of the source tenant (to be merged) and target tenant (to receive the data)
- Note: This is a rare operation; coordinate with the user before proceeding

## Steps

### 1. Identify the two tenants

Get the email addresses from the user:

```bash
SOURCE_EMAIL="alice.personal@example.com"
TARGET_EMAIL="alice@example.com"

export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, email, status FROM tenants
  WHERE email IN ('$SOURCE_EMAIL', '$TARGET_EMAIL');
"
```

Example output:

```
                  id                  |            email             | status
--------------------------------------+------------------------------+--------
 source-id-58cc-4372-a567-0e02b2c3d4 | alice.personal@example.com   | active
 target-id-4372-a567-0e02b2c3d479-8b | alice@example.com            | active
```

Save both UUIDs:

```
SOURCE_ID="source-id-58cc-4372-a567-0e02b2c3d4"
TARGET_ID="target-id-4372-a567-0e02b2c3d479-8b"
```

### 2. Backup data (optional but recommended)

Create a backup of the source tenant before merging:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT * FROM memories WHERE tenant_id = '$SOURCE_ID'
" > /tmp/source_memories_backup.json

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT * FROM audit_log WHERE tenant_id = '$SOURCE_ID'
" > /tmp/source_audit_backup.json

echo "Backups saved to /tmp/source_*.json"
```

### 3. Merge in a transaction

This is a single transaction to ensure atomicity. If any step fails, the entire merge is rolled back.

```bash
psql "$MEM_MCP_DB_MAINT_DSN" << 'EOF'
BEGIN;

-- Set working variables
SET LOCAL app.current_tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b';

-- Step 1: Move all memories from source to target
UPDATE memories
SET tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b'
WHERE tenant_id = 'source-id-58cc-4372-a567-0e02b2c3d4';

-- Step 2: Move all identities from source to target
-- (keeping is_primary = false for source identities to avoid conflicts)
UPDATE tenant_identities
SET tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b',
    is_primary = false
WHERE tenant_id = 'source-id-58cc-4372-a567-0e02b2c3d4';

-- Step 3: Move usage tracking
UPDATE tenant_daily_usage
SET tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b'
WHERE tenant_id = 'source-id-58cc-4372-a567-0e02b2c3d4';

-- Step 4: Move OAuth consents
UPDATE oauth_consents
SET tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b'
WHERE tenant_id = 'source-id-58cc-4372-a567-0e02b2c3d4';

-- Step 5: Create an audit log entry recording the merge
INSERT INTO audit_log (tenant_id, event_type, action, actor_id, metadata)
VALUES (
  'target-id-4372-a567-0e02b2c3d479-8b',
  'tenant',
  'merged',
  'ops',
  jsonb_build_object(
    'source_tenant_id', 'source-id-58cc-4372-a567-0e02b2c3d4',
    'source_tenant_email', 'alice.personal@example.com',
    'merged_at', NOW(),
    'merged_by', 'ops'
  )
);

-- Step 6: Deactivate the source tenant (mark as deleted, preserve for audit)
UPDATE tenants
SET status = 'deleted', updated_at = NOW()
WHERE id = 'source-id-58cc-4372-a567-0e02b2c3d4';

-- Verify the merge
SELECT
  (SELECT COUNT(*) FROM memories WHERE tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b') as memories_in_target,
  (SELECT COUNT(*) FROM memories WHERE tenant_id = 'source-id-58cc-4372-a567-0e02b2c3d4') as memories_in_source,
  (SELECT COUNT(*) FROM tenant_identities WHERE tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b') as identities_in_target,
  (SELECT status FROM tenants WHERE id = 'source-id-58cc-4372-a567-0e02b2c3d4') as source_status;

COMMIT;
EOF
```

Expected output after `COMMIT`:

```
 memories_in_target | memories_in_source | identities_in_target | source_status
--------------------+--------------------+----------------------+---------------
                 15 |                  0 |                    2 | deleted
```

### 4. Verify the merge

Confirm all data is in the target tenant:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT COUNT(*) as total_memories FROM memories
  WHERE tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b';
"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT email, is_primary FROM tenant_identities
  WHERE tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b'
  ORDER BY is_primary DESC;
"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT event_type, action, metadata FROM audit_log
  WHERE tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b'
  AND event_type = 'tenant' AND action = 'merged'
  LIMIT 1;
"
```

### 5. Notify the user

Send a confirmation email:

```
Subject: Your mem-mcp accounts have been merged

Hello Alice,

Your two accounts (alice@example.com and alice.personal@example.com) have been successfully merged into one. All your memories and activity have been consolidated under alice@example.com.

You can now sign in with alice@example.com or alice.personal@example.com (both identities are linked).

If you have any questions, contact support@mem-mcp.local.

Best regards,
Operator
```

## Verification

1. **Memories are accessible in the target tenant:**
   ```bash
   # Sign in as the target user (alice@example.com)
   # Verify that you can see all memories (including those from the source account)
   curl -H "Authorization: Bearer <target-token>" \
     https://mem-mcp.local/api/memories | jq '.items | length'
   ```

2. **Both email identities are linked:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT email, is_primary FROM tenant_identities
     WHERE tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b';
   "
   ```
   Should show both emails, with one marked `t` (primary) and one marked `f` (secondary).

3. **Source tenant is deactivated:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT status FROM tenants WHERE id = 'source-id-58cc-4372-a567-0e02b2c3d4';
   "
   ```
   Should return `deleted`.

4. **Merge is recorded in audit log:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT metadata FROM audit_log
     WHERE event_type = 'tenant' AND action = 'merged'
     ORDER BY created_at DESC
     LIMIT 1;
   "
   ```
   Should show the merge event with source and target tenant IDs.

## Rollback

If the merge failed or needs to be undone:

1. **Stop immediately** and roll back the transaction (automatically done if any step in the merge transaction failed).
2. **Investigate the failure** — check PostgreSQL logs for constraint violations or other errors.
3. **If you need to undo a successful merge** (rare), restore from a backup (see [restore_from_backup.md](restore_from_backup.md)).

If a single row was moved incorrectly, you can move it back:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  UPDATE memories
  SET tenant_id = 'source-id-58cc-4372-a567-0e02b2c3d4'
  WHERE id = '<memory-id>' AND tenant_id = 'target-id-4372-a567-0e02b2c3d479-8b';
"
```

## Notes

- **Audit trail is not merged:** The source tenant's audit_log entries remain associated with the source tenant_id. This preserves the historical record. New audit entries are written to the target tenant.
- **Primary identity:** After merge, the target tenant has one primary identity (`is_primary = true`). The source tenant's primary identity is downgraded to secondary (`is_primary = false`). The user can sign in with either email.
- **Source tenant is "soft deleted":** We set `status = 'deleted'` but do NOT drop the row. This preserves the merge history in case of audit or recovery needs.
- **Concurrent requests:** If the user is actively using the app during the merge, requests from the source tenant will fail (because the tenant no longer owns the data). Recommend asking the user to sign out and sign back in after the merge is complete.

See also: [suspend_tenant.md](suspend_tenant.md) (access control), [dpdp_delete_request.md](dpdp_delete_request.md) (deletion).
