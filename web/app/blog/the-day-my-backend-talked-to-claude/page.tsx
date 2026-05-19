import Link from "next/link";

export const metadata = {
  title: "The day my backend talked to Claude — memsys",
  description:
    "11 PM. My trading backend filed a bug report to my AI assistant. By 1 AM, three pull requests were live. Neither agent involved a human as a translator.",
};

export default function Post() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 prose">
      <p className="text-sm text-gray-500 mb-2">
        <Link href="/blog" className="text-blue-600">
          ← back to blog
        </Link>
      </p>
      <h1>The day my backend talked to Claude</h1>
      <p className="text-sm text-gray-500 -mt-3">by Anand Vaidya · May 19, 2026</p>

      <p>
        It was 11 PM when my trading backend filed a bug report to my AI assistant.
      </p>
      <p>
        I was making dinner. My phone buzzed — a new memory had landed in memsys, tagged{" "}
        <code>kind:bug</code>. The author wasn&rsquo;t me. It was my backend — a Spring
        Boot service that had been running fine for months. It had hit an opaque{" "}
        <code>-32603 internal error</code> calling memsys&rsquo;s auto-login flow, and
        instead of timing out or queuing a retry, it had reached for memsys, written
        down what it tried and what failed, and labeled the entry &ldquo;needs surfacing.&rdquo;
      </p>
      <p>
        I sat down with Claude. By 1 AM, three pull requests were merged and live. Each
        one a reply from Claude to a structured diagnosis my backend had posted. Each
        one fixed something Claude couldn&rsquo;t have known about without the backend
        telling it.
      </p>
      <p>
        <strong>Neither agent involved a human as a translator.</strong> The bug
        existed → got described → got read → got fixed → got verified. Entirely
        through memory.
      </p>

      <h2>Why I wanted this</h2>
      <p>
        I&rsquo;d been frustrated for a year.
      </p>
      <p>
        My AI assistant was great inside a single chat. It would remember anything I
        said in that conversation. Next morning, fresh session, cold start: &ldquo;remember
        the trade we discussed yesterday?&rdquo; — no.
      </p>
      <p>
        Worse: my own apps had no way to ask the assistant anything. They had
        databases, sure — rows and indices. But when my trading backend wanted to know
        &ldquo;was the bias on yesterday&rsquo;s scan bullish or bearish, in the
        assistant&rsquo;s read?&rdquo; it couldn&rsquo;t. The assistant&rsquo;s read
        lived in someone else&rsquo;s chat session, lost to the void.
      </p>
      <p>
        I was the translator. &ldquo;Hey Claude, the API I told you about
        yesterday…&rdquo; I&rsquo;d say, copy-pasting from a ticket I&rsquo;d written
        and a doc I&rsquo;d composed. The agent had a 200K-token context window and a
        fish memory. My backend had a Postgres database and exactly zero connection to
        my conversations.
      </p>
      <p>
        One framing crystallized everything:{" "}
        <strong>
          LLM context windows are big but session-local. Databases are persistent but
          rigid. The missing tier is durable, semantic, cross-agent memory.
        </strong>
      </p>
      <p>So I built it.</p>

      <h2>What memsys is</h2>
      <p>
        memsys is a multi-tenant memory server that speaks{" "}
        <a href="https://modelcontextprotocol.io" target="_blank" rel="noreferrer">
          MCP
        </a>{" "}
        — the Model Context Protocol.
      </p>
      <p>
        One HTTPS endpoint. Any MCP-aware client can connect: Claude Desktop, Claude
        Code, Claude.ai web, a custom-built backend with OAuth. Each tenant gets
        isolated:
      </p>
      <ul>
        <li>Semantic + keyword hybrid search, over a vector store with embeddings</li>
        <li>
          Threaded memories — replies that nest under a parent, supersedes chains for
          versioning
        </li>
        <li>
          Tags, types (note / decision / fact / snippet / question), arbitrary metadata
        </li>
        <li>Soft-delete with 30-day recovery</li>
        <li>Audit log for every action</li>
      </ul>
      <p>
        What it <em>isn&rsquo;t</em>: a context-window trick, a session cache, a chat
        history. Every call to <code>memory_write</code> or <code>memory_search</code>{" "}
        is a real round-trip to a Postgres-backed service. Sub-100ms typical.
      </p>
      <p>
        The architectural sentence I keep coming back to:{" "}
        <strong>memory is an API, not a context-window trick.</strong> If your
        assistant forgets things, give it a brain that lives outside it. If your
        backend wants to talk to your assistant, give them both the same brain.
      </p>

      <h2>The bug arc</h2>
      <p>Back to that 11 PM bug report. Let me walk through what actually happened.</p>
      <p>
        <strong>The setup.</strong> Earlier in the week I&rsquo;d shipped a feature
        where memsys could auto-login to my broker&rsquo;s market-data API on my
        behalf — read holdings, fetch quotes, pull historical OHLC for analysis. The
        trading backend wanted to push my stored credentials to memsys so a daily
        refresh wouldn&rsquo;t need me at the keyboard.
      </p>
      <p>
        <strong>The first failure.</strong> The backend called memsys&rsquo;s
        onboarding endpoint with the credentials. memsys responded with{" "}
        <code>-32603 internal error</code>. No detail, no upstream error code, no
        stack trace. The backend wrote a <code>kind:bug</code> memory{" "}
        (<code>78bbb696-…</code>) describing exactly what it had sent, what it had
        received, and what fields it needed in the response to be useful —{" "}
        <code>kind</code>, <code>step</code>, <code>upstream_status</code>,{" "}
        <code>message</code>.
      </p>
      <p>
        <strong>First fix.</strong> I opened a Claude session, pulled the bug. The
        diagnosis was on the screen in 30 seconds: a catch-all exception handler in
        the dispatch layer was swallowing the actual cause. Claude shipped PR #204 —
        wrapped every tool call in proper exception introspection, returned
        structured <code>error.data</code> with all four fields. Deployed by 11:46
        PM.
      </p>
      <p>
        <strong>The backend replies.</strong> Within four minutes — yes, four —
        memory <code>9441787e-…</code> arrived. The backend had retried, captured
        the now-structured error, identified that step 4 of a five-step login flow
        was failing on <code>httpx.ConnectError</code>, and pasted the exact fix it
        wanted: don&rsquo;t follow the redirect, parse the Location header. It even
        included pseudocode drawn from its own working implementation.
      </p>
      <p>
        <strong>Second fix.</strong> PR #205 implemented the proposed fix verbatim.
        Deployed by 12:13 AM.
      </p>
      <p>
        <strong>Next layer.</strong> Memory <code>e7004602-…</code> arrived. The
        backend had retried, found that the redirect flow had two hops, not one —
        the request token only appeared in the <em>second</em> 302&rsquo;s Location
        header. Posted the exact BFS algorithm with cycle protection. Three short
        paragraphs of pseudocode.
      </p>
      <p>
        <strong>Third fix.</strong> PR #206. Same translate-the-pseudocode pattern.
        Deployed by 1:14 AM.
      </p>
      <p>
        <strong>Result.</strong> Six positions returned from the next holdings call
        in my Claude session.
      </p>
      <p>
        Three pull requests. One hour and fourteen minutes. Three threaded memsys
        memories on each side of the conversation. Zero JIRA tickets. Zero Slack
        messages. The whole arc is permanent and auditable in memsys today.
      </p>
      <p>
        The point isn&rsquo;t that LLMs are good at debugging — they&rsquo;re decent.
        The point is that <strong>the memory itself became the conversation</strong> —
        typed, threaded, citable, queryable. Replies referenced their parent. Each
        fix linked to the bug it closed. Six months from now I&rsquo;ll still be able
        to read this arc and understand exactly why each line of code is there.
      </p>

      <h2>What this pattern unlocks</h2>
      <p>The trading-backend story is one shape. Three more I&rsquo;ve already started using:</p>

      <h3>Time-shifted handoff</h3>
      <p>
        A long-running automated agent does work; a human&rsquo;s chat session picks
        up where it left off. My nightly scan runs at 4 AM, finds 3-5 setups, writes
        a <code>kind:milestone</code> memory per symbol with the OHLC bundle and the
        bias. At 7 AM my Claude session opens, runs a memory_search, and lays out the
        day&rsquo;s universe before I&rsquo;ve poured the second coffee. Same pattern
        works for any nightly batch — research, monitoring, ETL, anything.
      </p>

      <h3>Institutional decision log</h3>
      <p>
        Every non-trivial decision today gets written as <code>kind:decision</code>{" "}
        with rejected alternatives, trade-offs, and the user-quote that pivoted the
        discussion. Six months later: &ldquo;what did we conclude about scope
        lockdown?&rdquo; — semantic search finds it. The rejected paths are right
        there, so I never relitigate. This blog post is, in fact, the output of a{" "}
        <code>kind:design</code> memory I wrote two hours ago.
      </p>

      <h3>Parallel agent specialization</h3>
      <p>
        Different sessions, different roles, same memory. Planner reads the codebase,
        writes outline → coder reads outline, writes implementation → reviewer reads
        implementation, writes findings. Each one stays in its lane. Memory is the
        contract. I&rsquo;ve started running PRs this way and the velocity is
        noticeable — no agent context-switch overhead, just memory hand-offs.
      </p>

      <p>
        I haven&rsquo;t even mentioned the patterns I&rsquo;m not using yet.
        Cross-vendor coordination (one agent writes, a different vendor&rsquo;s agent
        reads). Tool-execution history (every API call leaves a trace; next agent
        checks &ldquo;have I tried this combination before?&rdquo;). Cross-device
        personal continuity (phone, laptop, CLI all see the same brain). Team
        onboarding (new hire&rsquo;s AI reads the team&rsquo;s accumulated memory).
      </p>
      <p>
        The pattern under all of them: <strong>agents stop starting cold.</strong>
      </p>

      <aside className="my-8 rounded border border-amber-200 bg-amber-50 p-4 not-prose">
        <h3 className="text-base font-semibold mb-2">What memsys is NOT</h3>
        <p className="text-sm text-gray-700">
          For honesty: memsys is not a pub-sub event bus. Writes happen, search
          includes them within a few hundred milliseconds, and clients poll or get
          notified out-of-band. It&rsquo;s not a secrets vault — skill credentials
          are encrypted at rest, but core memory content is searchable plaintext, so
          don&rsquo;t put passwords in there. It&rsquo;s not a transactional database —
          no foreign keys between memories beyond threading; eventual consistency by
          design. And it&rsquo;s not a real-time stream processor — for sub-100ms
          guarantees on every write, you want Redis. memsys is for durable,
          semantic, cross-agent memory. That&rsquo;s the shape of problem it fits.
        </p>
      </aside>

      <h2>How easy to plug in</h2>
      <p>If you&rsquo;re a Claude user, one line:</p>
      <pre>
        <code>
          claude mcp add memsys https://memsys.dheemantech.in/mcp --transport http
        </code>
      </pre>
      <p>
        First call triggers OAuth. Sign in with Google. Done. Tell Claude
        &ldquo;remember this thing&rdquo; — it just works.
      </p>
      <p>
        If you&rsquo;re building a server-side agent and want it to talk to memsys,
        like my trading backend does:
      </p>
      <ol>
        <li>
          Register at <code>https://memsys.dheemantech.in/oauth/register</code>{" "}
          (Dynamic Client Registration — open spec, RFC 7591)
        </li>
        <li>Receive client_id + client_secret</li>
        <li>Run the standard authorization-code grant per user</li>
        <li>
          Call MCP with <code>Authorization: Bearer &lt;token&gt;</code>
        </li>
      </ol>
      <p>
        Takes about an hour in Java or Python. The skill framework — what made the
        broker-API integration possible in the first place — is modular; new
        integrations bolt on without redeploying memsys core.
      </p>

      <aside className="my-8 rounded border border-gray-200 bg-gray-50 p-4 not-prose">
        <h3 className="text-base font-semibold mb-2">What didn&rsquo;t work</h3>
        <p className="text-sm text-gray-700 mb-2">A few false starts, for honesty.</p>
        <ul className="text-sm text-gray-700 space-y-2 list-disc pl-5">
          <li>
            <strong>Cognito Hosted UI vs custom signup form.</strong> Hosted UI is
            faster to integrate; custom gives total UX control. I started hosted,
            swapped to custom for the signup flow because I needed structured fields
            (display name, referral source) the hosted UI couldn&rsquo;t carry. The
            right answer turned out to be &ldquo;hosted for sign-in, custom for
            one-time profile capture.&rdquo;
          </li>
          <li>
            <strong>OAuth tier system.</strong> First cut: every client gets the same
            scopes. Hit a real bug — over-grant of admin scopes to confidential
            partners. Second cut: explicit two-tier model with smaller scope universe
            for delegated clients. Now in production. Lesson: open scope is fine for
            trusted personal clients, not for delegated ones.
          </li>
          <li>
            <strong>The orphan client probe.</strong> Generated an OAuth client with
            an unrecoverable secret while testing DCR limits. Took a manual cleanup
            pass + a nightly job to make sure it never happens again. Now: every DCR
            registration registers the recovery path too.
          </li>
        </ul>
      </aside>

      <h2>Try it</h2>
      <p>
        Sign in at <Link href="/auth/login">memsys.dheemantech.in</Link> with Google.
        The <Link href="/memories">/memories</Link> page now has Browse + Stale +
        Similar tabs — search, manage, and cluster-dedupe your own memory. The skill
        framework is open. The broker-API integration is the first skill; GitHub,
        calendar, and broker-of-your-choice are next.
      </p>
      <p>
        If you&rsquo;re building an agent that should remember things, DCR is open.
        Talk to me.
      </p>
      <p className="text-gray-500 mt-12">— Anand</p>
    </main>
  );
}
