# Runbook: unblock new-user signups (fix the PreSignUp Lambda)

**Symptom:** new Google sign-ins fail with
`/welcome?status=oauth_error&reason=invalid_request`, detail
`PreSignUp failed with error invite check unavailable; signup denied`.
Existing users are unaffected (PreSignUp only runs at account creation).

**Root cause (confirmed in prod):** the `mem-mcp-presignup` Lambda was shipped
without its dependencies — `cfn_deploy.sh` uses `sam package` (source zip only)
and never runs `sam build`, so `requirements.txt` (httpx) is not installed. The
handler's `import httpx` raised `ModuleNotFoundError` **before** the HTTP call,
so the Lambda failed closed and denied every signup. The backend received **0**
`/internal/check_invite` calls, ruling out HMAC / network / the endpoint.

**Fix already in the repo (no code work left):** `lambdas/presignup/handler.py`
was rewritten to use the Python **stdlib** (`urllib.request`) instead of httpx,
so the function has **no packaged dependencies** and the source-only deploy
pipeline produces a working Lambda. `requirements.txt` no longer lists httpx.

The only thing left is to push that handler onto the live function. **This
requires AWS access** (any one of the options below). Pick whichever matches the
access you have.

---

## Option A — one-liner from AWS CloudShell (fastest, ~2 min)

AWS Console → **CloudShell** (region **ap-south-1**):

```bash
git clone https://github.com/dheemanopc/mem-mcp.git && cd mem-mcp/lambdas/presignup
zip /tmp/presignup.zip handler.py
aws lambda update-function-code \
  --region ap-south-1 \
  --function-name mem-mcp-presignup \
  --zip-file fileb:///tmp/presignup.zip \
  --publish
aws lambda wait function-updated --region ap-south-1 --function-name mem-mcp-presignup
```

Needs: `lambda:UpdateFunctionCode` on `mem-mcp-presignup`. Done — go to **Verify**.

## Option B — make it self-serve from GitHub (durable)

So future Lambda fixes need no AWS access, just a GitHub button:

1. Apply the deploy-role stack update once (adds a scoped
   `lambda:UpdateFunctionCode` grant — already committed to
   `deploy/cft/gha-deploy-role.yaml`). From CloudShell:

   ```bash
   git clone https://github.com/dheemanopc/mem-mcp.git && cd mem-mcp
   aws cloudformation deploy \
     --stack-name mem-mcp-gha-deploy-role \
     --template-file deploy/cft/gha-deploy-role.yaml \
     --capabilities CAPABILITY_NAMED_IAM
   ```
   (Update the **existing** role stack; the role name `mem-mcp-gha-deploy` is
   fixed and would collide if a new stack is created.)

2. Then, in GitHub (no AWS access needed): **Actions → deploy-lambda → Run
   workflow → main**, and approve the `production` gate. It updates the Lambda
   directly from the runner.

## Option C — full infra redeploy

Anyone with AWS admin runs `deploy/scripts/cfn_deploy.sh`. Because the handler is
now dependency-free, its source-only package deploys a working Lambda.

---

## Verify

1. A **not-invited** Google sign-in now shows a *clean* denial
   (`Sign-up not currently available for this email`) instead of "invite check
   unavailable", **and** the applicant appears in `/admin/signup-requests`
   (pending tab, tagged "via Google · not invited").
2. An **invited** user (add via `seed_invite.py add <email>`, or the
   `invite-user` workflow) gets through and is provisioned.

## Guard against regression

Adding any dependency back to `lambdas/presignup/requirements.txt` will silently
break the Lambda again unless `cfn_deploy.sh` is changed to `sam build` before
packaging. Keep the PreSignUp handler dependency-free, or fix the build step.
