# Add Beta User

## Purpose

Manually invite a new beta tester by adding their email to the `invited_emails` allowlist. Once invited, they can sign up via the web app. This is the operator's primary tool for controlling access during closed beta.

## Prerequisites

- SSH access to EC2 or local CLI access to DB
- `MEM_MCP_DB_MAINT_DSN` environment variable set (database maintenance role with BYPASSRLS)
- `seed_invite.py` script available in `deploy/scripts/`
- Invitee email address

## Steps

### Via CLI Script (Recommended)

1. **Prepare environment:**
   ```bash
   export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:<port>/mem_mcp"
   cd /codes/ai-work/memory-man
   ```

2. **Add the invite:**
   ```bash
   python deploy/scripts/seed_invite.py add user@example.com \
     --invited-by ops \
     --notes "beta tester #47"
   ```

   Expected output:
   ```
     email              user@example.com
     invited_by         ops
     invited_at         2026-05-03 14:22:33.456789+00:00
     consumed_at        (null)
     notes              beta tester #47
   ```

3. **Verify the invite was saved:**
   ```bash
   python deploy/scripts/seed_invite.py show user@example.com
   ```

4. **Send invite email (manual step):**
   Use AWS SES Console or CLI to send the signup link to the invitee:
   ```bash
   aws ses send-email \
     --from "ops@mem-mcp.local" \
     --to "user@example.com" \
     --subject "You're invited to mem-mcp beta" \
     --text "Sign up at https://mem-mcp.local/join"
   ```

   Or use the internal `SesMailer` protocol if available in the deployment tools.

### Via SQL (Manual)

If `seed_invite.py` is unavailable, use raw SQL:

```bash
export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  INSERT INTO invited_emails (email, invited_by, notes)
  VALUES ('user@example.com', 'ops', 'beta tester #47')
  ON CONFLICT (email) DO UPDATE
  SET invited_by = EXCLUDED.invited_by, notes = EXCLUDED.notes
  RETURNING email, invited_by, invited_at, consumed_at, notes;
"
```

## Verification

1. **Confirm row exists:**
   ```bash
   python deploy/scripts/seed_invite.py show user@example.com
   ```
   Should show the email, invited_by, and notes; `consumed_at` should be `(null)`.

2. **Confirm invitee can sign up:**
   - Invitee navigates to signup page
   - Enters their email
   - System calls PreSignUp Lambda → `/internal/check_invite` → queries `invited_emails`
   - If email exists and `consumed_at` is null, signup proceeds
   - On successful signup, `consumed_at` is set to current timestamp

3. **List all invites to audit:**
   ```bash
   python deploy/scripts/seed_invite.py list
   ```

## Rollback

To **revoke** an invite (prevent further signup, but keep audit trail):

```bash
python deploy/scripts/seed_invite.py revoke user@example.com
```

This sets `consumed_at` to `1970-01-01T00:00:00+00:00` (sentinel), making the row appear consumed without deleting it.

To **delete** an invite entirely:

```bash
python deploy/scripts/seed_invite.py delete user@example.com
```

## Notes

- **Invite is one-time use:** Once a user signs up with their email, `consumed_at` is updated automatically. They cannot reuse the same invite.
- **Email is case-insensitive:** The script normalizes to lowercase before insert.
- **Audit trail:** All invites (consumed, revoked, or pending) remain in the table indefinitely; `consumed_at` tracks the status.
- **Bulk adds:** To invite many users, write a loop:
  ```bash
  for email in alice@example.com bob@example.com charlie@example.com; do
    python deploy/scripts/seed_invite.py add "$email" --invited-by ops --notes "batch 1"
  done
  ```

See also: [suspend_tenant.md](suspend_tenant.md) (block an existing user post-signup).
