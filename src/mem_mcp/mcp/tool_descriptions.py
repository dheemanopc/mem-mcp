"""MCP tool descriptions - versioned artifact per CR-001.

Authoring rules (NFR-9.2.1-9.2.5):
- 80-250 words per description
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

Threading: pass parent_id (UUID of an existing root memory) to write this memory as a reply rather than a standalone entry. Replies inherit the parent's tags automatically at write time; you may add reply-only tags via the tags field. Replies cannot have their own replies (flat hierarchy) — pass parent_id only if you have a root id. parent_id cannot be combined with supersedes in the same call.

Teams & slug support: pass `team_id` (UUID) to set the team scope (or omit to use your default_team_id). For decision/fact types, pass `slug_clue` to auto-generate a short lookup slug. Pass `references` list to cite other memories. Pass `visibility` (team/external/public) to set sharing scope.

Multi-team users: **Remember the chosen team for the rest of this conversation and pass it on every subsequent memory_* call** unless the user explicitly switches contexts.

Contradiction detection: pass `check_contradictions=true` to have recent same-type memories NLI-screened against the new content; the response then carries a `contradictions` list (memory_id, content_snippet, contradiction_score, type, tags, created_at), capped by `contradiction_limit` (1-10, default 3). Advisory only — the write always proceeds. Populated results require server-side `nli_backend=ollama` configuration; otherwise the list is empty.

Examples: "Remember we chose PostgreSQL over MySQL." → type=decision. "Save this retry snippet." → type=snippet. "Note: deadline is March 15." → type=fact. "We're going freemium." → type=decision, tags=["pricing"]. "Add my comment on that trade memory: SL too tight." → type=note, parent_id="<trade-uuid>". "Save this for the team" → team_id=<uuid>, visibility="team".

Note: `visibility="team"` requires `team_id` to be specified.""",
    "memory_search": """Search memories using a natural-language query — call this to retrieve previously stored information relevant to the current topic, or when the user references past conversations, decisions, or facts.

Call when: user says "what did we decide", "remind me", "what was our", "do you remember", "what did I save about"; user asks a question whose answer might be in memory; user uses possessive pronouns about past work ("our plan", "my preferences", "the approach we chose"); a new conversation starts on a topic where prior context likely exists; you need to check for duplicates before calling memory_write.

Do not call for: general knowledge questions unrelated to the user's personal history; real-time or live data; information just provided in this conversation turn.

Tags: Multiple tags are intersected (AND) — a memory matches only if it has ALL the listed tags.

Threading: pass parent_id (UUID of a root memory) to restrict results to that root's direct replies, ranked by hybrid relevance. Use this when you want the most relevant comments on a specific memory rather than the full chronological thread (which is what memory_thread_get returns).

Multi-team users: pass `team_id` (UUID or team name) to scope the call. If you receive a `team_required` error, ask the user which team to work in (or use `user_default_team_id` from the error data). **Remember the chosen team for the rest of this conversation and pass it on every subsequent memory_* call** unless the user explicitly switches contexts. Use `team_id="*"` for cross-team queries. Use `memsys_list_my_teams` to see options.

Examples: "What did we decide about authentication?" → query="authentication decision". "Remind me of our pricing strategy." → query="pricing strategy". "What snippets do I have for retries?" → query="retry snippet", type=snippet. "What's our database?" → query="database choice decision". "Find the most relevant comments about RECLTD on this trade" → query="RECLTD slippage", parent_id="<trade-uuid>".

Matching: keyword matching is lenient — any query word can match (English-stemmed, OR semantics), with more matching words ranking higher; combined with semantic similarity and recency decay. Memories written with `indexable=false` are NEVER returned by search (neither semantic nor keyword) — reach those via memory_list (tags/parent_id filters), memory_thread_get, or memory_get.

Results: each result carries a short `preview` (keyword-windowed snippet with **match** markers, or head-of-content) for relevance triage, plus `content` truncated to 2000 chars (`content_truncated`/`content_length` indicate clipping). Evaluate with `preview`; call `memory_get(id)` for the full body.""",
    "memory_search_chunks": """Search inside memories at passage level — call this when you need the specific passage that matches a query rather than whole memories, especially across long documents (KB articles, specs, scan bundles).

Call when: the answer is likely a paragraph inside a long memory ("what does the spec say about retries", "find the section on rate limits"); synthesizing across several documents where you need the relevant passage from each; a memory_search result had a high semantic score but an unhelpful head-of-content preview and you want the actual matching passage.

Do not call for: precise/canonical lookups (instrument tokens, price levels, spec IDs, locked facts) — use tags/slugs/memory_get, never fuzzy retrieval. Do not call when whole-memory ranking is what you want — use memory_search. Results cover indexable memories only.

Results: each item is a chunk snippet with provenance — memory_id, chunk_index/total_chunks, type, tags, created_at, similarity score. Up to `per_memory` chunks per memory (default 2; long memories often match in several places). Escalate with memory_get(memory_id) for the full body; neighboring passages are chunk_index ± 1.

Freshness: chunks are built asynchronously after writes (typically within ~5 minutes). A just-written memory may not appear yet — memory_search covers it immediately.

Examples: "What did the LLD say about token budgets?" → query="token budget", type=decision. "Find the section about HNSW indexes across my notes" → query="HNSW index configuration".""",
    "memory_write_async": """Submit a memory write fire-and-forget — call this when you want to persist a memory WITHOUT blocking the conversation turn on the synchronous write latency (300ms-2s with embedding).

Call when: persisting a dialogue exchange mid-conversation where the user feels the wait of sync memory_write; PMO role-prompts persisting working notes/ambiguity captures during dialogue; any "log this and keep moving" pattern.

Do not call when: you need the memory id in the same turn for a downstream operation (read-your-own-write is NOT guaranteed in the same session — use sync memory_write); the content is required to be immediately searchable by the same caller before they end the turn.

Same input shape as memory_write (content, type, tags, team_id, slug_clue, references, visibility, parent_id, supersedes, fragment_id, expires_at, ttl_seconds, indexable, metadata, force_new). Submit-time validation: shape + quota + bare reference-existence. Quota counts at submission (no bypass via async). Deferred to drain: embedding, full reference access-check, dedup, slug allocation, parent/supersedes validation.

Returns: `{request_id, queued_at, estimated_consistency_by}` immediately. The resulting memory's `created_at` equals `submitted_at` (queued-at), preserving per-tenant ordering across an asynchronous drain. Eventual consistency target: ≤5s under nominal load. Drain failures surface via a notice on the caller's next MCP response.

Examples: "Capture this working note while we continue" → memory_write_async(content="...", type="note"). PM persisting a stabilized hypothesis mid-dialogue → memory_write_async(content="...", tags=["working-note"]).""",
    "memory_get_batch": """Fetch up to 50 specific memories in one round-trip — call this when you have a list of known memory IDs (or slug-tuples) and want to load them all together instead of N sequential memory_get calls.

Call when: session-start load (role definition + project_brief + recent dialogue exchanges + referenced decisions); any workflow where N specific memories are known up-front and must be loaded together.

Do not call for: search-by-content (use memory_search) or filter-by-tag (use memory_list) — those are different intents. Do not call for fetching a single memory — use memory_get.

Inputs: requests array of up to 50 entries, each shaped like memory_get input — either `{"id": "<uuid>"}` OR `{"team_id": "<uuid>", "resource_type": "decision|fact", "slug": "<slug>"}`. Mixed shapes in one batch are allowed.

Output: results array in submission order. Each entry: `{"ok": true, "memory": {...}}` on success, or `{"ok": false, "error": {"code": "memory_not_accessible", "message": "memory not found or not accessible"}}` on miss. The error envelope is intentionally opaque per the cross-team access contract — "not found" and "no access" are indistinguishable.

Examples: load PMO session start → memory_get_batch(requests=[{id: "role-uuid"}, {team_id: "...", resource_type: "decision", slug: "project-brief"}, {id: "..."} ...]).""",
    "memory_get": """Fetch a single memory by its UUID or slug — call this when you have a specific memory ID and need its complete, untruncated content or full metadata.

Call when: user references a specific memory ID explicitly; you found a result via memory_search and need the full content (search may truncate); you need to verify current state of a specific memory before updating or superseding it; user says "show me that memory" after you displayed an ID.

Slug lookup: pass (team_id, resource_type, slug) instead of id to fetch a decision/fact by its slug (opaque cross-team access control — caller must have read access to the team).

Do not call without either a known memory ID OR a slug-tuple — use memory_search to find memories by content first. Do not call repeatedly for the same ID within one conversation turn. Do not call to list or browse — use memory_list for that.

Examples: User says "show me memory abc-123-..." → get id="abc-123-...". You found a truncated search result and need the full text → get its id. "Fetch the decision 'pricing-strategy' from team X" → memory_get(team_id="<team-uuid>", resource_type="decision", slug="pricing-strategy").""",
    "memory_list": """List memories with filtering and pagination — call this when the user wants to browse their memory store by type, tag, or date range without a specific search query.

Call when: user says "show me all my decisions", "list my snippets", "what memories do I have tagged python", "show everything from last week", "what have I saved recently", "browse my memories"; user wants to audit or review stored memories; user asks for a count or overview.

Do not call when the user has a specific question — use memory_search instead (it ranks by relevance). Do not call just to check whether something exists — memory_search is better for existence checks. Do not call with no filters when memory_search would serve better.

Tags: Multiple tags are intersected (AND) — a memory matches only if it has ALL the listed tags.

Keywords: pass `keywords` to return only memories whose content textually matches at least one word (lenient, English-stemmed OR matching). Unlike memory_search this does not rank by relevance — pagination order (created_at/updated_at) is preserved.

Multi-team users: pass `team_id` (UUID or team name) to scope the call. If you receive a `team_required` error, ask the user which team to work in (or use `user_default_team_id` from the error data). **Remember the chosen team for the rest of this conversation and pass it on every subsequent memory_* call** unless the user explicitly switches contexts. Use `team_id="*"` for cross-team queries.

Examples: "Show me all my decisions." → type=decision, order=desc. "What did I save last week?" → since=7 days ago. "List snippets tagged python." → type=snippet, tags=["python"]. "Show my most recent 10 memories." → limit=10, order_by=created_at, order=desc. "List notes mentioning postgres from this month" → keywords="postgres", since=month start.

Results: each result carries a short `preview` (keyword-windowed snippet with **match** markers when `keywords` was passed, head-of-content otherwise); `content` is truncated to ~2000 chars per result — call `memory_get(id)` to fetch the full body.""",
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
    "memory_thread_get": """Fetch a memory thread — the root memory plus all its replies in chronological order. Call this when you need the full discussion context of a single memory, not a ranked search sample.

Call when: user references discussion on a specific memory ("what did we say about that decision", "show me the comments on that flag", "what's the full thread for X"); you have a known root memory id and need every reply in time order; you're about to summarize or continue a discussion and need complete context; the user wants to see how an existing trade memory or playbook rule evolved through comments.

Do not call for: general topic searches across the whole store — use memory_search instead; fetching a memory that may itself be a reply — this tool requires a root id and rejects reply ids; browsing memories by tag or date — use memory_list. Do not call without a known root id — search or list first.

Inputs: root_id (UUID of the root memory; must not itself be a reply). Output: the root memory plus a replies array ordered by created_at ascending. An empty replies array is a valid result — it means no comments yet.

Examples: User says "show me the discussion on memory abc-123-..." → memory_thread_get(root_id="abc-123-..."). After surfacing a trade memory via memory_search, user asks "what did I say about this?" → memory_thread_get with the trade memory id. "Pull the full conversation thread for the flag we discussed yesterday." → memory_thread_get on the flag's id.""",
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
    "memsys_enable_kite": """Enable or refresh Kite Connect (Zerodha) for the calling tenant. Three onboarding modes — choose exactly one.

Call when: user wants to connect their Zerodha account for the first time; user pasted a fresh request_token after Kite's daily-redirect flow; user's prior access_token has expired and they want to refresh; user wants to enable trade-placement tools (orders_enabled=true).

Do not call without explicit user instruction; this stores live broker credentials encrypted in the per-tenant vault. Do not call to test connectivity — use kite_get_holdings if credentials are already stored.

Inputs: api_key + api_secret + exactly one of:
- access_token (manual: paste a fresh access_token each day)
- request_token (one-time: exchange the request_token from Kite's redirect)
- user_id + password + totp_secret (full auto-login: mem-mcp handles every daily refresh from now on; TOTP secret is the base32 string from Kite's 'Can't scan? Copy key' under 2FA setup)

Optional: orders_enabled=true to allow kite_place_order and kite_cancel_order (default false; read-only Kite tools always work).

Output: status (enabled / stored_but_smoke_failed), mode (manual / request_token_exchange / auto_login), user_id (Zerodha user_id if known), smoke_test_result.

Examples: 'Set up Kite with auto-login for trader@example.com (totp ABCDEF, pwd xxx)' → mode auto_login. 'Refresh my Kite access_token: NEW_ATOK' → mode manual. 'Enable Kite trading' → include orders_enabled=true.""",
    "memory_write_batch": """Store multiple memories in a single request, avoiding N round-trips — call this when partners need to atomically write a batch of memories, avoiding per-write JSON-RPC envelope overhead.

Call when: user/partner says "write 50 memories at once", "batch insert these", "upload all my notes"; a client workflow fires 50+ memory_write calls in a loop and needs to reduce latency; you need to preserve quota semantics per-entry (each counts individually against writes_per_minute and embed_tokens_daily).

Do not call when only one memory is being written — use memory_write directly. Do not call to "merge" memories that should be one — use memory_write deduplication instead. Do not bypass per-entry error handling by pretending all-or-nothing is required; on_error=continue is the default (process all, report per-entry status).

Behavior: iterates each entry, calls memory_write internally (same code path, no duplication), reports per-entry success/failure. Each entry is its OWN transaction. Quota: each entry counts individually toward writes_per_minute and embed_tokens_daily — if mid-batch quota is exceeded, remaining entries fail with quota_exceeded (correct behavior). on_error=fail_all stops at first error and marks remaining as skipped; on_error=continue (default) processes all and returns full results.

Examples: "Write 141 memories from FNO batch" → memories=[{...}, {...}, ...], on_error=continue. Partner API call: batch all pending journals at once → MemoryWriteBatchInput(memories=[...], on_error=fail_all) if atomicity required.""",
    "kite_cancel_order": """Cancel a pending or trigger-pending order on Zerodha — call this when the user wants to abort an order that hasn't yet fully executed.

Call when: user explicitly says "cancel that order", "abort the buy on RELIANCE", "kill my SL"; orchestrator detects that a placed order's premise has been invalidated and needs to retract; cleanup before market close.

Do not call for: an order that has already fully executed (no-op or error). Do not call for an order that doesn't belong to the calling tenant. Do not call without first knowing the order_id and its variety (fetch via kite_get_orders).

Inputs: variety (must match the order's variety — usually "regular"), order_id (string from kite_place_order or kite_get_orders).

Output: order_id of the cancelled order (echoed).

Examples: "Cancel that Reliance buy" → kite_get_orders, find pending RELIANCE BUY, then kite_cancel_order(variety="regular", order_id=<id>). "Kill all my pending orders" → kite_get_orders, filter open, kite_cancel_order each.""",
    "kite_get_historical_data": """Fetch OHLC (and optional volume + open-interest) bars for a Zerodha instrument over a date range — the primary market-data source for the AI Trader v2 strategic planner.

Call when: user asks for historical price action on a symbol, "show me RECLTD daily bars for the last 90 days", "give me POWERGRID hourly for this week"; AI Trader Agent 1's nightly scan needs OHLC bundles per watchlist symbol; chart analysis or pattern-detection workflows.

Do not call for: latest quote — use kite_get_quote (faster, single round-trip). Do not call without a specific date range — pulls can be expensive at long ranges. Do not call without knowing the instrument_token — fetch via the instruments dump first (separate workflow).

Inputs: instrument_token (int), interval ("minute","3minute","5minute","15minute","30minute","60minute","day","week","month"), from_date and to_date as "YYYY-MM-DD HH:MM:SS", optional continuous (futures continuation), optional oi (open-interest column).

Output: candles array of [datetime, open, high, low, close, volume(, oi)] tuples.

Examples: "RECLTD daily, last 90d" → from_date/to_date span 90 days, interval="day". "POWERGRID hourly today" → interval="60minute". F&O continuation: continuous=True.""",
    "kite_get_holdings": """Fetch the user's long-term equity holdings from Zerodha — call this when the user asks about their portfolio, holdings, current positions, or what stocks they own.

Call when: user says "what do I own", "show my holdings", "what's in my portfolio", "list my stocks", "what's my P&L on holdings"; user asks about specific symbols they may own; cockpit checking pre-trade position state before validating a new trade.

Do not call for: intraday positions or open trades — use kite_get_positions for those. Do not call to check available cash — use kite_get_margins. Do not call to get a quote for a symbol not in holdings — use kite_get_quote.

Inputs: none. Output: array of holdings (tradingsymbol, exchange, quantity, average_price, last_price, pnl, day_change, day_change_percentage).

Examples: "What stocks do I currently own?" → kite_get_holdings(). "How is my Reliance position doing?" → kite_get_holdings() then filter for RELIANCE in result. "Show portfolio P&L." → kite_get_holdings(), sum the pnl column.""",
    "kite_get_margins": """Fetch available margin and used margin from the user's Zerodha account — call this to check buying power before placing a trade or to report account-level cash status.

Call when: user says "how much can I trade", "what's my available margin", "do I have funds for X qty", "how much cash do I have"; cockpit pre-trade safeguard checks; orchestrator validating sufficient margin before kite_place_order.

Do not call for: per-position margin — use kite_get_positions which has m2m. Do not call to check overall portfolio value — use kite_get_holdings and sum.

Inputs: optional segment ("equity" or "commodity"; default returns both). Output: margin block with available_cash, used_margin, opening_balance, exposure, span, etc.

Examples: "Do I have margin to buy 100 RELIANCE?" → kite_get_margins(segment="equity"). "What's my opening balance?" → kite_get_margins(). "Margin used in commodities?" → kite_get_margins(segment="commodity").""",
    "kite_get_orders": """Fetch the full list of today's orders from Zerodha — pending, executed, cancelled, rejected — call this to see what's open, what filled, and what was rejected.

Call when: user asks "did my order fill", "what orders are open", "show me today's order book", "any rejected orders"; cockpit post-trade audit; cross-reference an order_id you got back from kite_place_order.

Do not call for: settled holdings or running positions — use kite_get_holdings or kite_get_positions. Do not call without need — the order book can be large at end of day.

Inputs: none. Output: array of order rows (order_id, parent_order_id, exchange_order_id, status, status_message, tradingsymbol, exchange, transaction_type, quantity, filled_quantity, price, average_price, trigger_price, product, order_type, validity, order_timestamp, exchange_timestamp).

Examples: "Did my Reliance order fill?" → kite_get_orders(), filter by tradingsymbol and order_id. "Show all rejected orders." → filter status="REJECTED". "What's pending?" → filter status in ("OPEN","TRIGGER PENDING").""",
    "kite_get_positions": """Fetch the user's open positions for the trading day from Zerodha — call this for intraday MIS, F&O, and overnight CNC positions that aren't yet settled into holdings.

Call when: user asks "what trades are open right now", "show today's positions", "are any of my orders still active", "what F&O contracts do I have"; trade-management workflow needs to see current open exposure; cockpit checks before deciding if a new trade would breach max-concurrent-positions limit.

Do not call for: settled long-term holdings — use kite_get_holdings. Do not call to check order history — use kite_get_orders. Do not call to get specific tradable quote — use kite_get_quote.

Inputs: none. Output: two arrays — net (running net positions) and day (today's positions). Each entry has tradingsymbol, exchange, product, quantity, average_price, last_price, pnl, m2m, buy_quantity, sell_quantity.

Examples: "Any open positions?" → kite_get_positions(). "How much loss am I running today?" → kite_get_positions(), sum pnl from day. "Are my SL legs in place?" → kite_get_positions() and inspect SL columns.""",
    "kite_get_quote": """Fetch the latest live quote (LTP + OHLC + depth + OI) for one or more Zerodha instruments — call this for current-price questions or pre-trade sanity checks.

Call when: user asks "what's the LTP of X", "current price of POWERGRID", "show me the depth on RECLTD"; tactical-confirmer (Agent 2) needs current bar context against a triggered branch; user wants to monitor live price for a symbol.

Do not call for: historical bars — use kite_get_historical_data. Do not call repeatedly in a tight loop — Kite rate-limits the quote endpoint. Do not call without exchange-prefixed instruments (must be "NSE:RECLTD", not just "RECLTD").

Inputs: instruments — list of strings like "NSE:RECLTD", "BSE:HDFC", "NFO:NIFTY26MAYFUT". Output: dict keyed by instrument with last_price, last_quantity, ohlc.{open,high,low,close}, volume, average_price, oi, depth.

Examples: "LTP of Reliance" → instruments=["NSE:RELIANCE"]. "Compare RECLTD vs POWERGRID quotes" → instruments=["NSE:RECLTD","NSE:POWERGRID"]. "Live depth on Nifty fut" → instruments=["NFO:NIFTY26MAYFUT"].""",
    "kite_place_order": """Place a new order with Zerodha (BUY or SELL, equity or F&O, MIS/CNC/NRML) — call this ONLY when the user has explicitly confirmed the order or when the AI Trader v2 orchestrator has cleared its safeguards.

Call when: user explicitly says "place the order", "buy 100 RELIANCE at market", "sell my POWERGRID position"; AI Trader v2 Agent 2 has emitted GO and orchestrator's pre-trade safeguards passed (daily loss cap, position size cap, max concurrent, slippage tolerance); user has manually approved a paper/live trade.

Do not call speculatively, without explicit user/orchestrator approval, or to "test" the API. Do not call without all required Kite fields. Do not call to modify an existing order (separate endpoint). Do not call to cancel — use kite_cancel_order.

Inputs: variety ("regular","amo","co","iceberg","auction"), tradingsymbol, exchange, transaction_type ("BUY"/"SELL"), quantity, product ("CNC","MIS","NRML"), order_type ("MARKET","LIMIT","SL","SL-M"), optional price (required for LIMIT/SL), optional trigger_price (required for SL/SL-M), validity ("DAY","IOC","TTL"), optional disclosed_quantity.

Output: order_id (string).

Examples: User: "Buy 10 RELIANCE at 2500 limit MIS" → kite_place_order(variety="regular", tradingsymbol="RELIANCE", exchange="NSE", transaction_type="BUY", quantity=10, product="MIS", order_type="LIMIT", price=2500).""",
    "kite_ta_session_open": """Open a technical analysis session for historical OHLCV data and indicator compute.

Call when: user wants to analyze a specific symbol+timeframe over a date range; prep for indicator calculations; batch indicator compute across multiple timeframes; building a TA dashboard or screening workflow.

Do not call for: single-bar quote snapshots (use kite_get_quote). Do not call without explicit symbol and timeframe. Do not call repeatedly for the same analysis — reuse the session_id returned.

Inputs: symbol (e.g., "RELIANCE"), timeframe (minute|3minute|5minute|15minute|30minute|60minute|day|week), lookback_bars (default "max" per timeframe), optional ttl_seconds override.

Output: session_id (UUID), bars_loaded (int), first_bar_ts, last_bar_ts, last_close, expires_at (ISO), replaced_session_id if evicted due to per-tenant cap.

Examples: "Analyze WIPRO on 15-minute bars for the last 100 days" → ta_session_open(symbol="WIPRO", timeframe="15minute", lookback_bars=100). "Open a daily RSI session for RELIANCE" → ta_session_open(symbol="RELIANCE", timeframe="day").""",
    "kite_ta_indicator_compute": """Compute technical indicators in batch against a TA session.

Call when: user asks "what's the RSI", "compute MACD + Bollinger Bands", "run 5 indicators at once"; batch requests for multiple indicators with different parameters; fetch indicator series for visualization or export.

Do not call for: indicators not in the catalog (rsi, ema, sma, macd, atr, bollinger, stoch, adx, supertrend, vwap, obv, mfi, cci, williams_r). Do not call without an active session_id. Do not call expecting per-bar signals (that's Phase 2.5).

Inputs: session_id (from ta_session_open), requests (list of name+params+format), default_format ("summary"|"series"|"csv"). Per-request: optional at_timestamp (floor-snap to that bar), lookback (last N bars).

Output: results (list, one per request), each with ok flag, summary (last/prev/min/max), series (values), csv (text), error (if not ok).

Examples: "RSI(14) + EMA(20)" → requests=[{name:"rsi",params:{period:14}}, {name:"ema",params:{period:20}}]. "Bollinger Bands at 2026-05-19T10:00:00Z" → at_timestamp="2026-05-19T10:00:00Z". "Last 50 MACD bars as CSV" → format="csv", lookback=50.""",
    "kite_ta_series_fetch": """Fetch cached series data by key from a completed indicator compute.

Call when: user wants to export or visualize the full series from a prior compute; fetch series for further processing or external tools.

Do not call for: fresh indicator computes (use ta_indicator_compute). Do not call without a series_key from a prior compute result.

Inputs: session_id, series_key (from prior ta_indicator_compute), last_n (default 100), format ("json"|"csv").

Output: raw series values, last N rows.

Examples: "Export the RSI series as CSV" → ta_series_fetch(series_key=<key>, format="csv"). "Last 50 values of that MACD line" → ta_series_fetch(series_key=<key>, last_n=50).""",
    "kite_ta_ohlc_fetch": """Fetch OHLCV bars from the cache of an active TA session.

Call when: user wants the underlying OHLC data for charting or external analysis; export bars for backtesting; verify bar timestamps/prices in a session.

Do not call for: fresh historical fetches (use kite_get_historical_data). Do not call without an active session_id.

Inputs: session_id, last_n (default 50), format ("json"|"csv").

Output: OHLCV rows, timestamp + open/high/low/close/volume.

Examples: "Show me the last 20 bars from the WIPRO session" → ta_ohlc_fetch(session_id=<id>, last_n=20). "Export RELIANCE bars as CSV for Excel" → ta_ohlc_fetch(session_id=<id>, format="csv").""",
    "kite_ta_session_status": """Check the status of an active TA session (alive, TTL, computed indicators, cache state).

Call when: user asks "is my session still active", "how long until expiry", "what have I computed"; debugging session lifecycle; monitoring session age or staleness.

Do not call for: creating sessions (use ta_session_open). Do not call repeatedly in a tight loop (once per user question is fine).

Inputs: session_id.

Output: alive (bool), expires_at (ISO), age_s (seconds since open), trailing_bar_stale_s (seconds since last bar), indicators_computed (list), cache_status ("warm"|"rehydrating"|"evicted").

Examples: "Is my session still good?" → ta_session_status(session_id=<id>). "When does this session expire?" → ta_session_status, check expires_at.""",
    "kite_ta_session_refresh": """Refresh the data in a TA session (trailing bars or full rescan).

Call when: user needs fresh data after market hours pass; ensures session data is current before computing new indicators; mode="full" for bulk rescan.

Do not call for: automatic refresh (sessions auto-refresh on compute). Do not call excessively (refresh once per analysis cycle is typical).

Inputs: session_id, mode ("trailing"|"full", default "trailing").

Output: bars_refetched (int), new_last_bar_ts (ISO).

Examples: "Refresh my session" → ta_session_refresh(session_id=<id>, mode="trailing"). "Rescan all 100 days" → ta_session_refresh(session_id=<id>, mode="full").""",
    "memsys_list_my_teams": """Fetch the caller's teams (workspace and personal) with their role and member count.

Call when: user asks "what teams am I in", "list my teams", "show my workspace teams"; you receive a team_required error and need to show available teams to the user; you need to resolve a fuzzy team name to UUID.

Do not call repeatedly in the same conversation — cache the result locally.

Inputs: none. Output: teams (array of {id, name, workspace_domain, role, member_count}), default_team_id (UUID or null).

Examples: "What teams do I have?" → memsys_list_my_teams(). After team_required error → show the available_teams from error.data instead of calling this.""",
    "memsys_create_team": """Create a new team (workspace or personal) with the caller as owner.

Call when: user says "create a team", "start a new team", "set up a workspace"; caller is a workspace user (domain auto-derived); caller is a personal user (no domain).

Do not call without explicit user instruction.

Inputs: name (string). Optional parent_team_id (UUID) to create as sub-team of an existing team; role_in_parent (default "member") sets the new team's role within the parent.

Output: team (with id, name, workspace_domain, created_at), your_role="owner".

Examples: "Create a team called Engineering" → memsys_create_team(name="Engineering"). Workspace user creates team → domain auto-set to their workspace_domain. "Create sub-team T2 under T1 as vendor" → memsys_create_team(name="T2", parent_team_id="T1-uuid", role_in_parent="vendor").""",
    "memsys_add_team_member": """Add a user or sub-team as a member of an existing team.

Roles come from the global role catalog (call memsys_list_roles when that ships; for now, common values: 'owner', 'admin', 'member', 'viewer', 'vendor', 'customer'). When member_kind='team', cycle prevention + max-depth-5 are enforced; cycle-creating adds reject with code -32000.

For member_kind='user': provide EITHER member_id (UUID, existing user) OR member_email (string, existing user). Cross-org invitation by email of a non-existent user is not yet supported and returns -32000.

Required scope: memory.write. Caller must have admin-level access to the team (enforced once Spec 5's effective-access table ships; for now this is a placeholder).

Examples: "Add user with email alice@example.com as member" → memsys_add_team_member(team_id="...", member_kind="user", member_email="alice@example.com", role_key="member"). "Add sub-team T2 to T1 as vendor" → memsys_add_team_member(team_id="T1-uuid", member_kind="team", member_id="T2-uuid", role_key="vendor").""",
    "memsys_remove_team_member": """Remove a user or sub-team from a team.

Sole owner of a team cannot be removed (raises sole_owner_cannot_self_remove); assign another owner first.

Required scope: memory.write. Caller must have admin-level access to the team.

Examples: "Remove user X from team T" → memsys_remove_team_member(team_id="T", member_kind="user", member_id="X").""",
    "memsys_assign_role": """Change a member's role in a team (user or sub-team).

The (team, member_kind, member_id) tuple is unique; this UPSERTs the role_id. Common role_key values: owner, admin, member, viewer, vendor, customer.

Required scope: memory.write.

Examples: "Promote user X to admin in team T" → memsys_assign_role(team_id="T", member_kind="user", member_id="X", new_role_key="admin").""",
    "memsys_slug_lookup": """Look up a memory by slug (short human-readable identifier) within a team.

Call when: you have a slug from another memory's reference or from earlier context; you need to resolve a slug-based identifier to a memory UUID for further operations.

Do not call for: browsing or searching by content — use memory_search instead. Slug lookup requires you to know the exact team_id, resource_type (decision/fact), and slug beforehand.

Access control: opaque cross-team model. If you don't have read access to the team, slug lookup returns all-null trio (memory_id, title, updated_at = None) — caller cannot distinguish "not found" from "not accessible".

Required scope: memory.read.

Example: "Look up the decision 'market-analysis' in team X" → memsys_slug_lookup(team_id="<uuid>", resource_type="decision", slug="market-analysis").""",
    "memsys_refs_in": """Backward citation graph: list memories that cite the given memory.

Results are FILTERED to citers in teams the caller can read. Citers in inaccessible teams are aggregated as inaccessible_count (no UUIDs leaked across team boundaries — preserves the opaque-cross-team model from PR #270's slug design).

Required scope: memory.read.

Example: "Show me what depends on decision X" → memsys_refs_in(memory_id="X").""",
    "memsys_refs_out": """Forward citation graph: list memories that the given memory cites.

Results filtered the same way as memsys_refs_in: refs to inaccessible target teams aggregated as inaccessible_count.

Required scope: memory.read.

Example: "What does this decision cite?" → memsys_refs_out(memory_id="X").""",
}
