# Investigate Token Reuse

## Purpose

Respond to the `auth.token_refresh_reuse` CloudWatch alarm, which fires when a refresh token is used more than once within the same session. This indicates a potential token compromise, client bug, or attack. This playbook guides triage and remediation.

## Prerequisites

- CloudWatch Metrics access
- `MEM_MCP_DB_MAINT_DSN` environment variable set (for audit log queries)
- AWS CLI configured
- Cognito user pool ID

## Alarm Details

**Metric:** `auth.token_refresh_reuse`  
**Threshold:** 1 or more reuse events in a 5-minute window  
**What triggers it:** The auth service logs a `token.reused` event when:
1. A refresh token is used to request a new access token
2. The same refresh token is used *again* before the new one is issued
3. This pattern repeats in the same session

**Why it matters:** 
- Legitimate clients request new tokens only once per session
- Repeated reuse suggests: token theft (attacker replaying the token), concurrent requests from multiple clients (code bug), or a client retrying with the same token (network issue + bug)

## Steps

### 1. Check the alarm and recent audit events

View the CloudWatch alarm:

```bash
aws cloudwatch describe-alarms \
  --alarm-names "mem-mcp-auth-token-reuse" \
  --region ap-south-1
```

Query the audit log for recent token reuse events:

```bash
export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, tenant_id, event_type, action, actor_id, created_at, metadata
  FROM audit_log
  WHERE event_type = 'token' AND action = 'reused'
  AND created_at > now() - interval '1 hour'
  ORDER BY created_at DESC
  LIMIT 20;
"
```

Example output:
```
                  id                  |              tenant_id               | event_type |  action | actor_id |          created_at           |                 metadata
--------------------------------------+--------------------------------------+------------+---------+----------+-------------------------------+------------------------------------------
 abc1-def2-ghi3-jkl4-mno5            | f47ac10b-58cc-4372-a567-0e02b2c3d479 | token      | reused  | user1    | 2026-05-03 14:22:33.456789+00 | {"refresh_token_id":"rt123","attempt":2}
```

### 2. Identify the affected tenant and client

Extract the tenant_id from the audit log. Query for the tenant:

```bash
TENANT_ID="f47ac10b-58cc-4372-a567-0e02b2c3d479"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT id, email, status FROM tenants WHERE id = '$TENANT_ID';
"
```

Check the metadata in the audit log event for the `refresh_token_id` or `client_id`:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT metadata::jsonb->'client_id' as client_id,
         metadata::jsonb->'attempt' as attempt_count,
         COUNT(*) as reuse_count
  FROM audit_log
  WHERE event_type = 'token' AND action = 'reused'
  AND tenant_id = '$TENANT_ID'
  AND created_at > now() - interval '1 hour'
  GROUP BY metadata::jsonb->'client_id', metadata::jsonb->'attempt'
  ORDER BY reuse_count DESC;
"
```

### 3. Triage decision tree

**Decision 1: Is this the first reuse event for this tenant?**

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT COUNT(*) as reuse_events
  FROM audit_log
  WHERE event_type = 'token' AND action = 'reused'
  AND tenant_id = '$TENANT_ID'
  AND created_at > now() - interval '30 days';
"
```

- **Count = 1:** Isolated incident, likely a network hiccup or retry. Monitor.
- **Count > 5:** Pattern emerging. Investigate further.
- **Count > 20 in last hour:** Active abuse or compromise. Escalate to suspension.

**Decision 2: Is the tenant's password/account compromised?**

Check for other suspicious activity:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT event_type, action, COUNT(*) as count
  FROM audit_log
  WHERE tenant_id = '$TENANT_ID'
  AND created_at > now() - interval '1 hour'
  GROUP BY event_type, action
  ORDER BY count DESC;
"
```

- If there are many `memory.write` or `memory.export` events from unusual times/locations → likely compromise
- If only token reuse, no other activity → likely client bug

**Decision 3: Can you identify the client?**

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT DISTINCT metadata::jsonb->'client_id' as client_id
  FROM audit_log
  WHERE tenant_id = '$TENANT_ID'
  AND created_at > now() - interval '1 hour'
  ORDER BY client_id;
"
```

- If you see only one client_id → bug in that client
- If you see multiple different client_ids → attacker trying many stolen tokens

## Remediation

### Option 1: Monitor (Low-Risk, Isolated Event)

- Token reuse is rare (< 5 in 24 hours)
- No other suspicious activity
- Account status is normal

**Action:** Log and monitor. No immediate action needed.

```bash
echo "[$(date)] Token reuse detected for $TENANT_ID, count=1. Monitoring."
```

### Option 2: Revoke Client (Medium-Risk, Client Bug Suspected)

One client is misbehaving (retrying with the same token).

**Action:** Revoke all tokens for that client:

```bash
CLIENT_ID="oauth_client_abc123"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  UPDATE oauth_consents
  SET revoked_at = now()
  WHERE tenant_id = '$TENANT_ID' AND client_id = '$CLIENT_ID'
  RETURNING id, client_id, revoked_at;
"
```

Notify the client owner: "Your client was revoked due to token reuse pattern. Update your code to not retry with the same refresh token; always use the new token returned by the refresh endpoint."

### Option 3: Suspend Tenant (High-Risk, Compromise Suspected)

Many reuse events, other suspicious activity, or confirmed compromise.

**Action:** Suspend the tenant immediately (see [suspend_tenant.md](suspend_tenant.md)):

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  UPDATE tenants
  SET status = 'suspended', updated_at = now()
  WHERE id = '$TENANT_ID'
  RETURNING id, email, status;
"

# Revoke all Cognito sessions
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
  --output text)

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT cognito_sub FROM tenant_identities WHERE tenant_id = '$TENANT_ID'
" | while read sub; do
  aws cognito-idp admin-user-global-sign-out \
    --user-pool-id "$USER_POOL_ID" \
    --username "$sub" \
    --region ap-south-1
done
```

Notify the user: "Your account has been suspended due to suspected security compromise. Contact support@mem-mcp.local if this was not you."

## Verification

After remediation:

1. **Check alarm status:**
   ```bash
   aws cloudwatch describe-alarms \
     --alarm-names "mem-mcp-auth-token-reuse" \
     --region ap-south-1 \
     --query 'MetricAlarms[0].StateValue'
   ```
   Should return `OK` (no new reuse events in 5 minutes).

2. **Confirm no new audit events:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT COUNT(*) FROM audit_log
     WHERE event_type = 'token' AND action = 'reused'
     AND tenant_id = '$TENANT_ID'
     AND created_at > now() - interval '10 minutes';
   "
   ```
   Should return `0`.

3. **For tenant suspension, verify they cannot sign in:**
   - Attempt login with their Cognito credentials
   - Should fail with `NotAuthorizedException` or `LimitExceededException` (Cognito blocks suspended users)

## Notes

- **Token reuse is rare:** In normal operation, each client requests a new token once and uses it until expiration. Reuse is a red flag.
- **Refresh token lifetime:** Refresh tokens are valid for 30 days. If a token is reused after 30 days, it's already expired and harmless.
- **Client bug vs. attack:** If a single client is reusing tokens, it's likely a retry loop (bug). If many different clients reuse different tokens, it's likely token theft (attack).
- **Cognito rate limiting:** Cognito also has built-in rate limiting. If a token is reused too many times, Cognito itself will block the request; our audit log won't record it (because the request never reaches our auth service).

See also: [suspend_tenant.md](suspend_tenant.md) (suspension steps), [rotate_jwt_keys.md](rotate_jwt_keys.md) (JWT key rotation).
