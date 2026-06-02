---
source: memsys (team: pmo)
id: d463cc6b-b0ea-4e88-a950-466c272dab83
type: decision
version: 1
is_current: True
created_at: 2026-06-02T03:08:09.889070Z
updated_at: 2026-06-02T03:08:09.889070Z
tags: [pmo, pmo-project-testing, pmo-role-pm, project-brief, for-pa, v1, current, awaiting-verification]
extracted_at: 2026-06-02
---

# PROJECT BRIEF — testing: User Login Screen with Multi-Method Auth

**Authored 2026-06-02 by PM after dialogue with owner. State: `awaiting-verification` (PA to ratify project-tier shape and begin milestone structure).** Verbal handoff captured separately as user_response working memory (look up tag `pmo-user-response` under `pmo-project-testing`).

## ONE-LINE GOAL

Ship a web login screen that authenticates users via Cognito-fronted Google, Apple, and email/password — landing an httpOnly-cookie session on the app on success.

## WHAT SUCCESS LOOKS LIKE

An unauthenticated user reaches the login screen, picks one of three methods (Google, Apple, email/password), completes the provider/Cognito flow, and is returned to the app with an authenticated session represented by an httpOnly cookie. First-time OAuth sign-ins auto-provision a Cognito user record without owner intervention. Email/password users can complete sign-up (with email verification) and recover passwords through the standard Cognito flows. Error states — provider down, network failure, invalid credentials, unverified email — are explicitly handled with user-visible messages, not silent failures. The screen renders consistently across modern desktop and mobile-web browsers.

## OUT OF SCOPE (v1 — deferred, not rejected)

- MFA / TOTP (Cognito supports it; v2)
- Account linking across IdPs (a Google sign-in and an Apple sign-in remain separate Cognito users)
- Native mobile clients (web only)
- OAuth providers beyond Google + Apple (Microsoft, GitHub, Facebook deferred)
- App-side user database — Cognito User Pool holds all user data and custom attributes
- Cognito global sign-out (local logout only)
- Design system / branding work beyond functional UI

## NAMED CONSTRAINTS

1. **Cognito User Pool is the IdP.** Frontend speaks only to Cognito (Hosted UI or AWS SDK). No direct frontend↔provider OAuth flow.
2. **Session storage = httpOnly cookie.** Not localStorage, not sessionStorage. (Mitigates XSS-driven token exfiltration.)
3. **No app-side database.** Cognito is the only user store. Any per-user state v1 needs must live in Cognito custom attributes.
4. **Web only.** No iOS/Android native targets in this project.
5. **Auto-provisioning on first OAuth.** No manual approval / admin step between first sign-in and authenticated state.

## CONSUMERS / STAKEHOLDERS

- **PA** — owns project-tier structure; decomposes this brief into milestones for DM intake.
- **DM** — owns milestone/epic intent.
- **DA** — owns milestone/epic structure.
- **Developer** — implements.
- **Reviewer** — verifies.
- **Owner (Anand)** — ratifies milestone outcomes; not on critical path during build.

## DEFERRED DECISIONS (explicit, not silent assumptions)

- **Account linking strategy** — when v2 needs it, decide whether to merge by verified email or by explicit user action.
- **MFA enablement path** — when v2 needs it, choose between Cognito SMS, TOTP, or a third-party authenticator.
- **Cognito region + pool topology** — assumed single-region single-pool for v1; PA to confirm with infra before committing milestones.

## NOTES

- Project slug: `testing`. Session registry shows PM/PA/DM/DA/Developer registered (Reviewer deferred).
- No project manifest currently exists in memsys at slug `pmo-project-testing-manifest` (PM checked at handoff time). PA should surface this gap during ratification or stand it up before threading milestones.
- The dialogue that produced this brief: PM offered 10 clarifying questions on a one-line feature ask; owner accepted PM's recommended-default answer set verbatim. The handoff line was "yes.. make these assumptions and hand over to PA" — captured as the verbal-handoff working memory.
