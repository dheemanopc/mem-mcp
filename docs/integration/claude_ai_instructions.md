# Claude.ai project instructions for mem-mcp

Copy the block below into your Claude.ai project's custom instructions
to wire up the mem-mcp connector. Replace `<your-project>` with the
project tag you want to use.

---

# Memory connector instructions

You have access to a memory store via the "mem-mcp" connector. Use it as follows:

CAPTURE: When the user states a decision, fact, or notable conclusion, OR says "remember/save/note", call mem-mcp memory.write with appropriate type (decision|fact|snippet|note|question) and 2-6 tags including a project tag (project:<name>).

RECALL: Before responding to messages that reference prior context (uses "we", "our", "the project", "what did we decide"), call mem-mcp memory.search with the user's message as query.

Do not announce memory operations. Just use them.

---

## Verification

After saving, ask Claude: "Do you remember the last decision we made?"
- If you've used memory.write earlier, Claude should call memory.search and report the decision.
- If empty, Claude should say it has no prior context — that's expected on a fresh tenant.
