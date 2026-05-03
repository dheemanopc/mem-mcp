#!/usr/bin/env bash
# Nightly mem_mcp DB backup → encrypted → S3.
# Per FR-14.2.1. Run by mem-mcp-backup.timer at 02:30 IST (21:00 UTC).
set -euo pipefail

# Required env (set in /etc/mem-mcp/env or systemd EnvironmentFile):
#   PGUSER, PGPASSWORD, PGDATABASE, PGHOST, PGPORT
#   BACKUP_S3_BUCKET    — e.g. mem-mcp-backups-prod-ap-south-1
#   BACKUP_S3_PREFIX    — defaults to db
#   BACKUP_GPG_PASSPHRASE_FILE  — path to a file holding the AES256 passphrase (mode 0600)
#   AWS_REGION
#   CW_NAMESPACE        — defaults to mem_mcp/backup

PREFIX="${BACKUP_S3_PREFIX:-db}"
NS="${CW_NAMESPACE:-mem_mcp/backup}"
DATE=$(date -u +%Y-%m-%d)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

DUMP="$TMP/${PGDATABASE}-${DATE}.dump"
ENC="$DUMP.gpg"
S3_KEY="${PREFIX}/${DATE}.sql.gz.gpg"

echo "[$(date -u +%H:%M:%S)] pg_dump..."
pg_dump --format=custom --compress=9 --no-owner --no-privileges "$PGDATABASE" > "$DUMP"

echo "[$(date -u +%H:%M:%S)] gpg encrypt..."
gpg --cipher-algo AES256 --batch --yes \
    --passphrase-file "$BACKUP_GPG_PASSPHRASE_FILE" \
    --symmetric --output "$ENC" "$DUMP"

SIZE=$(stat -c%s "$ENC")
echo "[$(date -u +%H:%M:%S)] s3 upload (${SIZE} bytes) → s3://${BACKUP_S3_BUCKET}/${S3_KEY}"
aws s3 cp --no-progress "$ENC" "s3://${BACKUP_S3_BUCKET}/${S3_KEY}"

echo "[$(date -u +%H:%M:%S)] CW metric publish..."
aws cloudwatch put-metric-data \
    --namespace "$NS" \
    --metric-name backup.success \
    --value 1 \
    --unit Count \
    --dimensions Database="${PGDATABASE}"

echo "[$(date -u +%H:%M:%S)] Done."
