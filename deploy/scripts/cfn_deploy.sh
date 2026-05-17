#!/usr/bin/env bash
# Deploy mem-mcp CFN infrastructure correctly.
#
# Why this script (vs. raw `aws s3 sync` + `sam deploy`):
#   - Nested template 050-lambda-presignup.yaml has `Transform: AWS::Serverless-2016-10-31`
#     and a local-path CodeUri (../../../lambdas/presignup/). SAM only processes
#     transforms in the template passed to `sam deploy --template-file`; nested
#     templates with their own transforms must be packaged separately *before*
#     being uploaded to S3, otherwise CFN sees a CodeUri that isn't an s3:// URI
#     and the nested stack fails to parse.
#   - Running `aws s3 sync` on raw nested/*.yaml after `sam package` overwrites
#     the packaged version with the unpackaged one. This script syncs everything
#     EXCEPT 050, then uploads the packaged 050 separately.
#
# Usage:
#   bash deploy/scripts/cfn_deploy.sh         # full deploy
#   bash deploy/scripts/cfn_deploy.sh --preview # change-set preview only

set -euo pipefail

REGION="${REGION:-ap-south-1}"
STACK="${STACK:-mem-mcp-prod}"
BUCKET="${BUCKET:-mem-mcp-cfn-172485061306-aps1}"
SAM_PREFIX="${SAM_PREFIX:-sam-pkg-nested}"
PARAMS_FILE="${PARAMS_FILE:-infra/cfn/parameters/prod.json}"

# Source repo root regardless of cwd
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$REPO_ROOT"

if [[ ! -f "$PARAMS_FILE" ]]; then
  echo "ERROR: params file $PARAMS_FILE missing." >&2
  exit 1
fi

PREVIEW_ONLY=false
if [[ "${1:-}" == "--preview" ]]; then
  PREVIEW_ONLY=true
fi

PKG_DIR="$(mktemp -d /tmp/mem-mcp-cfn-pkg-XXXXXX)"
trap 'rm -rf "$PKG_DIR"' EXIT

echo "==> Packaging nested templates with SAM transforms"
sam package \
  --template-file infra/cfn/nested/050-lambda-presignup.yaml \
  --output-template-file "$PKG_DIR/050-lambda-presignup.yaml" \
  --s3-bucket "$BUCKET" \
  --s3-prefix "$SAM_PREFIX" \
  --region "$REGION"
echo "    packaged 050-lambda-presignup.yaml"

echo
echo "==> Syncing raw nested templates (excluding 050; we use packaged version)"
aws s3 sync infra/cfn/nested/ "s3://$BUCKET/nested/" \
  --region "$REGION" \
  --exclude '050-lambda-presignup.yaml'

echo
echo "==> Uploading packaged 050-lambda-presignup.yaml"
aws s3 cp "$PKG_DIR/050-lambda-presignup.yaml" \
  "s3://$BUCKET/nested/050-lambda-presignup.yaml" \
  --region "$REGION"

echo
echo "==> Fetching GoogleClientSecret from SSM"
GS_SEC=$(aws ssm get-parameter --region "$REGION" \
  --name /mem-mcp/cognito/google_client_secret \
  --with-decryption --query Parameter.Value --output text)
if [[ -z "$GS_SEC" || "$GS_SEC" == "None" ]]; then
  echo "ERROR: /mem-mcp/cognito/google_client_secret is unset or empty." >&2
  exit 1
fi

echo
echo "==> Building parameter overrides from $PARAMS_FILE"
PARAMS=$(python3 -c "
import json, sys
p = json.load(open('$PARAMS_FILE'))
print(' '.join(f'{k}={v}' for k,v in p.items()))
")

EXTRA_ARGS=""
if [[ "$PREVIEW_ONLY" == "true" ]]; then
  EXTRA_ARGS="--no-execute-changeset"
  echo "(preview mode: change set will be created but NOT executed)"
fi

echo
echo "==> sam deploy"
sam deploy \
  --template-file infra/cfn/root.yaml \
  --stack-name "$STACK" \
  --region "$REGION" \
  --s3-bucket "$BUCKET" \
  --s3-prefix sam-pkg \
  --capabilities CAPABILITY_NAMED_IAM CAPABILITY_AUTO_EXPAND \
  --parameter-overrides $PARAMS GoogleClientSecret="$GS_SEC" \
  --no-fail-on-empty-changeset \
  $EXTRA_ARGS
