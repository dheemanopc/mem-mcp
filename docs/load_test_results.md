# Load test results

T-9.9 — record p50/p95/p99 from running `tests/load/locustfile.py` against staging.

## Setup

1. Provision JWT fixture (`tests/load/jwt_fixture.json`):
   ```bash
   # 10 tenants, signed in via Cognito, copy access_token from each
   ./deploy/scripts/seed_invite.py --email loadtest-{1..10}@example.invalid
   # then sign in each via the web → grab cookies → exchange for JWT
   # (or use a service-account script — TODO)
   ```
   Format:
   ```json
   {
     "tenants": [
       {"label": "tenant-1", "jwt": "eyJ..."},
       {"label": "tenant-2", "jwt": "eyJ..."}
     ]
   }
   ```

2. Run locust:
   ```bash
   poetry run locust -f tests/load/locustfile.py \
       --host https://memsys.staging.dheemantech.in \
       --users 100 --spawn-rate 10 --run-time 5m
   ```

## Results

| Run date | Build SHA | RPS | p50 | p95 | p99 | Failures |
|---|---|---|---|---|---|---|
| _(record after first run)_ | | | | | | |

## Acceptance criterion

- p95 < 250ms (excluding Bedrock embedding latency — Bedrock calls are async-ish via the embedder cache)
- Failure rate < 1%

## Interpretation guide

- p99 spikes during dedup checks: expected; the cosine similarity scan is O(N) per type per tenant. If p99 > 500ms, time to add the IVF index per spec §10.4.2.
- Bedrock-induced latency: separate metric (logged as `embed_latency_ms` in the app log group). Subtract from observed write latency for "app-only" budget.
