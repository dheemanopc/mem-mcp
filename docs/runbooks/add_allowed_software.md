# Add Allowed Software

## Purpose

Add a new software to the `allowed_software` allowlist. This controls which third-party integrations (e.g., external tools, plugins, SDKs) can connect to mem-mcp via DCR (Dynamic Client Registration). Only whitelisted software can initiate OAuth flows.

## Prerequisites

- `MEM_MCP_DB_MAINT_DSN` environment variable set
- Software name, vendor, and description
- Review of security/compliance requirements (see step 2)
- Documentation of the software's use case

## Review Criteria

Before adding software, the operator must verify:

1. **Is the software legitimate?**
   - Vendor is known and trustworthy
   - Software is widely used (e.g., listed in app stores, GitHub, npm registry)
   - No known security vulnerabilities in recent versions

2. **Does it follow OAuth best practices?**
   - Supports PKCE (Proof Key for Code Exchange) for public clients
   - Does not store user tokens in plaintext
   - Has a privacy policy and data handling statement

3. **What data does it request?**
   - Review the scope (e.g., `memories:read`, `memories:write`, `profile`)
   - Ensure it only requests what it needs (least privilege)
   - Document the use case (e.g., "CLI tool for bulk memory export")

4. **Is it a first-party or third-party integration?**
   - First-party: mem-mcp team develops and operates it → add with high confidence
   - Third-party: external vendor → add only after thorough review

## Steps

### 1. Gather software details

Collect the following information:

- **name**: Unique identifier (e.g., `obsidian-plugin-v1`, `zapier-action`, `slack-bot`)
- **vendor**: Organization name (e.g., `Obsidian Foundation`, `Zapier Inc.`, `Slack`)
- **description**: What the software does (e.g., "Obsidian plugin for syncing notes to mem-mcp")
- **scopes_requested**: JSON array of OAuth scopes (e.g., `["memories:read", "memories:write"]`)
- **documentation_url**: Link to official documentation or GitHub repo
- **notes**: Any special considerations or caveats (optional)

### 2. Review the software

Use the criteria above. Document your review:

```
Software: obsidian-plugin-v1
Vendor: Obsidian Foundation
Use Case: Two-way sync of notes to mem-mcp
Review Date: 2026-05-03
Reviewer: ops@mem-mcp.local

✓ Vendor is legitimate (widely used note-taking app)
✓ Supports PKCE in their OAuth implementation
✓ Requests only memories:read and memories:write scopes (appropriate)
✓ Privacy policy available at https://obsidian.md/privacy
✓ No known CVEs in current version (v1.5.0)

Approved for addition.
```

### 3. Add to the database

```bash
export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"

psql "$MEM_MCP_DB_MAINT_DSN" -c "
  INSERT INTO allowed_software (name, vendor, description, scopes_requested, documentation_url, notes, created_at)
  VALUES (
    'obsidian-plugin-v1',
    'Obsidian Foundation',
    'Obsidian plugin for syncing notes to mem-mcp',
    '[\"memories:read\", \"memories:write\"]'::jsonb,
    'https://github.com/obsidian-mem-mcp/plugin',
    'PKCE-enabled. Supports selective sync via per-note tags.',
    now()
  )
  RETURNING id, name, vendor, scopes_requested, created_at;
"
```

Expected output:

```
                  id                  |       name        |       vendor       |       scopes_requested       |          created_at
--------------------------------------+-------------------+--------------------+------------------------------+-------------------------------
 f47ac10b-58cc-4372-a567-0e02b2c3d479 | obsidian-plugin-v1 | Obsidian Foundation | ["memories:read", "memories:write"] | 2026-05-03 14:22:33.456789+00
```

### 4. Verify it's in the list

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  SELECT name, vendor, scopes_requested, created_at
  FROM allowed_software
  WHERE name = 'obsidian-plugin-v1';
"
```

### 5. Update documentation

Add an entry to the internal software registry or runbook index:

```markdown
## obsidian-plugin-v1

**Vendor:** Obsidian Foundation  
**Use Case:** Sync notes from Obsidian to mem-mcp  
**Scopes:** memories:read, memories:write  
**Docs:** https://github.com/obsidian-mem-mcp/plugin  
**Added:** 2026-05-03  
**Reviewed By:** ops  
```

## Verification

1. **Confirm the entry exists:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT COUNT(*) as total_allowed_software FROM allowed_software;
   "
   ```

2. **Test DCR with the new software:**
   The software should now be able to call `/oauth/register` with its `software_id=obsidian-plugin-v1` and receive an `client_id` and `client_secret`.

3. **Monitor for abuse:**
   Watch for unusual activity from the new software in audit logs:
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT event_type, action, COUNT(*) as count
     FROM audit_log
     WHERE metadata::jsonb->'software_id' = '\"obsidian-plugin-v1\"'
     AND created_at > now() - interval '1 day'
     GROUP BY event_type, action;
   "
   ```

## Rollback

To **remove** a software (e.g., if a vulnerability is discovered):

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  DELETE FROM allowed_software
  WHERE name = 'obsidian-plugin-v1'
  RETURNING id, name;
"
```

Existing clients registered under this software will no longer be able to request new tokens, but their existing tokens remain valid until expiration.

To **revoke all clients** registered under this software:

```bash
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  UPDATE oauth_clients
  SET revoked_at = now()
  WHERE software_id = 'obsidian-plugin-v1'
  RETURNING id, client_id, software_id, revoked_at;
"
```

## Notes

- **Scopes are advisory:** Scopes in `allowed_software` are recommendations. Clients can request different scopes during DCR; the system enforces scope validity at token request time.
- **No version in ID:** The `name` field should include version if major versions have different OAuth requirements (e.g., `obsidian-plugin-v1` vs. `obsidian-plugin-v2`). This allows fine-grained control.
- **DCR (Dynamic Client Registration):** When a software instance calls `/oauth/register` with a matching `software_id`, it receives a `client_id` and `client_secret`. This is *not* a user action; it's a software action.
- **Audit trail:** All allowed_software inserts/deletes are logged in audit_log (automatic via DB trigger, assuming your schema includes audit triggers).

See also: [suspend_tenant.md](suspend_tenant.md) (revoking tenant access), [investigate_token_reuse.md](investigate_token_reuse.md) (token security).
