---
name: mem-capture
description: Stores user decisions, facts, and notable conclusions to long-term memory. Triggers when the user expresses a decision ("we'll go with X", "decided to", "plan is to"), states a fact worth remembering, or explicitly says "remember", "save this", "note that". Calls memory.write on the configured mem-mcp connector.
---

When the user states or implies a memorable item (decisions, facts, configuration choices, preferences, recurring snippets), call the `memory.write` tool on the mem-mcp connector with:
- `content`: a clear, self-contained restatement of the item (not a verbatim copy unless useful)
- `type`: one of decision | fact | snippet | note | question (pick the closest)
- `tags`: 2-6 tags including a project tag (e.g., `project:ew`) and topic tags
- `metadata`: { source: "claude-code", session_id: "..." } if available

Do NOT call memory.write for trivial chit-chat, emotional content, or sensitive information unless the user explicitly asks.

Confirm in one short sentence after writing: "Saved as a {type} memory."
