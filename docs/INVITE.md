# Welcome to the mem-mcp closed beta

You've been invited to a closed beta of mem-mcp — a private memory store for your AI assistants.

## What it does

mem-mcp gives Claude Code, Claude.ai, and ChatGPT a long-term memory shared across sessions. Decisions, facts, snippets, and notes you choose to capture stay accessible weeks or months later, scoped to your projects.

## Setup (5 minutes)

1. **Sign in** at https://memapp.dheemantech.in. Use the Google account associated with your invite email. The first sign-in creates your tenant.

2. **Connect your AI client.** Pick one (you can add more later):
   - **Claude Code**: install the mem-capture + mem-recall skills:
     ```bash
     claude skill install https://memapp.dheemantech.in/skills/mem-capture
     claude skill install https://memapp.dheemantech.in/skills/mem-recall
     ```
   - **Claude.ai**: copy the instruction block from https://memapp.dheemantech.in/skills into your project's custom instructions. See `docs/integration/claude_ai_instructions.md`.
   - **ChatGPT**: same for a custom GPT. See `docs/integration/chatgpt_instructions.md`.

3. **Try it.** Tell your AI: "Remember that we decided to use Postgres 16 for the new project." Then in a new session: "What database did we pick?" — the AI should recall.

## Privacy

- Your memories are stored only in your tenant — invisible to other beta users (enforced via Postgres row-level security, audited via `tests/security/`).
- All AI requests use your Google account; no anonymous access.
- Daily encrypted backups are stored in S3 in ap-south-1 (Mumbai). See PRIVACY at https://memapp.dheemantech.in/legal/privacy.
- You can export everything as JSON via the dashboard at any time.
- You can request full deletion via the dashboard. There's a 24h grace period to cancel.

## Tier and limits

You're on the Premium tier by default during beta:
- 25,000 memories
- 100,000 embedding tokens/day
- 120 writes / 600 reads per minute

You'll see your usage on the dashboard.

## Feedback

Use the in-app feedback form (`/settings/feedback`) for any bugs, feature requests, or issues. The operator triages weekly.

## Invitation expansion

If you have a friend who'd benefit, ping the operator with their email. Beta is currently capped around 10 active users.

## Help

- Operator: anand@dheemantech.com
- Status page (eventually): https://memapp.dheemantech.in/status
