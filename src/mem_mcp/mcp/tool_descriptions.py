"""MCP tool descriptions — versioned artifact per CR-001.

Authoring rules (NFR-9.2.1–9.2.5):
- 80–250 words per description
- Three-part structure: positive triggers | anti-triggers | examples
- Never inline descriptions in tool classes or route handlers
- Treat this file as diff-able, lintable, testable artifact
"""

from __future__ import annotations

TOOL_DESCRIPTIONS: dict[str, str] = {
    "memory_write": """Store a memory — call this whenever the user wants to save information for future recall, or when a conversation produces a decision, fact, preference, snippet, or question worth preserving across sessions.

Call when: user says "remember this", "save this", "note that", "keep track of"; user reaches a clear decision after discussion; user shares a deadline, code snippet, personal preference, architectural choice, or project fact they'll need later; user explicitly asks you to record something. Also call proactively when a firm decision is made mid-conversation — do not wait to be asked.

Do not call for: transient working notes the user is thinking aloud without committing; casual exploration; information the user will obviously not need again; content already stored (search first if uncertain).

Types: note (general), decision (architectural or product choices), fact (objective facts, deadlines, names), snippet (reusable code), question (open questions to revisit).

Examples: "Remember we chose PostgreSQL over MySQL." → type=decision. "Save this retry snippet." → type=snippet. "Note: deadline is March 15." → type=fact. "We're going freemium." → type=decision, tags=["pricing"].""",

    "memory_search": """Search memories using a natural-language query — call this to retrieve previously stored information relevant to the current topic, or when the user references past conversations, decisions, or facts.

Call when: user says "what did we decide", "remind me", "what was our", "do you remember", "what did I save about"; user asks a question whose answer might be in memory; user uses possessive pronouns about past work ("our plan", "my preferences", "the approach we chose"); a new conversation starts on a topic where prior context likely exists; you need to check for duplicates before calling memory_write.

Do not call for: general knowledge questions unrelated to the user's personal history; real-time or live data; information just provided in this conversation turn.

Examples: "What did we decide about authentication?" → query="authentication decision". "Remind me of our pricing strategy." → query="pricing strategy". "What snippets do I have for retries?" → query="retry snippet", type=snippet. "What's our database?" → query="database choice decision".""",

    "memory_get": """Fetch a single memory by its UUID — call this when you have a specific memory ID and need its complete, untruncated content or full metadata.

Call when: user references a specific memory ID explicitly; you found a result via memory_search and need the full content (search may truncate); you need to verify current state of a specific memory before updating or superseding it; user says "show me that memory" after you displayed an ID.

Do not call without a known memory ID — use memory_search to find memories by content first. Do not call repeatedly for the same ID within one conversation turn. Do not call to list or browse — use memory_list for that.

Examples: User says "show me memory abc-123-..." → get id="abc-123-...". You found a truncated search result and need the full text → get its id. You want to confirm a memory exists before superseding → get its id to verify type and version.""",

    "memory_list": """List memories with filtering and pagination — call this when the user wants to browse their memory store by type, tag, or date range without a specific search query.

Call when: user says "show me all my decisions", "list my snippets", "what memories do I have tagged python", "show everything from last week", "what have I saved recently", "browse my memories"; user wants to audit or review stored memories; user asks for a count or overview.

Do not call when the user has a specific question — use memory_search instead (it ranks by relevance). Do not call just to check whether something exists — memory_search is better for existence checks. Do not call with no filters when memory_search would serve better.

Examples: "Show me all my decisions." → type=decision, order=desc. "What did I save last week?" → since=7 days ago. "List snippets tagged python." → type=snippet, tags=["python"]. "Show my most recent 10 memories." → limit=10, order_by=created_at, order=desc.""",

    "memory_update": """Edit the content or tags of an existing memory in place — call this when the user wants to correct or extend a memory without creating a new version.

Call when: user says "update that memory", "fix that note", "change it to say", "add a tag to", "that's not right — it should say"; user provides a correction to a stored note or snippet; user wants to add tags to an existing memory; small corrections to facts or notes that don't represent a conceptual change.

Do not call for decision or fact types when the update represents a changed conclusion — use memory_supersede instead (it preserves history). Do not call without a known memory ID — search first. Do not call when the user wants to delete and rewrite from scratch — delete then write.

Examples: "Fix that note to say the deadline is April 15, not March 15." → update content. "Add tag 'urgent' to that memory." → update tags. "That snippet has a bug — here's the corrected version." → update content.""",

    "memory_delete": """Soft-delete a memory — call this when the user explicitly wants to remove a memory from their active store. Deleted memories are not returned in search or list by default but can be restored.

Call when: user says "delete that", "remove that memory", "I don't need that anymore", "forget that", "that's outdated, get rid of it"; user explicitly instructs removal after you display a memory.

Do not delete without explicit user instruction — never delete based on your own judgment that something is outdated. Do not delete when the user wants to correct content — use memory_update instead. Do not delete when the user wants to replace a decision — use memory_supersede (preserves history).

Examples: "Delete the note about the old deadline." → delete its id. "Remove all my test memories." → delete each id individually after listing them. "Forget that." → after displaying a memory, delete its id.""",

    "memory_undelete": """Restore a soft-deleted memory — call this when the user wants to bring back a memory they previously deleted.

Call when: user says "restore that", "undelete", "I changed my mind, bring that back", "actually keep it"; user regrets a deletion in the same or a later session. Use memory_list with include_deleted=true to find the ID of what was deleted.

Do not call without explicit user instruction. Do not call speculatively — only restore what the user specifically asks to recover. Do not call if the memory was never deleted.

Examples: "Actually, restore that memory I just deleted." → undelete its id. "I deleted something about our pricing last week — can you recover it?" → list with include_deleted=true to find it, then undelete.""",

    "memory_supersede": """Replace a decision or fact with a new version, preserving full history — call this when a decision or fact has changed and the old version should be kept for audit purposes.

Call when: user says "we changed our decision about X", "that's outdated — the new answer is", "update our decision on"; the user explicitly acknowledges a prior decision is being replaced by a new one; a fact that was true is now different (new deadline, new technology choice, new price).

Do not use for note or snippet types — use memory_update for those. Do not use without the ID of the memory being superseded — search for it first. Do not use when the original memory was simply wrong and has no audit value — delete and rewrite instead.

Examples: "We switched from PostgreSQL to CockroachDB." → supersede the old database decision, write the new one with supersedes=old_id. "The deadline moved from March 15 to April 30." → supersede the old deadline fact.""",

    "memory_export": """Export all memories as a structured data dump — call this when the user wants a complete copy of their memory store for backup, migration, or external processing.

Call when: user says "export all my memories", "give me a backup", "download my data", "export everything", "I want a full dump"; user is migrating to a new setup; user wants to process their memories externally.

Do not call for partial queries or browsing — use memory_search or memory_list instead. Do not call just to read a few memories — use memory_get or memory_search. Export returns all memories and may be large.

Examples: "Export all my memories." → export with no filters. "Give me a backup of everything." → export.""",

    "memory_feedback": """Record explicit feedback on a memory or search result — call this when the user rates, reacts to, or evaluates a specific memory that was retrieved or displayed.

Call when: user says "that was useful", "that result wasn't relevant", "that's exactly what I needed", "thumbs up on that one", "mark that as helpful"; user explicitly rates a retrieved memory positively or negatively; you want to record that a result was acted on.

Do not call speculatively without user feedback. Do not call on every search result — only when the user explicitly reacts. Do not use as a substitute for memory_update when the user wants to change content.

Examples: "That search result was perfect." → feedback positive on the result id. "That memory isn't relevant to what I asked." → feedback negative on the result id.""",

    "memory_stats": """Return usage statistics for the user's memory store — call this when the user asks about their memory usage, counts, or quota.

Call when: user says "how many memories do I have", "show me my usage", "what's my storage", "how full is my quota", "give me stats", "memory summary"; user wants an overview of their store size or composition.

Do not call unless the user is explicitly asking about statistics or usage. Do not call as a substitute for memory_list or memory_search when the user wants to see content. Do not call repeatedly in the same turn.

Examples: "How many memories do I have?" → stats. "What's my memory usage?" → stats. "Am I close to my limit?" → stats.""",
}
