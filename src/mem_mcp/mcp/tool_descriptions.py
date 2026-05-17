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

Examples: "Remember we chose PostgreSQL over MySQL." → type=decision. "Save this retry snippet." → type=snippet. "Note: deadline is March 15." → type=fact. "We're going freemium." → type=decision, tags=["pricing"]. "Add my comment on that trade memory: SL too tight." → type=note, parent_id="<trade-uuid>".""",
    "memory_search": """Search memories using a natural-language query — call this to retrieve previously stored information relevant to the current topic, or when the user references past conversations, decisions, or facts.

Call when: user says "what did we decide", "remind me", "what was our", "do you remember", "what did I save about"; user asks a question whose answer might be in memory; user uses possessive pronouns about past work ("our plan", "my preferences", "the approach we chose"); a new conversation starts on a topic where prior context likely exists; you need to check for duplicates before calling memory_write.

Do not call for: general knowledge questions unrelated to the user's personal history; real-time or live data; information just provided in this conversation turn.

Threading: pass parent_id (UUID of a root memory) to restrict results to that root's direct replies, ranked by hybrid relevance. Use this when you want the most relevant comments on a specific memory rather than the full chronological thread (which is what memory_thread_get returns).

Examples: "What did we decide about authentication?" → query="authentication decision". "Remind me of our pricing strategy." → query="pricing strategy". "What snippets do I have for retries?" → query="retry snippet", type=snippet. "What's our database?" → query="database choice decision". "Find the most relevant comments about RECLTD on this trade" → query="RECLTD slippage", parent_id="<trade-uuid>".""",
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
    "memsys_enable_kite": """Onboard or refresh the calling tenant's Kite Connect credentials — call this exactly once for first-time setup, and again any day the user's daily access_token has expired.

Call when: user says "set up Kite", "connect my Zerodha account", "enable Kite skill", "onboard Kite", "I got a new request_token from Kite", "my Kite access_token expired"; cockpit setup workflow; daily access_token refresh (the tool overwrites prior credentials).

Do not call without explicit user instruction supplying their Kite credentials. Do not call to test connectivity — use kite_get_holdings if credentials are already stored. Credentials are AES-256-GCM encrypted at rest in the per-tenant skill vault before this call returns.

Inputs: api_key (string), api_secret (string), and EXACTLY ONE of: request_token (the one-time token from Kite's redirect URL — the tool exchanges it for an access_token via Kite session/token), OR access_token (a fresh access_token if the user already completed the exchange externally).

Output: status (one of "enabled", "stored_but_smoke_failed"), user_id (Zerodha user_id from auth response, null if access_token path was used), smoke_test_result (short description of the kite_get_holdings probe). status="enabled" means credentials are stored AND the smoke call succeeded; status="stored_but_smoke_failed" means credentials are stored but the verification call failed (often a stale access_token — retry with a fresh one).

Examples: First-time onboarding via Kite redirect: api_key=AKEY, api_secret=ASEC, request_token=RTOK. Daily refresh: api_key=AKEY, api_secret=ASEC, access_token=NEW_ATOK.""",
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
}
