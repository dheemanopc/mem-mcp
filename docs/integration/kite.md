# Integrating a headless system (kite) with memsys over `/mcp`

memsys has **no plain REST API**. The only machine-callable surface is
`POST /mcp` — JSON-RPC 2.0 with a Cognito Bearer access token. This guide
wires a headless partner system (the kite trading system) to it using the
**client_credentials** machine grant, with a `local_admin` fallback for
local development.

The `/api/web/*` routes are session-cookie + CSRF (browser only) and are
**not** for machine clients.

---

## Architecture

```
kite process
  └─ ClientCredentialsTokenProvider ── POST https://memauth.../oauth2/token
  │                                     grant_type=client_credentials  → access_token (60 min)
  └─ MemsysClient ── POST https://memsys.../mcp   (Authorization: Bearer <token>)
        {"jsonrpc":"2.0","method":"tools/call",
         "params":{"name":"memory_write","arguments":{...}}}
```

The machine token has **no user**: its `sub` claim equals the app-client ID.
The Bearer middleware resolves the tenant via `tenant_identities.cognito_sub`,
so a dedicated **service tenant** must be seeded once (below). Scopes on the
token (`https://memsys.dheemantech.in/memory.write`) are normalized to bare
`memory.write` by the middleware and matched against each tool's required
scope.

---

## One-time provisioning (memsys side)

### 1. Deploy the machine app client

`infra/cfn/nested/040-identity.yaml` defines `mem-kite-service-client`
(client_credentials, scopes `memory.read` + `memory.write`, `GenerateSecret:
true`). Deploy the root stack, then:

```bash
# client id is bridged to SSM automatically by root.yaml
KITE_CLIENT_ID=$(aws ssm get-parameter --name /mem-mcp/kite/client_id \
                  --query Parameter.Value --output text)

# the generated secret must be stored in SSM by the operator (CFN cannot
# safely output a generated secret):
SECRET=$(aws cognito-idp describe-user-pool-client \
           --user-pool-id "$POOL_ID" --client-id "$KITE_CLIENT_ID" \
           --query 'UserPoolClient.ClientSecret' --output text)
aws ssm put-parameter --name /mem-mcp/kite/client_secret \
    --type SecureString --key-id alias/mem-mcp --value "$SECRET"
```

### 2. Verify the token `sub` (the linchpin)

Before seeding, mint a token and confirm what `sub` it carries — the seed
must use the *actual* `sub`:

```bash
eval $(KITE_CLIENT_ID=... KITE_CLIENT_SECRET=... scripts/service-token.sh)
python -c "import base64,json,sys; p=sys.argv[1].split('.')[1]; \
  p+='='*(-len(p)%4); print(json.loads(base64.urlsafe_b64decode(p)))" "$MEM_MCP_TOKEN"
# expect: token_use=access, scope contains .../memory.read .../memory.write,
#         and note the `sub` value (normally == the client id).
```

### 3. Seed the service tenant

```bash
KITE_CLIENT_ID="$KITE_CLIENT_ID" \
  KITE_CLIENT_SUB="<sub from step 2>" \
  MEM_MCP_DB_MAINT_DSN="postgresql+psycopg://mem_maint:...@<db>:5432/mem_mcp" \
  scripts/seed-service-tenant.sh
```

This creates a dedicated tenant + personal team + `tenant_identities` row
(keyed on the token `sub`) + an `oauth_clients` row. Idempotent — safe to
re-run. RLS isolates this tenant from every human tenant.

---

## kite side

Copy `docs/integration/kite_memsys_client.py` into your kite repo (only
dependency is `httpx`). Configure via env:

```bash
# prod
export MEMSYS_BASE_URL=https://memsys.dheemantech.in
export MEMSYS_AUTH_MODE=client_credentials
export MEMSYS_TOKEN_URL=https://memauth.dheemantech.in/oauth2/token
export MEMSYS_CLIENT_ID=...        # /mem-mcp/kite/client_id
export MEMSYS_CLIENT_SECRET=...    # /mem-mcp/kite/client_secret
```

```python
from kite_memsys_client import from_env

mem = from_env()
mem.memory_write("NIFTY breakout entry @ 23480, SL 23410",
                 type="note", tags=["kite", "trade"])
hits = mem.memory_search("nifty breakout entries", limit=5)
for r in hits["results"]:
    print(r["score"], r["content"])
```

For `decision`/`fact` writes (versioned + slugged), pass the service tenant's
default `team_id` and a `slug_clue`. Plain `note` (best for high-volume trade
logging) needs neither.

---

## Local development

cognito-local does **not** implement client_credentials, so locally use the
admin flow (the client module's `local_admin` mode, same as
`scripts/dev-token.sh`):

```bash
docker compose up -d
export MEMSYS_BASE_URL=http://localhost:8080
export MEMSYS_AUTH_MODE=local_admin
python kite_memsys_client.py     # runs the built-in smoke test
```

Scope checks are bypassed locally, so the admin token (which lacks the
`memory.*` scopes) still works. Prototype the client here, then flip
`MEMSYS_AUTH_MODE=client_credentials` for prod.

---

## Gotchas

- **Errors are HTTP 200.** Tool failures ride inside the JSON-RPC `error`
  envelope. The client raises `MemsysToolError` for those, `MemsysAuthError`
  for 401s (and retries once after refreshing the token).
- **`401 no_tenant_for_sub`** ⇒ the seed used the wrong `sub`, or wasn't run.
  Re-check step 2/3.
- **`-32000 insufficient_scope`** ⇒ the app client wasn't granted
  `memory.read`/`memory.write`, or you're hitting prod with a local token.
- **Dedup**: `memory_write` deduplicates by default — near-identical trade
  logs may merge. Pass `force_new=True` if every write must persist.
- **Embedding degradation**: if Bedrock throttles, the write still succeeds
  with `embedding_status="throttled"` and a NULL vector (backfilled later);
  it just won't appear in semantic search until then. Log the field.
- **Isolation & least privilege**: the machine client is scoped to its own
  tenant (RLS blocks cross-tenant reads) and granted only read+write, never
  `memory.admin`. Keep the secret in SSM/KMS and rotate it; the client has
  `EnableTokenRevocation` so a leaked token can be killed.
