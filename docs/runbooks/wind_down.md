# Wind Down

## Purpose

Gracefully shut down the entire mem-mcp service. This is used when you decide to stop running v1 (e.g., end of closed beta, service migration, or permanent closure). The process includes user notification, a final data export window, backup creation, and then complete infrastructure teardown via `destroy.sh`.

## Prerequisites

- SSH access to EC2
- AWS CLI configured with appropriate credentials
- Access to operator email to send notifications
- Downtime window scheduled (notify users in advance)
- Final backups are in S3 (automatic if running the daily backup job)

## Steps

### 1. Announce shutdown (2 weeks before)

Send an email to all users:

```
Subject: mem-mcp will shut down on [DATE]

Dear mem-mcp user,

We are shutting down the mem-mcp service on [DATE] at [TIME] UTC due to [reason: end of beta, service migration, etc.].

You have until [DATE] to export your data. After the service shuts down, no new access will be available.

To export your data, sign in and visit your profile → Export Data, or contact ops@mem-mcp.local.

Thank you for using mem-mcp.

Best regards,
Operator
```

### 2. Disable new signups (1 week before)

This prevents new users from signing up while the service is shutting down.

```bash
export MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:<password>@<host>:5432/mem_mcp"

# Clear or truncate the invited_emails table
psql "$MEM_MCP_DB_MAINT_DSN" -c "
  DELETE FROM invited_emails;
"

# Alternatively, set a feature flag in your app config to disable signups:
# (if your app supports feature flags; otherwise, just clear invites)
```

### 3. Enable export windows (1 week before shutdown)

Ensure the `/api/memories/export` endpoint is active and working:

```bash
# Test the export endpoint
curl -H "Authorization: Bearer <test-token>" \
  https://mem-mcp.local/api/memories/export | jq .
```

If users need help, provide instructions:

```markdown
## How to export your data

1. Sign in at https://mem-mcp.local
2. Go to Profile → Data Export
3. Click "Export my data"
4. Download the JSON file
5. Save it somewhere safe
```

### 4. Final backup (1 day before shutdown)

Create a final backup of the entire database:

```bash
ssh -i ~/mem-mcp-ops.pem ubuntu@<elastic-ip>

# On the EC2 instance, as the postgres user
sudo -u postgres pg_dump_to_s3.sh

# Verify the backup was created
BACKUP_BUCKET=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`BackupBucketName`].OutputValue' \
  --output text)

aws s3 ls s3://$BACKUP_BUCKET --recursive | tail -5
```

### 5. Stop the application

On the EC2 instance:

```bash
ssh -i ~/mem-mcp-ops.pem ubuntu@<elastic-ip>

# Stop the application and web service
sudo systemctl stop mem-mcp mem-web caddy

# Stop cron jobs (if running backups, etc.)
sudo systemctl stop mem-mcp-backup.timer  # if it exists

# Verify they are stopped
sudo systemctl status mem-mcp mem-web caddy
```

### 6. Send final notification

Email users that the service is now down:

```
Subject: mem-mcp service is now offline

The mem-mcp service has been shut down as announced.

Data export is no longer available. If you needed to export your data, please contact ops@mem-mcp.local within 30 days.

Thank you for using mem-mcp.

Operator
```

### 7. Run the destruction script

Now destroy all AWS infrastructure:

```bash
cd /codes/ai-work/memory-man

# Run the full destruction script (non-idempotent but safe if called once)
bash deploy/scripts/destroy.sh

# The script will:
# 1. Drain and delete all Cognito users
# 2. Delete all S3 objects (except versioned backups in retention buckets)
# 3. Terminate the EC2 instance
# 4. Delete the RDS PostgreSQL database
# 5. Delete Lambda functions, API Gateway, CloudWatch resources
# 6. Delete the CloudFormation stack
# 7. Verify zero remaining resources before exiting
```

Expected output:

```
[*] Step 1: Draining Cognito users ...
[*] Step 2: Emptying S3 buckets ...
[*] Step 3: Terminating EC2 instances ...
[*] Step 4: Deleting RDS database ...
[*] Step 5: Deleting Lambda functions ...
[*] Step 6: Deleting CloudFormation stacks ...
[+] Destruction complete. Verifying account is clean...
[+] Zero cost state achieved. AWS account is clean.
```

