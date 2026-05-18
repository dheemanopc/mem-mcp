import Link from "next/link";

export const metadata = {
  title: "Docs — mem-mcp",
  description: "How to connect Claude Code, Claude.ai, and ChatGPT to your memsys instance.",
};

export default function DocsPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 prose">
      <h1>mem-mcp documentation</h1>

      <p>
        mem-mcp is a personal memory layer that lets multiple AI assistants
        (Claude Code, Claude.ai, ChatGPT, Cursor) share the same long-term
        memory you control. It speaks the{" "}
        <a href="https://modelcontextprotocol.io" target="_blank" rel="noreferrer">
          Model Context Protocol
        </a>{" "}
        — so you connect each assistant once, and they all see and write to the
        same memory store.
      </p>

      <h2>Quick start</h2>
      <ol>
        <li>
          <Link href="/auth/login">Sign in with Google</Link>. Your access must already be
          approved by the operator.
        </li>
        <li>
          Connect at least one MCP client (instructions below).
        </li>
        <li>
          Try it: ask your assistant to <em>&ldquo;remember that I prefer dark mode&rdquo;</em>,
          then in another session ask <em>&ldquo;what are my preferences?&rdquo;</em>.
        </li>
      </ol>

      <h2>The MCP endpoint</h2>
      <p>
        All clients connect to the same endpoint:
      </p>
      <pre>
        <code>https://memsys.dheemantech.in/mcp</code>
      </pre>
      <p>
        Authentication is OAuth 2.0 with Dynamic Client Registration (DCR). Most MCP
        clients handle this automatically — you click &ldquo;connect&rdquo;, sign in with
        Google, and they store the access token.
      </p>

      <h2>Connecting Claude Code</h2>
      <p>
        Add this server to your Claude Code config. In a terminal:
      </p>
      <pre>
        <code>{`claude mcp add --transport http memsys https://memsys.dheemantech.in/mcp`}</code>
      </pre>
      <p>
        Then restart Claude Code. The first tool call will trigger the OAuth login
        flow in your browser. After authorizing, Claude Code remembers the token.
      </p>

      <h2>Connecting Claude.ai (chat)</h2>
      <p>
        Open <a href="https://claude.ai/settings/connectors" target="_blank" rel="noreferrer">
          claude.ai/settings/connectors
        </a>, click <strong>Add custom connector</strong>, and use:
      </p>
      <ul>
        <li>
          <strong>Name</strong>: <code>memsys</code>
        </li>
        <li>
          <strong>URL</strong>: <code>https://memsys.dheemantech.in/mcp</code>
        </li>
      </ul>
      <p>
        Click connect, sign in with Google, and the connector becomes available in chat.
      </p>

      <h2>Connecting ChatGPT (Developer Mode)</h2>
      <p>
        In ChatGPT, open <strong>Settings — Developer Mode — MCP Servers</strong> (requires
        a ChatGPT plan that includes Developer Mode). Add the same URL:{" "}
        <code>https://memsys.dheemantech.in/mcp</code>. Authorize via Google.
      </p>

      <h2>Available tools</h2>
      <p>
        Once connected, the assistant sees these tools. Most are auto-discovered and
        invoked when the conversation needs them.
      </p>

      <h3>Memory tools (core)</h3>
      <ul>
        <li><strong>memory_write</strong> — store a note, decision, fact, snippet, or question</li>
        <li><strong>memory_search</strong> — hybrid retrieval (semantic + keyword, recency-decayed)</li>
        <li><strong>memory_get</strong> — fetch one memory by id (+ optional version history)</li>
        <li><strong>memory_list</strong> — browse memories by type / tag / date</li>
        <li><strong>memory_update</strong> — edit content or tags in-place</li>
        <li><strong>memory_delete</strong> — soft-delete (recoverable for 30 days)</li>
        <li><strong>memory_undelete</strong> — restore within the 30-day grace window</li>
        <li><strong>memory_supersede</strong> — replace a decision/fact with a new version while preserving history</li>
        <li><strong>memory_thread_get</strong> — fetch a root memory plus all its replies in order</li>
        <li><strong>memory_export</strong> — full data dump for backup / portability</li>
        <li><strong>memory_feedback</strong> — record explicit thumbs up / down on a search result</li>
        <li><strong>memory_stats</strong> — counts, quota usage, embeddings consumed today</li>
      </ul>

      <h3>Onboarding</h3>
      <ul>
        <li>
          <strong>memsys_enable_kite</strong> — wire your Zerodha Kite Connect credentials
          to enable broker tools (skill framework, opt-in per tenant)
        </li>
      </ul>

      <h3>Kite skill (optional, broker integration)</h3>
      <p>
        Enabled after a successful <code>memsys_enable_kite</code> call. All operate
        against your own Zerodha account; nothing is shared across tenants.
      </p>
      <ul>
        <li><strong>kite_get_holdings</strong> — long-term equity holdings</li>
        <li><strong>kite_get_positions</strong> — open intraday + F&O positions</li>
        <li><strong>kite_get_margins</strong> — available cash and used margin</li>
        <li><strong>kite_get_quote</strong> — live LTP / depth for one or many instruments</li>
        <li><strong>kite_get_historical_data</strong> — OHLC bars for charting / pattern analysis</li>
        <li><strong>kite_get_orders</strong> — today&apos;s order book</li>
        <li><strong>kite_place_order</strong> — submit a new order (use with care)</li>
        <li><strong>kite_cancel_order</strong> — cancel a pending order</li>
      </ul>

      <h2>Tags, types, and search</h2>
      <p>
        Every memory has a <strong>type</strong> (one of: note, decision, fact, snippet,
        question) and an optional list of <strong>tags</strong>. Search ranks by hybrid
        semantic + keyword relevance and decays older items. Tags are exact-match
        filters; combine them with a query for sharp retrieval.
      </p>

      <h2>Privacy & data</h2>
      <p>
        See the <Link href="/legal/privacy">privacy policy</Link> for what we collect,
        where it lives, and how to export or delete your data. Short version: AWS
        Mumbai, encrypted at rest, no third-party analytics, you can export or
        delete everything at any time.
      </p>

      <h2>Source &amp; status</h2>
      <p>
        mem-mcp is in closed beta. Bugs and feature requests go to the operator —
        right now that&apos;s a person, not a form. Email{" "}
        <a href="mailto:anand@dheemantech.com">anand@dheemantech.com</a>.
      </p>
    </main>
  );
}
