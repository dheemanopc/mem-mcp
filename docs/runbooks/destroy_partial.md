# Partial Infrastructure Destruction

This runbook covers three partial-destroy scenarios, each preserving a critical resource tier while removing the rest.

**See LLD §3.5 for full context.**

---

## Scenario 1: Keep Database Only

**Purpose:** Save the PostgreSQL data for migration to a new host or restore later.

1. Stop application services on EC2:
   ```bash
   ssh -i ~/mem-mcp-ops.pem ubuntu@<eip> \
     "sudo systemctl stop mem-mcp mem-web caddy"
   ```

2. Perform final backup:
   ```bash
   # On EC2, as postgres:
   ssh ... sudo -u postgres pg_dump_to_s3.sh
   # Verify backup in S3 restore bucket
   ```

3. Run destroy.sh EXCEPT steps 2–3 (Cognito drain), 7 (root stack delete):
   - Modify `destroy.sh` to comment out or skip those steps (or run the steps individually).
   - Or: stop services, deregister from ASG, terminate the EC2 instance manually, then run destroy.sh as-is (it will skip the already-gone resources).

4. PostgreSQL data persists. Restore on new host:
   ```bash
   aws s3 cp s3://<backup_bucket>/<dump.sql.gz> - | gunzip | psql -U postgres
   ```

---

## Scenario 2: Keep S3 Backups (Version History)

**Purpose:** Preserve all backup snapshots and version history indefinitely.

1. The backup bucket already has `DeletionPolicy: Retain` in 030-storage.yaml (see template).

2. Run destroy.sh as-is. Step 4 (empty backup bucket) will delete current objects and delete markers but **not** noncurrent versions if they are locked.

3. **To permanently retain:** remove the backup bucket from the CFN stack *before* destroy:
   ```bash
   # After step 6 (disable termination protection) but before step 7 (delete root stack):
   aws s3api put-bucket-versioning --bucket <backup_bucket> \
     --versioning-configuration Status=Suspended
   ```
   Then run steps 7–11 of destroy.sh. The bucket will be left behind.

4. Restore from retained bucket:
   ```bash
   aws s3api list-object-versions --bucket <backup_bucket> | jq '.Versions'
   ```

---

## Scenario 3: Keep Cognito Users (Rare)

**Purpose:** Preserve user identities and consent records for audit or future reactivation.

1. **Export users before destroy:**
   ```bash
   USER_POOL_ID=$(aws cloudformation describe-stacks --stack-name mem-mcp-prod \
     --region ap-south-1 --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
     --output text)
   aws cognito-idp list-users --user-pool-id "$USER_POOL_ID" \
     --region ap-south-1 | jq '.Users' > cognito_users_backup.json
   ```

2. Modify destroy.sh: comment out or skip step 2 (drain Cognito users).

3. Run destroy.sh. Step 7 (delete root stack) will delete the Cognito user pool by default.

4. **To prevent pool deletion:** set `DeletionPolicy: Retain` on the Cognito user pool in 040-identity.yaml before deployment. Then the pool survives the stack delete.

5. Restore users from JSON backup to a new pool (future):
   ```bash
   # Out of scope; requires admin API calls to re-create users and identities.
   ```

---

## Notes

- **Partial destroy is NOT idempotent** — operator must track which resources were retained and manage cleanup manually.
- **Cost** — retained resources (DB, backups, pools) continue to incur charges. Monitor CloudWatch.
- **Cross-region stacks** — cert stack (us-east-1) is always deleted unless you skip step 8 manually.
- **SSM parameters and snapshots** — intentionally never auto-deleted (per LLD §3.3); manage separately.

For questions, see the full LLD §3 or contact anand@dheemantech.com.