### 8. Verify zero cost state

Run the verification script:

```bash
bash deploy/scripts/verify_destroy.sh

# Expected output: All checks pass, zero resources remaining
```

Check the AWS Cost Explorer to confirm no ongoing charges:

```bash
# After ~30 minutes for eventual consistency
aws ce get-cost-and-usage \
  --time-period Start=2026-05-01,End=2026-05-04 \
  --metrics "BlendedCost" \
  --granularity DAILY \
  --region ap-south-1 | jq '.ResultsByTime[]'
```

Cost should drop to ~$0 (except for reserved capacity, if any).

### 9. Archive operational logs (optional)

Store logs for potential future audit:

```bash
# Download CloudWatch logs before they are deleted
aws logs describe-log-groups --region ap-south-1 | jq '.logGroups[].logGroupName' | grep mem-mcp | while read lg; do
  aws logs create-export-task \
    --log-group-name "$lg" \
    --from $(date -d '30 days ago' +%s)000 \
    --to $(date +%s)000 \
    --destination s3://my-archive-bucket \
    --destination-prefix "mem-mcp-logs/$(date +%Y-%m-%d)" \
    --region ap-south-1
done

# Wait a few minutes, then verify the export completed
aws s3 ls s3://my-archive-bucket/mem-mcp-logs/ --recursive
```

## Verification

1. **All AWS resources deleted:**
   ```bash
   aws cloudformation describe-stacks \
     --stack-name mem-mcp-prod \
     --region ap-south-1
   ```
   Should return an error (stack not found).

2. **RDS instance deleted:**
   ```bash
   aws rds describe-db-instances \
     --db-instance-identifier mem-mcp-postgres \
     --region ap-south-1
   ```
   Should return an error (instance not found).

3. **EC2 instance terminated:**
   ```bash
   aws ec2 describe-instances \
     --filters "Name=tag:Project,Values=mem-mcp" \
     --region ap-south-1 | jq '.Reservations'
   ```
   Should be empty.

4. **S3 backups remain (for compliance):**
   ```bash
   BACKUP_BUCKET=$(aws s3 ls | grep mem-mcp | awk '{print $3}')
   aws s3 ls s3://$BACKUP_BUCKET --recursive | head -5
   ```
   Backups should still exist (they have `DeletionPolicy: Retain`).

5. **Zero cost confirmed:**
   Check AWS Cost Explorer and Billing Dashboard. Monthly cost should be ~$0 (excluding S3 backup storage, which is negligible).

## Cleanup (30 days after shutdown)

After 30 days, if you are confident there are no recovery needs:

1. **Delete S3 backup buckets (optional):**
   ```bash
   aws s3 rm s3://<backup-bucket> --recursive
   aws s3api delete-bucket --bucket <backup-bucket> --region ap-south-1
   ```

2. **Delete CloudWatch alarms:**
   ```bash
   aws cloudwatch delete-alarms --alarm-names "mem-mcp-*" --region ap-south-1
   ```

3. **Delete SSM parameters:**
   ```bash
   aws ssm describe-parameters \
     --filters "Key=Name,Values=/mem-mcp/" \
     --region ap-south-1 | jq '.Parameters[].Name' | while read param; do
     aws ssm delete-parameter --name "$param" --region ap-south-1
   done
   ```

## Notes

- **User data is not purged immediately:** The final backups in S3 contain all user data. These are retained for compliance/audit. Delete them only if legally allowed and operationally safe.
- **Cognito users:** The `destroy.sh` script deletes the Cognito user pool, but AWS may retain some user metadata for 30 days (eventual consistency). This does not affect you operationally.
- **Backups as archives:** Keep the final S3 backup as a long-term archive (store elsewhere if needed). This can be used to recover data if users have legal recourse within 30 days of shutdown.
- **Cost:** The cost of keeping S3 backups is ~$0.023 per GB per month. A year of backups for 1000 users might cost ~$10-20/year.

See also: [destroy_partial.md](destroy_partial.md) (partial teardown), [restore_from_backup.md](restore_from_backup.md) (restoration).
