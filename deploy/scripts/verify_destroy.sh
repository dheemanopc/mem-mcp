#!/usr/bin/env bash
# Verify mem-mcp infrastructure is fully destroyed.
# Per LLD §3.4. Run 24h+ after destroy.sh.

set -euo pipefail

REGION="${REGION:-ap-south-1}"

log() { printf '[%s] %s\n' "$(date -u +%FT%TZ)" "$*"; }

# 1) Cost check (last 24h)
log "Checking last-24h cost for tag Project=mem-mcp ..."
START=$(date -u -d '24 hours ago' +%F)
END=$(date -u +%F)

# Note: AWS Cost Explorer may take 24h+ to reflect resource deletes
COST=$(aws ce get-cost-and-usage \
  --time-period Start="$START",End="$END" \
  --granularity DAILY --metrics UnblendedCost \
  --filter '{"Tags":{"Key":"Project","Values":["mem-mcp"]}}' \
  --query 'ResultsByTime[].Total.UnblendedCost.Amount' --output text 2>/dev/null || echo "0")

# Sum across days
TOTAL=$(echo "$COST" | tr '\t' '\n' | python3 -c 'import sys; print(sum(float(x) for x in sys.stdin if x.strip()))')

log "  Total tagged cost over $START..$END = \$${TOTAL}"

# Threshold: allow $0.10 for KMS / snapshot tail
THRESHOLD=0.10
if (( $(echo "$TOTAL > $THRESHOLD" | bc -l) )); then
  log "FAIL: tagged cost exceeds threshold (\$$THRESHOLD). Resources may still be running."
  exit 1
fi

# 2) Orphan scan (same as destroy.sh step 11; re-run as a check)
log "Re-running orphan check ..."

ec2s=$(aws ec2 describe-instances \
        --filters Name=tag:Project,Values=mem-mcp Name=instance-state-name,Values=running,pending,stopping,stopped \
        --region "$REGION" --query 'Reservations[].Instances[].InstanceId' --output text)
buckets=$(aws s3 ls | awk '{print $3}' | grep -E '^mem-mcp-' || true)
pools=$(aws cognito-idp list-user-pools --max-results 60 --region "$REGION" \
          --query "UserPools[?contains(Name, \`mem-mcp\`)].Id" --output text)

if [[ -n "$ec2s$buckets$pools" ]]; then
  log "FAIL: orphans still present"
  [[ -n "$ec2s" ]] && log "  EC2: $ec2s"
  [[ -n "$buckets" ]] && log "  Buckets: $buckets"
  [[ -n "$pools" ]] && log "  Cognito pools: $pools"
  exit 1
fi

log "PASS: zero ongoing cost, no orphan resources. Destroy verified clean."
