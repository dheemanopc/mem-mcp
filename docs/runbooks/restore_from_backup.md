# Restore from Backup

## Purpose

Recover the entire PostgreSQL database from an S3 backup in case of data corruption, accidental deletion, or disaster. This walkthrough covers pre-flight checks, data loss assessment, restore steps, and post-restore smoke tests.

## Prerequisites

- Access to EC2 instance (SSH)
- AWS CLI configured with S3 access
- `restore_from_s3.sh` script in `deploy/scripts/`
- List of available backups (stored in S3 `backup_bucket`)
- Downtime window scheduled (restore takes ~10-30 min depending on backup size)

## Steps

### 1. Pre-flight checks

**Check S3 backup availability:**

```bash
BACKUP_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`BackupBucketName`].OutputValue' \
  --output text)

aws s3 ls s3://$BACKUP_BUCKET --recursive | tail -20
```

Expected output: list of dated backup files, e.g., `backup_2026-05-03_14-22-33.sql.gz`.

**Choose the backup to restore:**

Select the most recent backup before the incident, or ask the user which date they want. Note the full S3 path.

### 2. Data loss assessment

Before restoring, evaluate what data will be lost (everything since the backup):

```bash
BACKUP_DATE="2026-05-03_14-22-33"  # Example backup date
CURRENT_MEMORIES=$(psql -h /var/run/postgresql -U mem_app -d mem_mcp -t -c \
  "SELECT COUNT(*) FROM memories;")
echo "Current memories in DB: $CURRENT_MEMORIES"
echo "Memories after restore will be those as of $BACKUP_DATE"
echo "All changes since then will be LOST."
```

**Notify users** if necessary (e.g., "API down until [time], some data from last 2 hours lost").

### 3. Stop the application

On the EC2 instance:

```bash
ssh -i ~/mem-mcp-ops.pem ubuntu@<elastic-ip>

# Stop the app and web service
sudo systemctl stop mem-mcp mem-web

# Verify they are stopped
sudo systemctl status mem-mcp mem-web
```

### 4. Perform pre-restore database snapshot (optional but recommended)

Create a dump of the corrupted state for analysis later:

```bash
sudo -u postgres pg_dump \
  --verbose \
  -f /tmp/corrupted_db_backup_$(date +%s).sql \
  mem_mcp

# Compress and upload to a "postmortem" bucket
gzip /tmp/corrupted_db_backup_*.sql
aws s3 cp /tmp/corrupted_db_backup_*.sql.gz s3://$BACKUP_BUCKET/postmortems/ --region ap-south-1
```

### 5. Run the restore script

```bash
cd /codes/ai-work/memory-man

# Set environment
export BACKUP_BUCKET="<bucket-name>"
export BACKUP_DATE="2026-05-03_14-22-33"  # or full S3 path

# Run the restore script
bash deploy/scripts/restore_from_s3.sh

# The script will:
# 1. Download and decompress the backup from S3
# 2. Drop the current mem_mcp database
# 3. Create a fresh mem_mcp database
# 4. Restore from the backup SQL
# 5. Run migrations (if any migrations were added since the backup date, they run now)
```

Example output:
```
[*] Downloading s3://backup-bucket/backup_2026-05-03_14-22-33.sql.gz ...
[*] Decompressing ...
[*] Stopping mem-mcp service (if running) ...
[*] Dropping existing mem_mcp database ...
[*] Creating mem_mcp database ...
[*] Restoring from backup ...
[+] Restore completed in 18 seconds
[*] Running Alembic migrations ...
[+] Migrations applied (0 new)
[+] Restore complete
```

### 6. Post-restore smoke tests

Run the database RLS smoke test to verify isolation:

```bash
bash deploy/scripts/db_smoke.sh
# Or manually:
psql -h /var/run/postgresql -U mem_app -d mem_mcp \
  -f deploy/postgres/smoke_rls.sql
```

Expected output:
```
NOTICE:  OK: RLS fail-closed verified - no data leakage without tenant context
```

Verify data counts:

```bash
psql -h /var/run/postgresql -U mem_app -d mem_mcp -t -c "
  SELECT 'memories' as table_name, COUNT(*) as row_count FROM memories
  UNION ALL
  SELECT 'tenants', COUNT(*) FROM tenants
  UNION ALL
  SELECT 'audit_log', COUNT(*) FROM audit_log;
"
```

Spot-check a specific tenant's data:

```bash
psql -h /var/run/postgresql -U mem_app -d mem_mcp -c "
  -- Must use a real tenant_id from your backups
  SELECT id, content, created_at FROM memories
  WHERE tenant_id = '<known-tenant-id>'
  ORDER BY created_at DESC
  LIMIT 5;
"
```

### 7. Restart the application

```bash
sudo systemctl start mem-mcp mem-web

# Verify startup
sudo systemctl status mem-mcp mem-web
sudo journalctl -u mem-mcp -n 50 --no-pager
```

### 8. Verify end-to-end

From your local machine or a test client:

```bash
# Hit the /internal/health endpoint
curl -s https://mem-mcp.local/internal/health | jq

# Attempt a test memory write and read
curl -X POST https://mem-mcp.local/api/memories \
  -H "Authorization: Bearer <valid-token>" \
  -H "Content-Type: application/json" \
  -d '{"content":"test after restore"}' | jq
```

## Verification Checklist

- [ ] Backup was located and accessible in S3
- [ ] Data loss assessment completed and users notified
- [ ] Application stopped cleanly
- [ ] Restore script ran without errors
- [ ] RLS smoke test passed
- [ ] Data counts and spot-checks look correct
- [ ] Application restarted and is serving traffic
- [ ] End-to-end test (write + read) successful
- [ ] No alerts firing in CloudWatch

## Rollback

If the restore failed or went to the wrong backup:

```bash
# Stop the app again
sudo systemctl stop mem-mcp mem-web

# Try the restore again with a different backup date
export BACKUP_DATE="2026-05-02_18-00-00"  # Earlier backup
bash deploy/scripts/restore_from_s3.sh

# Or restore the corrupted state from your pre-restore snapshot
# (if you created one in step 4) — this is the only way to "undo" a restore
```

## Notes

- **Backup retention:** S3 bucket has versioning enabled; all backups are retained indefinitely (cost ~$0.023 per GB/month). Manually delete old backups if storage cost becomes a concern.
- **Backup frequency:** Backups run daily at 2 AM UTC (configurable in `bootstrap.sh`). Manual backups can be triggered via `pg_dump_to_s3.sh` on the EC2 instance.
- **RPO (Recovery Point Objective):** Up to 24 hours of data loss (since backups are once per day). For tighter RPO, increase backup frequency in `bootstrap.sh`.
- **RTO (Recovery Time Objective):** ~20 minutes (stop app, restore DB, restart app, tests).
- **Migrations:** The script automatically applies any Alembic migrations added since the backup date. If a migration fails, the restore is incomplete — investigate the migration and fix it before retrying.

See also: [db_smoke.md](db_smoke.md) (smoke test reference), [wind_down.md](wind_down.md) (full shutdown).
