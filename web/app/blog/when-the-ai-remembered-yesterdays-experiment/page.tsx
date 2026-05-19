import Link from "next/link";

export const metadata = {
  title:
    "When the AI Remembered Yesterday's Experiment and Rewrote Today's Design — memsys",
  description:
    "Three memories from yesterday rewrote a design we'd just finished agreeing to. The brainstorm provides creativity. The memory provides discipline.",
};

export default function Post() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 prose">
      <p className="text-sm text-gray-500 mb-2">
        <Link href="/blog" className="text-blue-600">
          ← back to blog
        </Link>
      </p>
      <h1>When the AI Remembered Yesterday's Experiment and Rewrote Today's Design</h1>
      <p className="text-sm text-gray-500 -mt-3">by Anand Vaidya · May 19, 2026</p>

      <p>
        <em>On building with AI assistants that have persistent memory across sessions</em>
      </p>

      <p>
        We've been building an algorithmic trading system on top of Zerodha's Kite API for a few months. The stack is unremarkable on the surface: a Go MCP server for Kite, a Python memory service we call memsys, Opus and Sonnet doing the actual reasoning, and a chat-based cockpit instead of a custom dashboard.
      </p>

      <p>
        What's been interesting is not the stack. It's what happens when one of the components — the memory service — quietly changes how the other components get designed.
      </p>

      <p>
        This is the story of one such moment.
      </p>

      <h2>The problem we sat down to solve</h2>

      <p>
        The trading agent reads charts. To read a chart, it needs indicators — RSI, MACD, moving averages, Bollinger bands, the usual suspects. Until today, the agent was fetching raw OHLCV bars from Kite and computing indicators itself, in its head, by reasoning over the numbers. This works, but it burns tokens — and worse, the analysis depends on the agent's arithmetic, which isn't something you want to bet money on.
      </p>

      <p>
        The obvious fix: expose indicators as tools. Let the agent ask <code>get_rsi(WIPRO, 15m, period=14)</code> and let the server compute it deterministically with pandas-ta.
      </p>

      <p>
        The obvious fix has a catch. We didn't know which indicators the agent would want. We didn't want to refetch OHLCV on every tool call. And we had a hard constraint: the Kite MCP server is in Go and we don't want to fork it — we want to pull upstream updates cleanly forever.
      </p>

      <p>
        So we started brainstorming.
      </p>

      <h2>The brainstorm</h2>

      <p>
        We walked through the design tree the way you'd expect. Fixed catalog of indicators versus expression engine versus hybrid. Where the cache lives — in the Go MCP, in memsys, on disk. How sessions work. How indicator results come back to the model. Whether to fork the Go server or sidecar around it. Three or four turns of back-and-forth.
      </p>

      <p>
        By the time we'd settled on &ldquo;memsys is the front door, in-memory OHLCV cache, six new MCP tools, lazy trailing-bar refresh,&rdquo; it felt like good architecture. Clean. Plausible.
      </p>

      <p>
        Then I asked the assistant to write the spec up as a memory and store it.
      </p>

      <h2>What happened next</h2>

      <p>
        Before writing anything, the assistant searched memsys for prior architectural decisions. Standard practice — you don't want to write a &ldquo;new&rdquo; decision that conflicts with three older ones nobody remembered.
      </p>

      <p>
        The search came back with eight memories that mattered. Three of them changed the design we'd just finished agreeing to.
      </p>

      <p>
        <strong>The first one rewrote the architecture.</strong> A milestone memory from two days ago documented that memsys already had a native Python Kite client built into a skill framework. We'd spent the entire conversation discussing how memsys-the-Python-service would talk to kite-mcp-the-Go-service over MCP, what the IPC overhead would look like, where credentials lived. None of that was relevant. Memsys had been calling Kite directly in Python for forty-eight hours and I had forgotten. The &ldquo;two-service architecture&rdquo; we'd designed didn't need to exist. The feature was a Phase 2 extension of one Python service — half the moving parts of what we'd just spec'd.
      </p>

      <p>
        <strong>The second one inverted a token-economics assumption.</strong> A memory from yesterday — sixteen hours before this conversation started — documented an experiment we'd run. We'd tested whether pre-computing per-bar indicators and stuffing them into the agent's scan bundle would save tokens. It did save tokens. It also broke convergence between Opus and Sonnet on a specific stock, SBIN: Opus stayed correctly short on a continuing C-wave, but Sonnet flipped long after reading a daily MACD histogram improvement as a bottoming signal. Pre-computed indicators <em>surfaced an interpretive split between the two models that the raw structural data did not produce.</em>
      </p>

      <p>
        That experiment had already locked the direction: lazy fetch, not eager bake. The agent should pull only the indicator it needs, only when it needs it, only at the timestamp that matters. The brainstorm we'd just finished was an implementation of a direction that had already been decided — but I had been treating the direction itself as still open. Without the memory check, the spec would have included options like &ldquo;should we bake some indicators into the bundle for efficiency?&rdquo; — a question with empirical evidence in memsys saying no, the cost is convergence, which is more expensive than tokens.
      </p>

      <p>
        <strong>The third one named the killer feature.</strong> The same memory contained a sketched tool signature including an <code>at_timestamp</code> parameter — &ldquo;give me RSI at this specific bar.&rdquo; It noted this was the most important feature for Elliott Wave analysis, because momentum divergence at swing pivots is what wave theory is about. I had not included this in the brainstorm at all. The assistant had not raised it. It was sitting in a memory from yesterday, waiting.
      </p>

      <h2>The rewrite</h2>

      <p>
        The spec got rewritten on the fly. The two-service architecture collapsed into a single-service Phase 2 extension. The &ldquo;should we eager-bake some indicators&rdquo; question disappeared. The <code>at_timestamp</code> parameter became a first-class tool input. The implementation estimate dropped from a week to two-to-three days. The whole shape of the feature was different — better — because of three memories that already existed.
      </p>

      <p>
        The final spec went into memsys as a reply threaded under yesterday's experiment memory, so the lineage stays clear: yesterday's empirical finding, today's locked design, tomorrow's implementation.
      </p>

      <h2>What's actually interesting about this</h2>

      <p>
        Persistent memory for AI assistants is usually pitched as a personalization feature. &ldquo;It remembers your name.&rdquo; &ldquo;It remembers your preferences.&rdquo; This is the version that ships in consumer chat apps and it is fine but it is not the interesting version.
      </p>

      <p>
        The interesting version is what happened here: <strong>the memory wasn't a fact about me, it was a piece of empirical evidence from a past session that was directly load-bearing on a current design decision.</strong> Without it, the design would have been wrong in a specific, expensive way — we'd have built the eager-bake path, watched Sonnet flip long on a trade Opus was correctly short on, and only then realized the experiment we ran sixteen hours earlier had already answered the question.
      </p>

      <p>
        The memory didn't replace the brainstorm. The brainstorm was valuable — it walked through the design space and surfaced trade-offs we hadn't seen before. But the memory pruned the design space at the end, hard, by re-introducing prior commitments that the brainstorm had forgotten.
      </p>

      <p>
        This is a different mode of working with AI than &ldquo;ask the model a question, get an answer.&rdquo; It's closer to working with a colleague who has read every meeting note from the project, never forgets a decision, and can recall the relevant ones at the moment they matter. The brainstorm provides creativity. The memory provides discipline.
      </p>

      <h2>A few things this changes</h2>

      <p>
        <strong>Where decisions live.</strong> Decisions in our project live in memsys, not in our heads or in random Slack messages. Every architectural choice, every empirical finding, every &ldquo;we considered X and rejected it because Y&rdquo; — it all goes into a <code>type=decision</code> or <code>type=question</code> memory with tags. The cost is the discipline of writing it down. The benefit is that future sessions — with us or with the AI — start from the actual state, not from someone's possibly-stale recollection.
      </p>

      <p>
        <strong>Threading matters more than I expected.</strong> Today's spec is a reply to yesterday's memory. The thread is the design's history. Six months from now, when someone asks &ldquo;why did we go lazy-fetch instead of eager-bake,&rdquo; the answer is two clicks away, with the SBIN experiment data attached.
      </p>

      <p>
        <strong>The AI gets better at the project over time, not just at the task.</strong> Each session leaves artifacts. The next session reads them. The project accumulates institutional knowledge that the assistant participates in, not just consumes. It's not that the model is learning — it's that the <em>context</em> it operates in is learning.
      </p>

      <p>
        <strong>Brainstorming with persistent memory is qualitatively different from brainstorming without it.</strong> Without memory, every conversation starts from zero and the participants have to manually recall what was decided before. The brainstorm wanders, sometimes re-litigating settled questions. With memory, the brainstorm wanders too — but the synthesis step at the end pulls in everything relevant from prior work. The wandering stays useful; the conclusions stay grounded.
      </p>

      <h2>What I'd recommend</h2>

      <p>
        If you're building anything serious with an AI assistant — code, architecture, research, writing — give it persistent memory. Make the act of &ldquo;saving a decision&rdquo; cheap and natural enough that you actually do it. Use tags. Use threading. Treat the memory store as the source of truth for what was decided, and let the chat be the place where decisions get made and questioned.
      </p>

      <p>
        Then, before the AI writes anything substantive — a design doc, a spec, a plan — let it search the memory store. Not to look up facts about you. To look up evidence about the project.
      </p>

      <p>
        The first time it tells you &ldquo;before I write this, I want to flag that an experiment from yesterday contradicts one of the assumptions we just walked through&rdquo; — that's when the value lands.
      </p>

      <p>
        It happened to me today. The spec is better for it.
      </p>

      <hr />

      <p className="text-sm text-gray-500 italic">
        The trading system, the memory service, and the cockpit pattern described here are part of an ongoing build for Indian equity markets. The empirical convergence finding referenced (Opus and Sonnet diverging on SBIN given pre-computed momentum indicators, converging without them) is from a controlled comparison on May 19, 2026.
      </p>
    </main>
  );
}
