# Lockout Recovery

## Purpose

Recover operator access when completely locked out (lost primary identity, all admin paths broken). This is a low-frequency, high-stakes scenario. The recovery method is a direct database write to create a new tenant_identity row, plus Cognito console steps.

## Prerequisites

- Direct database access via `MEM_MCP_DB_MAINT_DSN` (maintenance role with BYPASSRLS)
- Operator's original email address
- Cognito user pool ID
- AWS Console access (optional but recommended for verification)

## Scenario

You (the operator) are locked out because:

- Your primary Cognito identity is lost or inaccessible
- Your backup identity (if you had one) is also gone
- You cannot sign in via the web app
- The `/internal/` admin endpoints require a valid Cognito token

## Steps

### 1. Verify you are truly locked out

Try to sign in to the web app:
- Go to signup/login page
- Enter your email
- Cognito returns an error (user not found, account locked, etc.)

Try to call an admin endpoint with your last known token:
```bash
curl -H "Authorization: Bearer <old-token>" \
  https://mem-mcp.local/internal/health
```

Should return `401 Unauthorized` or similar.

### 2. Create a new Cognito user (Cognito Console)

This requires AWS Console access, not API.

1. Go to AWS Cognito → User Pools → `mem-mcp-prod`
2. Click "Users" → "Create user"
3. **Username:** Use your email (e.g., `ops@dheemantech.com`)
4. **Email:** Your email
5. **Temporary password:** Generate a strong one (e.g., `TempPass123!@#`)
6. **Mark email as verified:** Yes
7. **Send invitation email:** No (you'll bypass this)
8. Click "Create user"

Note the username (usually the email or a UUID; check the user details page).

### 3. Get your Cognito sub

From the user details page in Cognito Console, find the `sub` claim (unique identifier):

```
Username: ops@dheemantech.com
Sub: us-south-1:12345678-1234-1234-123456
Email: ops@dheemantech.com
```

Or query programmatically:

```bash
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
  --output text)

aws cognito-idp admin-get-user \
  --user-pool-id "$USER_POOL_ID" \
  --username "ops@dheemantech.com" \
  --region ap-south-1 \
  --query 'UserAttributes[?Name==`sub`].Value' \
  --output text
```

Example output: `us-south-1:12345678-1234-1234-123456`

### 4. Identify your operator tenant

Query the database for a tenant record that represents you:

```bash
export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, email, status FROM tenants
  WHERE email = 'ops@dheemantech.com'
  LIMIT 1;
"
```

If a row exists, save the `id` (tenant UUID).

If no row exists, create one:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  INSERT INTO tenants (email, display_name, status, tier)
  VALUES ('ops@dheemantech.com', 'Operator', 'active', 'platinum')
  RETURNING id, email, status;
"
```

Save the returned `id`.

### 5. Link the Cognito identity to the tenant

Create a new `tenant_identities` row linking your tenant to the new Cognito `sub`:

```bash
TENANT_ID="f47ac10b-58cc-4372-a567-0e02b2c3d479"  # from step 4
COGNITO_SUB="us-south-1:12345678-1234-1234-123456"  # from step 3
EMAIL="ops@dheemantech.com"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  INSERT INTO tenant_identities (tenant_id, cognito_sub, email, provider, is_primary)
  VALUES ('$TENANT_ID', '$COGNITO_SUB', '$EMAIL', 'cognito', true)
  ON CONFLICT (cognito_sub) DO NOTHING
  RETURNING id, tenant_id, cognito_sub, is_primary;
"
```

Expected output:

```
                  id                  |              tenant_id               |          cognito_sub          | is_primary
--------------------------------------+--------------------------------------+-------------------------------+------------
 abc1-def2-ghi3-jkl4-mno5            | f47ac10b-58cc-4372-a567-0e02b2c3d479 | us-south-1:12345678-1234-1234-123456 | t
```

### 6. Reset your Cognito password

You can now set a permanent password via Cognito:

```bash
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
  --output text)

# Set a new permanent password
aws cognito-idp admin-set-user-password \
  --user-pool-id "$USER_POOL_ID" \
  --username "ops@dheemantech.com" \
  --password "YourNewSecurePassword123!@#" \
  --permanent \
  --region ap-south-1
```

Or, reset via the Cognito Console:
1. Go to Users → Find your user
2. Click on your username
3. Under "Password," click "Set password"
4. Enter and confirm your password
5. Check "Mark as permanent"
6. Save

### 7. Test sign-in

Go to the mem-mcp web app and sign in:

```
Email: ops@dheemantech.com
Password: YourNewSecurePassword123!@#
```

You should be redirected to the dashboard.

### 8. Verify admin access

Call an admin endpoint to confirm:

```bash
# Obtain a fresh token from the Cognito login flow, then:
curl -H "Authorization: Bearer <new-token>" \
  https://mem-mcp.local/internal/health | jq
```

Should return `{"status": "ok"}` or similar.

## Verification

1. **Confirm the new tenant_identity exists:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT * FROM tenant_identities
     WHERE email = 'ops@dheemantech.com';
   "
   ```

2. **Confirm the identity is marked primary:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT is_primary FROM tenant_identities
     WHERE email = 'ops@dheemantech.com';
   "
   ```
   Should return `t` (true).

3. **Sign in and call a protected endpoint:**
   The web app should grant you a valid token, and the admin endpoint should return successfully.

## Prevention

To avoid future lockout:

1. **Create a backup identity:** Set up an additional Cognito identity (e.g., a backup email) linked to your tenant as a secondary identity (`is_primary = false`). If the primary is lost, you can authenticate via the secondary.

   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     INSERT INTO tenant_identities (tenant_id, cognito_sub, email, provider, is_primary)
     VALUES ('$TENANT_ID', '<backup-cognito-sub>', 'backup@dheemantech.com', 'cognito', false);
   "
   ```

2. **Store recovery credentials:** Keep a written copy of your operator tenant UUID and a list of associated Cognito SUBs in a safe place (password manager, offline vault).

3. **Test recovery quarterly:** Every 3 months, test the recovery process on a non-production tenant to ensure you remember the steps.

## Notes

- **Direct database write:** This recovery method bypasses the normal signup flow and RLS. Only do this if you are absolutely certain you are the operator and have legitimate access to the database.
- **Audit trail:** The insert is logged automatically. Review audit_log later to confirm the recovery was recorded.
- **Cognito user must exist first:** You cannot link a `cognito_sub` to a tenant_identity if the Cognito user does not exist. Always create the Cognito user first.
- **Email uniqueness:** The `email` column in `tenant_identities` is NOT unique (multiple identities can share the same email if they are for the same tenant). Only `cognito_sub` is globally unique.

See also: [suspend_tenant.md](suspend_tenant.md) (access revocation), [add_beta_user.md](add_beta_user.md) (identity management).
