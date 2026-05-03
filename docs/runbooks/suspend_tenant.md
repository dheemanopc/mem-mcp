# Suspend Tenant

## Purpose

Immediately disable a tenant (user account) due to abuse, policy violation, or other operational reason. This revokes all active sessions and marks the account as `suspended`. The user can no longer sign in or access their memories.

## Prerequisites

- SSH access to EC2 or CLI access to AWS/DB
- `MEM_MCP_DB_MAINT_DSN` environment variable set
- Tenant email address or UUID
- AWS Cognito user pool ID (available in CloudFormation outputs)
- AWS CLI configured with appropriate credentials

## Steps

### 1. Identify the tenant

If you have the email, get the UUID:

```bash
export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, email, status FROM tenants WHERE email = 'abuse@example.com';
"
```

Example output:
```
                  id                  |        email         | status
--------------------------------------+----------------------+--------
 f47ac10b-58cc-4372-a567-0e02b2c3d479 | abuse@example.com    | active
```

Save the tenant UUID for the next steps.

### 2. Update tenant status to suspended

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  UPDATE tenants
  SET status = 'suspended', updated_at = now()
  WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
  RETURNING id, email, status, updated_at;
"
```

### 3. Revoke all active Cognito sessions

Get the Cognito user pool ID from CloudFormation:

```bash
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
  --output text)
echo "User Pool ID: $USER_POOL_ID"
```

List all identities for this tenant:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, cognito_sub, email FROM tenant_identities
  WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
  ORDER BY is_primary DESC;
"
```

Example output:
```
                  id                  |              cognito_sub              |        email
--------------------------------------+---------------------------------------+-------------------
 abc123-def456-ghi789-jkl012-mno345  | us-south-1:12345678-1234-1234-123456 | abuse@example.com
```

For each `cognito_sub`, revoke all tokens:

```bash
aws cognito-idp admin-user-global-sign-out \
  --user-pool-id "$USER_POOL_ID" \
  --username "us-south-1:12345678-1234-1234-123456" \
  --region ap-south-1
```

Expected output:
```
{}
```

### 4. Send notification email (optional)

Use AWS SES to notify the user:

```bash
aws ses send-email \
  --from "ops@mem-mcp.local" \
  --to "abuse@example.com" \
  --subject "Your mem-mcp account has been suspended" \
  --text "Your account has been suspended due to policy violation. If you believe this was an error, contact support@mem-mcp.local"
```

## Verification

1. **Confirm status update:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT id, email, status FROM tenants WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479';
   "
   ```
   Should show `status = 'suspended'`.

2. **Confirm Cognito signout:**
   Try to access the app with the old session token — should fail with `NotAuthorizedException`.

3. **Verify login is blocked:**
   Attempt to sign in at the web app with the email/Cognito flow — should fail because Cognito knows the user is in a suspended pool (or you manually enforced a Lambda rule check).

## Rollback

To **reactivate** a suspended tenant:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  UPDATE tenants
  SET status = 'active', updated_at = now()
  WHERE id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
  RETURNING id, email, status;
"
```

The user can then sign in again with fresh Cognito auth.

## Notes

- **Session revocation is immediate:** No grace period. Logged-in clients with cached tokens will fail on next API call.
- **Data is not deleted:** Use [dpdp_delete_request.md](dpdp_delete_request.md) if you want to erase memories.
- **Why both DB and Cognito?** DB status blocks business logic; Cognito revocation blocks login. Both are needed.
- **Audit trail:** The update to `tenants.status` is logged; Cognito also logs the sign-out in CloudWatch Logs.

See also: [dpdp_delete_request.md](dpdp_delete_request.md) (full deletion), [add_beta_user.md](add_beta_user.md) (invites).
