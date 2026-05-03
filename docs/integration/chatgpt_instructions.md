# ChatGPT custom GPT instructions for mem-mcp

Copy the block below into your custom GPT's instructions to wire up the
mem-mcp connector. Replace `<your-project>` with the project tag you want
to use.

---

# Memory connector instructions

You have access to a memory store via the "mem-mcp" connector. Use it as follows:

CAPTURE: When the user states a decision, fact, or notable conclusion, OR says "remember/save/note", call mem-mcp memory.write with appropriate type (decision|fact|snippet|note|question) and 2-6 tags including a project tag (project:<your-project>).

RECALL: Before responding to messages that reference prior context (uses "we", "our", "the project", "what did we decide"), call mem-mcp memory.search with the user's message as query.

Do not announce memory operations. Just use them.

---

## Verification

Same pattern as the Claude.ai version: ask "Do you remember the last decision we made?" after a few writes.
