# Privacy Policy

**Effective:** 2026-05-03 (closed beta — subject to change before public launch)

> This is the source-of-truth markdown version. The web/ pages render this content; keep them in sync.

## What we collect

- Your Google account email and unique sub identifier (via Cognito).
- The memory contents you choose to save (decisions, facts, notes, snippets, questions).
- Tags and metadata you attach to memories.
- Audit log: timestamps and types of operations you perform.
- Operational metadata: connection IP, user agent (for session security only).

## What we do not collect

- Browsing history, location, contacts, or any data outside what you explicitly write into mem-mcp.
- We do not share data with third parties (no advertising, no analytics SDKs).

## Where data lives

AWS Mumbai (ap-south-1). All data at rest is encrypted (KMS). All transport is HTTPS.
Backups are encrypted (AES256) and stored in S3 in the same region.

## Your rights (DPDP)

- **Access**: download a JSON dump of all your data via /data/export.
- **Erasure**: request deletion via /data/delete. 24h grace period to cancel; full hard-delete within 7 days of confirmation.
- **Correction**: edit memories via the dashboard.
- **Portability**: same as Access — JSON export.

## Audit and retention

- Audit logs retained 730 days, then purged.
- After tenant deletion, audit log entries are anonymized at the 90d mark.
- Memories: retention configurable per-tenant (default 365d). Soft-deleted memories recoverable for 30d.

## Contact

Operator: anand@dheemantech.com
