#!/usr/bin/env bash
# Mint an admin@local access token via the cognito-local sidecar.
#
# Usage:
#   eval $(scripts/dev-token.sh)
#   curl -H "Authorization: Bearer $MEM_MCP_TOKEN" http://localhost:8080/mcp ...
#
# Defaults match docker/cognito-local/* seed files. Override via env if needed:
#   COGNITO_LOCAL_URL    (default http://localhost:9229)
#   POOL_ID              (default local_memmcp)
#   CLIENT_ID            (default memsys-local-client)
#   MEMSYS_DEV_USERNAME  (default admin@local) — NOT USERNAME (collides with $USER)
#   MEMSYS_DEV_PASSWORD  (default Admin@Local2026!)

set -euo pipefail

COGNITO_LOCAL_URL="${COGNITO_LOCAL_URL:-http://localhost:9229}"
POOL_ID="${POOL_ID:-local_memmcp}"
CLIENT_ID="${CLIENT_ID:-memsys-local-client}"
# MEMSYS_DEV_* prefix so we don't inherit the shell's USERNAME / PASSWORD env vars
# (e.g. USERNAME defaults to the unix login name on many distros).
USERNAME="${MEMSYS_DEV_USERNAME:-admin@local}"
PASSWORD="${MEMSYS_DEV_PASSWORD:-Admin@Local2026!}"

# Use the cognito-local AdminInitiateAuth API. Returns JSON with AccessToken,
# IdToken, RefreshToken inside AuthenticationResult.
RESP=$(curl -sf -X POST "${COGNITO_LOCAL_URL}/" \
  -H 'Content-Type: application/x-amz-json-1.1' \
  -H 'X-Amz-Target: AWSCognitoIdentityProviderService.AdminInitiateAuth' \
  -d "$(jq -nc \
        --arg pool "$POOL_ID" \
        --arg client "$CLIENT_ID" \
        --arg user "$USERNAME" \
        --arg pass "$PASSWORD" \
        '{UserPoolId:$pool, ClientId:$client, AuthFlow:"ADMIN_USER_PASSWORD_AUTH",
          AuthParameters:{USERNAME:$user, PASSWORD:$pass}}')") || {
  echo "# dev-token.sh: cognito-local auth failed against ${COGNITO_LOCAL_URL}" >&2
  echo "# is the sidecar running? try: docker compose ps cognito-local" >&2
  exit 1
}

TOKEN=$(echo "$RESP" | jq -r '.AuthenticationResult.AccessToken')
if [[ -z "$TOKEN" || "$TOKEN" == "null" ]]; then
  echo "# dev-token.sh: no AccessToken in response: $RESP" >&2
  exit 1
fi

echo "export MEM_MCP_TOKEN=$TOKEN"
