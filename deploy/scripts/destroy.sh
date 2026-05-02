#!/usr/bin/env bash
# mem-mcp infrastructure destruction script.
# 11 idempotent steps per LLD §3.2.
#
# Usage:
#   DESTROY_CONFIRM=mem-mcp-prod-yes-i-mean-it ./deploy/scripts/destroy.sh
#
# Or override env vars:
#   STACK_NAME=mem-mcp-staging REGION=ap-south-1 DESTROY_CONFIRM=... ./deploy/scripts/destroy.sh

set -euo pipefail

# === Configuration ===
STACK_NAME="${STACK_NAME:-mem-mcp-prod}"
BOOTSTRAP_STACK_NAME="${BOOTSTRAP_STACK_NAME:-mem-mcp-cfn-bootstrap}"
CERT_STACK_NAME="${CERT_STACK_NAME:-mem-mcp-cert-use1}"
REGION="${REGION:-ap-south-1}"
US_EAST_1="us-east-1"
KMS_KEY_ALIAS="${KMS_KEY_ALIAS:-alias/mem-mcp}"
KMS_PENDING_WINDOW_DAYS="${KMS_PENDING_WINDOW_DAYS:-30}"

log()  { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }
warn() { printf '[%s] WARN: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; }
fail() { printf '[%s] FAIL: %s\n' "$(date -u +%FT%TZ)" "$*" >&2; exit 1; }
sec()  { printf '\n==== Step %s: %s ====\n' "$1" "$2"; }

# Helper: aws call that returns empty string on failure (e.g. resource not found)
aws_safe() { aws "$@" 2>/dev/null || true; }

# Discover values from the existing stack BEFORE we start deleting things
discover_resources() {
  log "Discovering resources from $STACK_NAME (if exists) ..."
  ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
  STACK_STATUS=$(aws_safe cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
                  --query 'Stacks[0].StackStatus' --output text)
  if [[ -z "$STACK_STATUS" || "$STACK_STATUS" == "None" ]]; then
    warn "  Root stack $STACK_NAME does not exist; many steps will be no-ops."
    EC2_ID=""
    EIP=""
    BACKUP_BUCKET=""
    USER_POOL_ID=""
  else
    EC2_ID=$(aws_safe cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
              --query "Stacks[0].Outputs[?OutputKey=='Ec2InstanceId'].OutputValue | [0]" --output text)
    EIP=$(aws_safe cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
            --query "Stacks[0].Outputs[?OutputKey=='ElasticIp'].OutputValue | [0]" --output text)
    BACKUP_BUCKET=$(aws_safe cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
                      --query "Stacks[0].Outputs[?OutputKey=='BackupBucketName'].OutputValue | [0]" --output text)
    USER_POOL_ID=$(aws_safe cloudformation describe-stacks --stack-name "$STACK_NAME" --region "$REGION" \
                    --query "Stacks[0].Outputs[?OutputKey=='CognitoUserPoolId'].OutputValue | [0]" --output text)
  fi
  CFN_BUCKET="mem-mcp-cfn-${ACCOUNT_ID}-aps1"
  KMS_KEY_ID=$(aws_safe kms describe-key --key-id "$KMS_KEY_ALIAS" --region "$REGION" \
                  --query 'KeyMetadata.KeyId' --output text)
}

step1_safety_gate() {
  sec 1 "SAFETY GATE"
  if [[ "${DESTROY_CONFIRM:-}" != "mem-mcp-prod-yes-i-mean-it" ]]; then
    fail "Set DESTROY_CONFIRM=mem-mcp-prod-yes-i-mean-it to proceed."
  fi

  cat <<EOF
About to PERMANENTLY destroy:
  Account:           $ACCOUNT_ID
  Region:            $REGION
  Root stack:        $STACK_NAME (status=$STACK_STATUS)
  Cert stack:        $CERT_STACK_NAME (us-east-1)
  Bootstrap stack:   $BOOTSTRAP_STACK_NAME
  EC2 instance:      ${EC2_ID:-<none>}
  Elastic IP:        ${EIP:-<none>}
  Backup bucket:     ${BACKUP_BUCKET:-<none>}
  CFN bucket:        $CFN_BUCKET
  Cognito pool:      ${USER_POOL_ID:-<none>}
  KMS key:           ${KMS_KEY_ID:-<none>} (will be SCHEDULED for deletion in ${KMS_PENDING_WINDOW_DAYS} days)

This is irreversible. Backups in S3 are version-history bucket; pre-destroy, run pg_dump_to_s3.sh manually if you want a final snapshot — see LLD §3.1.
EOF

  read -r -p "Type 'destroy' to proceed (anything else aborts): " confirm
  [[ "$confirm" == "destroy" ]] || fail "Aborted by user."
}

step2_drain_cognito_users() {
  sec 2 "DRAIN COGNITO USERS"
  if [[ -z "$USER_POOL_ID" ]]; then log "  No user pool; skipping."; return; fi
  local users
  users=$(aws_safe cognito-idp list-users --user-pool-id "$USER_POOL_ID" --region "$REGION" \
            --query 'Users[].Username' --output text)
  if [[ -z "$users" ]]; then log "  Pool empty."; return; fi
  for user in $users; do
    log "  AdminDeleteUser $user"
    aws_safe cognito-idp admin-delete-user --user-pool-id "$USER_POOL_ID" --username "$user" --region "$REGION"
  done
}

step3_drain_dcr_clients() {
  sec 3 "DRAIN DCR-CREATED USER POOL CLIENTS"
  if [[ -z "$USER_POOL_ID" ]]; then log "  No user pool; skipping."; return; fi
  # List all clients; filter to those NOT managed by CFN
  local client_ids
  client_ids=$(aws_safe cognito-idp list-user-pool-clients --user-pool-id "$USER_POOL_ID" --region "$REGION" \
                --max-results 60 --query 'UserPoolClients[].ClientId' --output text)
  for cid in $client_ids; do
    # Check client name: keep CFN-managed web client, delete DCR-created ones
    local name
    name=$(aws_safe cognito-idp describe-user-pool-client --user-pool-id "$USER_POOL_ID" --client-id "$cid" --region "$REGION" \
            --query 'UserPoolClient.ClientName' --output text)
    if [[ "$name" == "mem-web-client" ]]; then
      log "  Keeping CFN-managed client $name ($cid)"
      continue
    fi
    log "  DeleteUserPoolClient $name ($cid)"
    aws_safe cognito-idp delete-user-pool-client --user-pool-id "$USER_POOL_ID" --client-id "$cid" --region "$REGION"
  done
}

step4_empty_backup_bucket() {
  sec 4 "EMPTY S3 BACKUP BUCKET"
  if [[ -z "$BACKUP_BUCKET" ]]; then log "  No bucket name; skipping."; return; fi
  if ! aws_safe s3api head-bucket --bucket "$BACKUP_BUCKET" >/dev/null; then
    log "  Bucket $BACKUP_BUCKET does not exist; skipping."
    return
  fi
  log "  Removing all current objects from $BACKUP_BUCKET"
  aws s3 rm "s3://$BACKUP_BUCKET" --recursive --quiet || true
  log "  Removing all noncurrent versions + delete markers"
  # Use aws s3api list-object-versions + delete-objects in batches of 1000
  while :; do
    local payload
    payload=$(aws s3api list-object-versions --bucket "$BACKUP_BUCKET" \
        --query '{Objects: Versions[].{Key: Key, VersionId: VersionId}, DeleteMarkers: DeleteMarkers[].{Key: Key, VersionId: VersionId}}' \
        --output json 2>/dev/null || echo '{}')
    local total
    total=$(echo "$payload" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len((d.get("Objects") or []))+len((d.get("DeleteMarkers") or [])))')
    [[ "$total" == "0" ]] && break
    # Combine versions + delete markers into one delete payload (max 1000)
    echo "$payload" | python3 -c '
import json, subprocess, sys
d = json.load(sys.stdin)
items = (d.get("Objects") or []) + (d.get("DeleteMarkers") or [])
for batch_start in range(0, len(items), 1000):
    batch = items[batch_start:batch_start+1000]
    payload = {"Objects": batch, "Quiet": True}
    subprocess.run(["aws","s3api","delete-objects","--bucket","'"$BACKUP_BUCKET"'","--delete",json.dumps(payload)], check=True)
'
  done
}

step5_empty_cfn_bucket() {
  sec 5 "EMPTY CFN BOOTSTRAP BUCKET"
  if ! aws_safe s3api head-bucket --bucket "$CFN_BUCKET" >/dev/null; then
    log "  Bucket $CFN_BUCKET does not exist; skipping."
    return
  fi
  log "  Removing all objects from $CFN_BUCKET"
  aws s3 rm "s3://$CFN_BUCKET" --recursive --quiet || true
  # Same noncurrent + delete-marker drain as step 4
  while :; do
    local payload total
    payload=$(aws s3api list-object-versions --bucket "$CFN_BUCKET" \
        --query '{Objects: Versions[].{Key: Key, VersionId: VersionId}, DeleteMarkers: DeleteMarkers[].{Key: Key, VersionId: VersionId}}' \
        --output json 2>/dev/null || echo '{}')
    total=$(echo "$payload" | python3 -c 'import json,sys; d=json.load(sys.stdin); print(len((d.get("Objects") or []))+len((d.get("DeleteMarkers") or [])))')
    [[ "$total" == "0" ]] && break
    echo "$payload" | python3 -c '
import json, subprocess, sys
d = json.load(sys.stdin)
items = (d.get("Objects") or []) + (d.get("DeleteMarkers") or [])
for batch_start in range(0, len(items), 1000):
    batch = items[batch_start:batch_start+1000]
    subprocess.run(["aws","s3api","delete-objects","--bucket","'"$CFN_BUCKET"'","--delete",json.dumps({"Objects": batch, "Quiet": True})], check=True)
'
  done
}

step6_disable_termination_protection() {
  sec 6 "DISABLE EC2 TERMINATION PROTECTION"
  if [[ -z "$EC2_ID" ]]; then log "  No EC2 ID; skipping."; return; fi
  log "  Disabling termination protection on $EC2_ID"
  aws_safe ec2 modify-instance-attribute --instance-id "$EC2_ID" --no-disable-api-termination --region "$REGION"
  # Cognito user pool DeletionProtection
  if [[ -n "$USER_POOL_ID" ]]; then
    log "  Disabling DeletionProtection on Cognito user pool $USER_POOL_ID"
    aws_safe cognito-idp update-user-pool --user-pool-id "$USER_POOL_ID" --deletion-protection INACTIVE --region "$REGION"
  fi
}

step7_delete_root_stack() {
  sec 7 "DELETE ROOT STACK"
  if [[ -z "$STACK_STATUS" || "$STACK_STATUS" == "None" ]]; then
    log "  Root stack already deleted; skipping."
    return
  fi
  log "  aws cloudformation delete-stack --stack-name $STACK_NAME"
  aws cloudformation delete-stack --stack-name "$STACK_NAME" --region "$REGION"
  log "  Waiting for stack-delete-complete (this takes 5-15 min) ..."
  aws cloudformation wait stack-delete-complete --stack-name "$STACK_NAME" --region "$REGION" || \
    fail "Root stack delete did not complete. Check CFN console for failed resource(s)."
  log "  Root stack deleted."
}

step8_delete_cert_stack() {
  sec 8 "DELETE US-EAST-1 CERT STACK"
  if ! aws_safe cloudformation describe-stacks --stack-name "$CERT_STACK_NAME" --region "$US_EAST_1" >/dev/null; then
    log "  Cert stack does not exist; skipping."
    return
  fi
  log "  Deleting $CERT_STACK_NAME in $US_EAST_1"
  aws cloudformation delete-stack --stack-name "$CERT_STACK_NAME" --region "$US_EAST_1"
  aws cloudformation wait stack-delete-complete --stack-name "$CERT_STACK_NAME" --region "$US_EAST_1" || \
    warn "Cert stack delete did not complete cleanly."
}

step9_delete_bootstrap_stack() {
  sec 9 "DELETE CFN BOOTSTRAP STACK"
  if ! aws_safe cloudformation describe-stacks --stack-name "$BOOTSTRAP_STACK_NAME" --region "$REGION" >/dev/null; then
    log "  Bootstrap stack does not exist; skipping."
    return
  fi
  log "  Deleting $BOOTSTRAP_STACK_NAME"
  aws cloudformation delete-stack --stack-name "$BOOTSTRAP_STACK_NAME" --region "$REGION"
  aws cloudformation wait stack-delete-complete --stack-name "$BOOTSTRAP_STACK_NAME" --region "$REGION" || \
    warn "Bootstrap stack delete did not complete cleanly."
}

step10_schedule_kms_deletion() {
  sec 10 "KMS SCHEDULED KEY DELETION"
  if [[ -z "$KMS_KEY_ID" ]]; then log "  No KMS key; skipping."; return; fi
  log "  Scheduling deletion of KMS key $KMS_KEY_ID in ${KMS_PENDING_WINDOW_DAYS} days"
  aws_safe kms schedule-key-deletion --key-id "$KMS_KEY_ID" \
              --pending-window-in-days "$KMS_PENDING_WINDOW_DAYS" --region "$REGION"
  cat <<EOF
  KMS key scheduled for deletion in ${KMS_PENDING_WINDOW_DAYS} days.
  To CANCEL the deletion: aws kms cancel-key-deletion --key-id $KMS_KEY_ID --region $REGION
EOF
}

step11_orphan_check() {
  sec 11 "ORPHAN CHECK"
  local orphans=0

  # EC2 instances tagged Project=mem-mcp
  local ec2s
  ec2s=$(aws ec2 describe-instances --filters Name=tag:Project,Values=mem-mcp Name=instance-state-name,Values=running,pending,stopping,stopped \
          --region "$REGION" --query 'Reservations[].Instances[].InstanceId' --output text)
  if [[ -n "$ec2s" ]]; then
    warn "  Orphan EC2 instances: $ec2s"
    orphans=$((orphans + 1))
  fi

  # S3 buckets matching mem-mcp-*
  local buckets
  buckets=$(aws s3 ls | awk '{print $3}' | grep -E '^mem-mcp-' || true)
  if [[ -n "$buckets" ]]; then
    warn "  Orphan S3 buckets: $buckets"
    orphans=$((orphans + 1))
  fi

  # Cognito user pools tagged Project=mem-mcp (or by name pattern mem-mcp-pool)
  local pools
  pools=$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
            --query "UserPools[?contains(Name, \`mem-mcp\`)].Id" --output text)
  if [[ -n "$pools" ]]; then
    warn "  Orphan Cognito user pools: $pools"
    orphans=$((orphans + 1))
  fi

  # SSM parameters under /mem-mcp/* — list but do NOT auto-delete (operator decides)
  local ssm_count
  ssm_count=$(aws ssm describe-parameters --parameter-filters "Key=Name,Option=BeginsWith,Values=/mem-mcp/" \
                --region "$REGION" --query 'length(Parameters)' --output text)
  if [[ "$ssm_count" != "0" ]]; then
    log "  Note: $ssm_count SSM parameters under /mem-mcp/ remain (intentionally not auto-deleted; see LLD §3.3). Manual cleanup if winding down."
  fi

  # Snapshots tagged Project=mem-mcp
  local snaps
  snaps=$(aws ec2 describe-snapshots --owner-ids self \
            --filters Name=tag:Project,Values=mem-mcp \
            --region "$REGION" --query 'Snapshots[].SnapshotId' --output text)
  if [[ -n "$snaps" ]]; then
    log "  Note: EBS snapshots remain (DLM-created; not auto-deleted; see LLD §3.3): $snaps"
  fi

  if [[ $orphans -gt 0 ]]; then
    fail "Orphan check FAILED: $orphans resource group(s) still present. Investigate above."
  fi
  log "  Orphan check clean (modulo intentionally-retained items: SSM params, snapshots)."
}

main() {
  discover_resources
  step1_safety_gate
  step2_drain_cognito_users
  step3_drain_dcr_clients
  step4_empty_backup_bucket
  step5_empty_cfn_bucket
  step6_disable_termination_protection
  step7_delete_root_stack
  step8_delete_cert_stack
  step9_delete_bootstrap_stack
  step10_schedule_kms_deletion
  step11_orphan_check
  log "destroy.sh complete."
}

main "$@"
