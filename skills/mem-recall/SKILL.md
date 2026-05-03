---
name: mem-recall
description: Retrieves relevant prior memories when a user message references past context. Triggers on possessives ("my project", "our approach"), definite articles assuming shared reference ("the script", "that decision"), or explicit asks ("what did we decide", "remind me"). Calls memory.search on the configured mem-mcp connector before responding.
---

Before responding to messages that reference prior context, call `memory.search` with:
- `query`: the user's message verbatim, trimmed to 200 chars
- `tags`: include the active project tag if obvious from context
- `limit`: 8

Use returned context naturally; do not announce that you searched memory unless asked.

If no results return: respond normally without speculating about prior context.
