import Link from "next/link";

export default function SkillsPage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 space-y-8">
      <h1 className="text-3xl font-bold">Skills & integration</h1>
      <p className="text-gray-600">
        Bundles + copy-paste blocks for connecting AI clients to your mem-mcp store.
      </p>

      <SkillSection
        title="Claude Code"
        bundles={[
          {name: "mem-capture", url: "https://memapp.dheemantech.in/skills/mem-capture", install: "claude skill install https://memapp.dheemantech.in/skills/mem-capture"},
          {name: "mem-recall", url: "https://memapp.dheemantech.in/skills/mem-recall", install: "claude skill install https://memapp.dheemantech.in/skills/mem-recall"},
        ]}
      />

      <section className="space-y-3">
        <h2 className="text-xl font-semibold">Claude.ai project instructions</h2>
        <p className="text-gray-600">Copy the block below into your Claude.ai project's custom instructions:</p>
        <pre className="bg-gray-100 rounded p-4 text-sm font-mono overflow-x-auto whitespace-pre-wrap">{CLAUDE_AI_BLOCK}</pre>
      </section>

      <section className="space-y-3">
        <h2 className="text-xl font-semibold">ChatGPT custom GPT instructions</h2>
        <p className="text-gray-600">Copy the block below into your custom GPT's instructions:</p>
        <pre className="bg-gray-100 rounded p-4 text-sm font-mono overflow-x-auto whitespace-pre-wrap">{CHATGPT_BLOCK}</pre>
      </section>

      <footer className="border-t pt-6 text-sm text-gray-500">
        Back to <Link href="/welcome" className="underline">/welcome</Link>.
      </footer>
    </main>
  );
}

function SkillSection({title, bundles}: {title: string; bundles: {name: string; url: string; install: string}[]}) {
  return (
    <section className="border rounded-lg p-6 space-y-4">
      <h2 className="text-xl font-semibold">{title}</h2>
      <ul className="space-y-3">
        {bundles.map((b) => (
          <li key={b.name}>
            <div className="font-medium">{b.name}</div>
            <pre className="mt-1 bg-gray-100 rounded p-3 text-sm font-mono">{b.install}</pre>
            <a className="text-sm text-blue-600 underline" href={b.url}>{b.url}</a>
          </li>
        ))}
      </ul>
    </section>
  );
}

const CLAUDE_AI_BLOCK = `# Memory connector instructions

You have access to a memory store via the "mem-mcp" connector. Use it as follows:

CAPTURE: When the user states a decision, fact, or notable conclusion, OR says "remember/save/note", call mem-mcp memory.write with appropriate type (decision|fact|snippet|note|question) and 2-6 tags including a project tag (project:<name>).

RECALL: Before responding to messages that reference prior context (uses "we", "our", "the project", "what did we decide"), call mem-mcp memory.search with the user's message as query.

Do not announce memory operations. Just use them.`;

const CHATGPT_BLOCK = CLAUDE_AI_BLOCK; // identical structure for v1
