import Link from "next/link";

export default function WelcomePage() {
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 space-y-8">
      <header>
        <h1 className="text-3xl font-bold">Welcome to mem-mcp</h1>
        <p className="text-gray-600 mt-2">
          Pick the AI client you'd like to connect first. You can add the others later.
        </p>
      </header>

      <ClientCard
        title="Claude Code (CLI)"
        description="Install the mem-capture and mem-recall skill bundles."
        steps={[
          {label: "Install mem-capture", code: "claude skill install https://memapp.dheemantech.in/skills/mem-capture"},
          {label: "Install mem-recall", code: "claude skill install https://memapp.dheemantech.in/skills/mem-recall"},
          {label: "Verify", code: "Tell Claude: \"Remember that we use Postgres 16.\" Then in a new session: \"What database did we pick?\""},
        ]}
      />

      <ClientCard
        title="Claude.ai (web)"
        description="Add the connector + paste the project instructions."
        steps={[
          {label: "Add connector", code: "Settings → Connectors → Add → URL: https://memsys.dheemantech.in/mcp"},
          {label: "Add project instructions", code: "Copy the block from /skills into your project's custom instructions"},
        ]}
      />

      <ClientCard
        title="ChatGPT (custom GPT)"
        description="Configure a custom GPT to use the mem-mcp connector."
        steps={[
          {label: "Add action", code: "Custom GPT → Configure → Actions → Import: https://memsys.dheemantech.in/mcp"},
          {label: "Paste instructions", code: "From /skills, copy the ChatGPT block into your GPT's instructions"},
        ]}
      />

      <footer className="border-t pt-6 text-sm text-gray-500">
        Need help? See <Link href="/skills" className="underline">/skills</Link> or
        email anand@dheemantech.com.
      </footer>
    </main>
  );
}

function ClientCard({title, description, steps}: {title: string; description: string; steps: {label: string; code: string}[]}) {
  return (
    <section className="border rounded-lg p-6 space-y-4">
      <header>
        <h2 className="text-xl font-semibold">{title}</h2>
        <p className="text-gray-600">{description}</p>
      </header>
      <ol className="space-y-3">
        {steps.map((step, i) => (
          <li key={i}>
            <div className="font-medium text-sm text-gray-700">{i + 1}. {step.label}</div>
            <pre className="mt-1 bg-gray-100 rounded p-3 text-sm font-mono overflow-x-auto whitespace-pre-wrap break-all">{step.code}</pre>
          </li>
        ))}
      </ol>
    </section>
  );
}
