#!/usr/bin/env bash
# Restore mem_mcp DB from an S3 backup. Interactive — prompts for date.
# Per FR-14.2.2. Run manually as part of restore drills (FR-14.2.3).
set -euo pipefail

# Required env (same as pg_dump_to_s3.sh + RESTORE_TARGET_DB):
#   PGUSER, PGPASSWORD, PGHOST, PGPORT
#   RESTORE_TARGET_DB   — name of the fresh DB to restore into (must NOT exist)
#   BACKUP_S3_BUCKET, BACKUP_S3_PREFIX (default: db)
#   BACKUP_GPG_PASSPHRASE_FILE
#   AWS_REGION

PREFIX="${BACKUP_S3_PREFIX:-db}"

# Prompt for date (or accept as $1)
DATE="${1:-}"
if [ -z "$DATE" ]; then
    echo "Recent backups in s3://${BACKUP_S3_BUCKET}/${PREFIX}/:"
    aws s3 ls "s3://${BACKUP_S3_BUCKET}/${PREFIX}/" | tail -20
    read -r -p "Date to restore (YYYY-MM-DD): " DATE
fi

S3_KEY="${PREFIX}/${DATE}.sql.gz.gpg"
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

ENC="$TMP/${DATE}.sql.gz.gpg"
DUMP="$TMP/${DATE}.dump"

echo "[$(date -u +%H:%M:%S)] s3 download s3://${BACKUP_S3_BUCKET}/${S3_KEY}..."
aws s3 cp --no-progress "s3://${BACKUP_S3_BUCKET}/${S3_KEY}" "$ENC"

echo "[$(date -u +%H:%M:%S)] gpg decrypt..."
gpg --batch --yes \
    --passphrase-file "$BACKUP_GPG_PASSPHRASE_FILE" \
    --decrypt --output "$DUMP" "$ENC"

echo "[$(date -u +%H:%M:%S)] createdb ${RESTORE_TARGET_DB}..."
createdb "$RESTORE_TARGET_DB"

echo "[$(date -u +%H:%M:%S)] pg_restore..."
pg_restore --dbname="$RESTORE_TARGET_DB" --no-owner --no-privileges --jobs=2 "$DUMP"

echo "[$(date -u +%H:%M:%S)] Done. Restored to DB '${RESTORE_TARGET_DB}'."
