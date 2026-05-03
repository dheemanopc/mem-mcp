# ADR 0001: Cognito with DCR shim

## Status

Accepted (2026-04-15)

## Context

mem-mcp requires an OAuth 2.0 authorization server to manage client credentials for MCP-connected applications. The specification requires Dynamic Client Registration (DCR, RFC 7591) support so that new clients can self-register at runtime without manual credential provisioning.

AWS Cognito is the natural choice in an AWS-first architecture and is available in ap-south-1. However, Cognito does not natively implement RFC 7591 (Dynamic Client Registration). We must decide whether to accept Cognito's limitations, hand-roll an OAuth server, or adopt a third-party IdP.

Exposing a thin DCR shim from mem-mcp allows us to: accept Cognito as the authoritative identity provider, let Cognito handle JWT issuance and revocation, and provide a small stateless wrapper endpoint that translates DCR register/update calls into Cognito AppClient operations.

## Decision

We adopt AWS Cognito as the authorization server and expose a DCR shim layer (mem-mcp's own `/auth/dcr` endpoints) that:

1. Receives RFC 7591 registration requests
2. Creates Cognito AppClient objects (scoped to a tenant)
3. Returns client_id, client_secret in the RFC 7591 response
4. Manages client metadata (redirect URIs, grant types, etc.) via Cognito's AppClient API

Cognito remains the source of truth for token validation, user identity, and client credentials. The shim is stateless and purely translates API shapes.

## Consequences

### Positive
- Single IdP with minimal operational footprint in ap-south-1
- IAM-driven access control; no separate credential store
- Cognito's token refresh, MFA, and rate-limiting included
- No multi-region / high-availability burden in v1
- Straightforward to add other OAuth 2.0 grants (authorization_code, client_credentials) later

### Negative
- Additional complexity: DCR shim must be maintained and tested
- Cognito's AppClient API has a few quirks (e.g., regenerating secrets requires a separate API call)
- Lock-in to AWS Cognito; migrating to another IdP requires rewriting the shim

### Risks accepted
- The DCR shim is in the critical auth path; bugs here directly compromise security. Mitigation: extensive unit + integration tests, code review for all changes.
- Cognito AppClient quota (default 25 per user pool) may become a bottleneck in long-running closed beta. Mitigation: request quota increase proactively.

## Alternatives considered

- **Hand-rolled OAuth 2.0 server** (e.g., using authlib, python-jose): Rejected. Re-implementing the OAuth 2.0 security boundary is error-prone and reinvents wheels. Better to delegate token lifecycle to a proven service.
- **Auth0 or Okta**: Rejected. Cost is higher (~$10-30/mo), vendor lock-in is similar, and neither is co-hosted in ap-south-1 (increases latency and complexity).
- **No DCR; manual client provisioning**: Rejected. Spec §7.2 explicitly requires DCR so that SDK clients can auto-enroll. Manual provisioning defeats the closed-beta self-service model.
