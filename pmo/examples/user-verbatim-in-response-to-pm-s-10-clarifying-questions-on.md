---
source: memsys (team: pmo)
id: f8e7ce4b-f167-4c93-8f7b-4d8a69fc7499
type: note
version: 1
is_current: True
created_at: 2026-06-02T03:07:49.968631Z
updated_at: 2026-06-02T03:07:50.468404Z
tags: [pmo, pmo-project-testing, pmo-role-pm, pmo-working, pmo-user-response, verbal-handoff, current]
extracted_at: 2026-06-02
---

User verbatim, in response to PM's 10 clarifying questions on the login-screen feature for project `testing`:

"yes.. make these assumptions and hand over to PA"

PM's recommended-default assumption set this acknowledges (from the prior PM message):
- Cognito-fronted shape (b): frontend speaks only to Cognito; Cognito federates to OAuth providers via Hosted UI / federated identities
- Providers v1: Google + Apple
- Email/password as a fallback method (with forgot-password, email verification, password rules)
- MFA / TOTP: OUT for v1
- Account linking across IdPs: OUT (Google sign-in and Apple sign-in remain separate accounts)
- Auto-provision Cognito user on first successful OAuth
- Session: httpOnly cookie (not localStorage)
- Platform: web only
- No app-side user database — Cognito User Pool is the user store
- Logout: local only (no Cognito global sign-out v1)

This is the verbal handoff that authorizes the PM to write the formal project_brief and hand to PA.
