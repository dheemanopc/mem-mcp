#!/usr/bin/env bash
# Mint a machine (client_credentials) access token for a headless partner
# system against the REAL Cognito hosted domain. Prod analogue of
# dev-token.sh (which uses the cognito-local admin flow).
#
# Requires the mem-kite-service-client app client (040-identity.yaml) and
# its secret. Fetch inputs from SSM after deploy:
#   KITE_CLIENT_ID=$(aws ssm get-parameter --name /mem-mcp/kite/client_id \
#                     --query Parameter.Value --output text)
#   KITE_CLIENT_SECRET=$(aws ssm get-parameter --name /mem-mcp/kite/client_secret \
#                     --with-decryption --query Parameter.Value --output text)
#
# Usage:
#   eval $(KITE_CLIENT_ID=... KITE_CLIENT_SECRET=... scripts/service-token.sh)
#   curl -H "Authorization: Bearer $MEM_MCP_TOKEN" https://memsys.dheemantech.in/mcp ...
#
# Env:
#   KITE_CLIENT_ID / KITE_CLIENT_SECRET   (required)
#   COGNITO_DOMAIN   (default memauth.dheemantech.in)
#   RESOURCE_URL     (default https://memsys.dheemantech.in) — scope prefix
#   SCOPES           (default "<RESOURCE_URL>/memory.read <RESOURCE_URL>/memory.write")

set -euo pipefail

CLIENT_ID="${KITE_CLIENT_ID:?set KITE_CLIENT_ID (SSM /mem-mcp/kite/client_id)}"
CLIENT_SECRET="${KITE_CLIENT_SECRET:?set KITE_CLIENT_SECRET (SSM /mem-mcp/kite/client_secret)}"
COGNITO_DOMAIN="${COGNITO_DOMAIN:-memauth.dheemantech.in}"
RESOURCE_URL="${RESOURCE_URL:-https://memsys.dheemantech.in}"
SCOPES="${SCOPES:-${RESOURCE_URL}/memory.read ${RESOURCE_URL}/memory.write}"

RESP=$(curl -sf -X POST "https://${COGNITO_DOMAIN}/oauth2/token" \
  -u "${CLIENT_ID}:${CLIENT_SECRET}" \
  -H 'Content-Type: application/x-www-form-urlencoded' \
  --data-urlencode 'grant_type=client_credentials' \
  --data-urlencode "scope=${SCOPES}") || {
  echo "# service-token.sh: token request to ${COGNITO_DOMAIN} failed" >&2
  echo "# check client id/secret and that AllowedOAuthFlows includes client_credentials" >&2
  exit 1
}

TOKEN=$(echo "$RESP" | jq -r '.access_token')
if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
  echo "# service-token.sh: no access_token in response: $RESP" >&2
  exit 1
fi

echo "export MEM_MCP_TOKEN=$TOKEN"
