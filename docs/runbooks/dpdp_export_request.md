# DPDP Export Request (Right to Access)

## Purpose

Fulfill a user's DPDP (Digital Personal Data Protection) "right to access" request. The user is entitled to a copy of all their personal data in a machine-readable format. This runbook covers the two operator paths: via the `memory.export` MCP tool (high-level) or raw SQL dump (low-level).

## Prerequisites

- `MEM_MCP_DB_MAINT_DSN` environment variable set
- Tenant email address or UUID
- Access to either the `memory.export` MCP tool or direct database access
- Record of the request date and user confirmation

## Steps

### Path A: Via memory.export MCP Tool (Recommended)

This is the highest-level path, assuming the MCP tool is available in your deployment environment.

1. **Identify the tenant:**
   ```bash
   export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT id, email FROM tenants WHERE email = 'user@example.com';
   "
   ```
   Save the tenant UUID.

2. **Call memory.export (via Python REPL or job runner):**
   ```bash
   python3 << 'EOF'
   import asyncio
   from mem_mcp.protocols import MemoryExporter
   # Assume your MCP implementation provides a bound instance:
   exporter = MemoryExporter()
   
   tenant_id = "f47ac10b-58cc-4372-a567-0e02b2c3d479"  # from step 1
   export_data = asyncio.run(exporter.export(tenant_id))
   
   # Write to JSON file
   import json
   with open(f"export_{tenant_id}.json", "w") as f:
       json.dump(export_data, f, indent=2, default=str)
   print(f"Exported to export_{tenant_id}.json")
   EOF
   ```

3. **Verify the export:**
   - File size > 0 and contains valid JSON
   - Top-level keys: `tenant`, `memories`, `audit_log`, `identities`, `consents`
   - Spot-check a few memory entries for correct tenant_id

4. **Deliver to user:**
   - Send via secure channel (encrypted email or secure link with expiration)
   - Include a manifest listing what is included
   - Log the delivery date and method

### Path B: Via Raw SQL Dump (When MCP Tool Unavailable)

Use this if the application is not available or you need to verify the raw data directly.

1. **Export tenant metadata:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT id, email, display_name, tier, status, created_at, updated_at, metadata
     FROM tenants
     WHERE email = 'user@example.com'
     \gexec
   " > tenant_export.json
   ```

2. **Export memories:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT id, tenant_id, content, vector, tags, created_at, updated_at, deleted_at
     FROM memories
     WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
     ORDER BY created_at
     FORMAT JSON
   " > memories_export.json
   ```

3. **Export audit log:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT id, tenant_id, event_type, resource_id, action, actor_id, created_at, metadata
     FROM audit_log
     WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
     ORDER BY created_at
     FORMAT JSON
   " > audit_log_export.json
   ```

4. **Export identities and consents:**
   ```bash
   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT id, tenant_id, cognito_sub, email, provider, linked_at
     FROM tenant_identities
     WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
     FORMAT JSON
   " > identities_export.json

   psql "$MEM_MCP_DB_MAINT_DSN" -c "
     SELECT id, tenant_id, client_id, scope_requested, scope_granted, created_at
     FROM oauth_consents
     WHERE tenant_id = 'f47ac10b-58cc-4372-a567-0e02b2c3d479'
     FORMAT JSON
   " > consents_export.json
   ```

5. **Combine into single export file:**
   ```bash
   python3 << 'EOF'
   import json
   
   export = {
       "export_date": "2026-05-03T14:22:33Z",
       "tenant_email": "user@example.com",
       "tenant": json.load(open("tenant_export.json")),
       "memories": json.load(open("memories_export.json")),
       "audit_log": json.load(open("audit_log_export.json")),
       "identities": json.load(open("identities_export.json")),
       "consents": json.load(open("consents_export.json"))
   }
   
   with open("full_export.json", "w") as f:
       json.dump(export, f, indent=2, default=str)
   
   print("Combined export written to full_export.json")
   EOF
   ```

## Verification

1. **Check file validity:**
   ```bash
   python3 -m json.tool full_export.json > /dev/null && echo "Valid JSON"
   ```

2. **Verify tenant scope:**
   - All entries in `memories`, `audit_log`, `identities`, `consents` have the correct `tenant_id`
   - No data from other tenants leaked
   - Deleted memories and revoked consents are included (do NOT filter them out; user is entitled to see what was deleted)

3. **Check timestamps:**
   - All dates are valid ISO 8601
   - Entries are chronologically ordered

## Delivery

- **Method:** Encrypted email or secure download link with expiration (48 hours)
- **Format:** JSON or CSV (user preference)
- **Documentation:** Include a data dictionary explaining each field
- **Confirmation:** Request user to confirm receipt and that the data is complete
- **Retention:** Keep a log of delivery date, method, and user confirmation

## Retention & Cleanup

- Delete the export file after user confirms receipt
- Operator retains only the audit log entry (automatic) showing "DPDP export requested and fulfilled on [date]"

## Notes

- **No personally identifiable information in filenames:** Use opaque identifiers or encrypted names for files in transit
- **Completeness:** Ensure you export ALL tables with tenant_id, not just memories
- **Deleted data included:** DPDP right to access includes the right to see what was deleted (restore from soft-delete markers)
- **Frequency:** Users may request exports multiple times; each is a separate audit event

See also: [dpdp_delete_request.md](dpdp_delete_request.md) (deletion right).
