# Rotate JWT Keys

## Purpose

Rotate the JWT signing keys used by Cognito to sign ID and access tokens. Key rotation is a security best practice that limits exposure if a key is compromised. In v1, Cognito's built-in advanced security mode handles most of the rotation; the operator's role is to monitor and verify client adoption.

## Prerequisites

- AWS CLI configured with appropriate credentials
- Cognito user pool ID (available in CloudFormation outputs)
- Knowledge of which clients are using the tokens (web app, CLI, third-party integrations)

## Context

- **Who signs tokens:** AWS Cognito (not us; we don't generate keys)
- **Key rotation frequency:** Cognito rotates automatically every 365 days; operator can manually rotate via the console or CLI
- **Client impact:** When Cognito rotates keys, clients continue to work because they fetch the new public key from Cognito's JWKS endpoint (auto-discovery). No action needed from clients if they use the standard OAuth flow.
- **Legacy tokens:** Old tokens signed with the old key remain valid for their TTL (default 1 hour for access tokens). After TTL expires, clients must refresh to get a new token signed with the new key.

## Steps

### 1. Understand the current key rotation status

```bash
USER_POOL_ID=$(aws cloudformation describe-stacks \
  --stack-name mem-mcp-prod \
  --region ap-south-1 \
  --query 'Stacks[0].Outputs[?OutputKey==`CognitoUserPoolId`].OutputValue' \
  --output text)

# List signing certificates (public keys)
aws cognito-idp get-signing-certificate \
  --user-pool-id "$USER_POOL_ID" \
  --region ap-south-1 \
  --query 'Certificate' \
  --output text
```

This shows the current public key. Cognito does not expose the private key; it is managed entirely by AWS.

### 2. Manual key rotation (if Cognito's auto-rotation is insufficient)

Cognito does not provide a direct "rotate key now" command in the CLI. However, if your Cognito user pool has advanced security enabled (recommended for v1), key rotation is automatic.

**To enable advanced security (if not already enabled):**

```bash
aws cognito-idp update-user-pool \
  --user-pool-id "$USER_POOL_ID" \
  --user-pool-tags "Key1=Value1" \
  --account-takeover-risk-configuration '{
    "LowAction": {
      "Notify": true,
      "EventAction": "MFA_IF_CONFIGURED"
    },
    "MediumAction": {
      "Notify": true,
      "EventAction": "MFA_REQUIRED"
    },
    "HighAction": {
      "Notify": true,
      "EventAction": "BLOCK"
    }
  }' \
  --region ap-south-1
```

### 3. Monitor for key rotation

Cognito rotates keys automatically. You can detect a key rotation by monitoring:

**Option A: CloudWatch Events**

Create a CloudWatch alarm that triggers when a new certificate is issued:

```bash
# View events related to certificate or key operations
aws logs tail /aws/cognito-idp/<user-pool-id> \
  --follow \
  --filter-pattern "certificate OR key OR rotate"
```

**Option B: Polling JWKS Endpoint**

Periodically fetch the public JWKS and compare to a known baseline:

```bash
python3 << 'EOF'
import requests
import json
from datetime import datetime

# Cognito JWKS endpoint
region = "ap-south-1"
user_pool_id = "<USER_POOL_ID>"
jwks_url = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}/.well-known/jwks.json"

response = requests.get(jwks_url)
jwks = response.json()

print(f"Timestamp: {datetime.utcnow().isoformat()}")
print(f"Number of keys in JWKS: {len(jwks['keys'])}")
for key in jwks['keys']:
    print(f"  Key ID: {key['kid']}, Use: {key['use']}, Algorithm: {key['alg']}")

# Compare to a previous snapshot to detect new keys
# Store the output and compare regularly
EOF
```

### 4. Verify client token refresh behavior

After a key rotation, verify that clients can still refresh their tokens:

```bash
# Test with a valid refresh token (obtain from your test client)
REFRESH_TOKEN="<valid-refresh-token>"
USER_POOL_ID="<USER_POOL_ID>"

aws cognito-idp initiate-auth \
  --client-id "<CLIENT_ID>" \
  --auth-flow REFRESH_TOKEN_AUTH \
  --auth-parameters "REFRESH_TOKEN=$REFRESH_TOKEN" \
  --region ap-south-1

# If successful, you'll get a new AccessToken signed with the new key
```

Decode the returned access token to verify it was signed with the current (rotated) key:

```bash
python3 << 'EOF'
import json
import base64

access_token = "<returned-AccessToken>"

# Decode without verification (to inspect claims)
parts = access_token.split('.')
header = json.loads(base64.urlsafe_b64decode(parts[0] + "=="))
print(f"Token header: {header}")
print(f"Key ID (kid): {header.get('kid')}")
EOF
```

## Verification

1. **Monitor CloudWatch for certificate rotation events:**
   ```bash
   aws logs tail /aws/cognito-idp --filter-pattern "certificate" --follow
   ```

2. **Verify JWKS endpoint has the new key:**
   ```bash
   curl -s https://cognito-idp.ap-south-1.amazonaws.com/<USER_POOL_ID>/.well-known/jwks.json | jq '.keys | length'
   ```

3. **Test a client token refresh:**
   Use a test client (e.g., Postman, curl) to call the refresh endpoint and verify it succeeds.

4. **Check no alerts firing:**
   - Token validation should not fail in the app logs
   - No 401 Unauthorized errors spiking in CloudWatch Metrics

## Notes

- **Automatic rotation:** Cognito rotates keys automatically every 365 days. The operator does not need to trigger rotation manually for v1.
- **Key overlapping:** During rotation, Cognito publishes both old and new keys in the JWKS endpoint for a grace period (~24 hours), allowing clients time to migrate their cached public key.
- **Backward compatibility:** Tokens signed with the old key remain valid until their TTL expires. No clients need to re-authenticate.
- **No secret management:** The private key is never exposed. We only manage and monitor the public key (certificate).
- **Token TTL:** Access tokens expire after 1 hour (default). Refresh tokens expire after 30 days. Clients must refresh to get a new access token signed with the new key.

See also: [investigate_token_reuse.md](investigate_token_reuse.md) (token reuse incident).
