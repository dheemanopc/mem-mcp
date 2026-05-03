/**
 * Dashboard page — fetches /api/web/stats server-side.
 * Renders memory counts, usage by type, and quota information.
 */

async function fetchStats() {
  try {
    const res = await fetch("http://127.0.0.1:8080/api/web/stats", {
      cache: "no-store",
    });
    if (!res.ok) {
      return null;
    }
    return res.json();
  } catch {
    return null;
  }
}

interface Stats {
  total_memories: number;
  by_type: Record<string, number>;
  today: {
    writes: number;
    reads: number;
    embed_tokens: number;
  };
  quota: {
    tier: string;
    memories_limit: number;
    embed_tokens_daily_limit: number;
  };
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="border rounded-lg p-4">
      <div className="text-sm text-gray-500">{label}</div>
      <div className="text-2xl font-bold">{value ?? 0}</div>
    </div>
  );
}

function QuotaBar({
  label,
  used,
  limit,
}: {
  label: string;
  used: number;
  limit: number;
}) {
  const pct = limit > 0 ? Math.min(100, Math.round((used / limit) * 100)) : 0;
  return (
    <div>
      <div className="flex justify-between text-sm">
        <span>{label}</span>
        <span className="font-mono">
          {used} / {limit}
        </span>
      </div>
      <div className="bg-gray-200 rounded h-2 mt-1">
        <div
          className="bg-blue-500 h-2 rounded"
          style={{ width: `${pct}%` }}
        />
      </div>
    </div>
  );
}

export default async function DashboardPage() {
  const stats: Stats | null = await fetchStats();

  if (!stats) {
    return (
      <main className="mx-auto max-w-4xl px-4 py-12">
        <h1 className="text-3xl font-bold">Dashboard</h1>
        <p className="text-red-600 mt-4">
          Could not load stats. Are you signed in?
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-4xl px-4 py-12 space-y-8">
      <h1 className="text-3xl font-bold">Dashboard</h1>

      <section className="grid grid-cols-2 gap-4">
        <Stat label="Total memories" value={stats.total_memories} />
        <Stat label="Writes today" value={stats.today.writes} />
        <Stat label="Reads today" value={stats.today.reads} />
        <Stat
          label="Embed tokens today"
          value={stats.today.embed_tokens}
        />
      </section>

      <section>
        <h2 className="text-xl font-semibold mb-4">By type</h2>
        <ul className="space-y-1">
          {Object.entries(stats.by_type ?? {}).map(([type, n]) => (
            <li key={type} className="flex justify-between border-b py-1">
              <span>{type}</span>
              <span className="font-mono">{n as number}</span>
            </li>
          ))}
        </ul>
      </section>

      <section>
        <h2 className="text-xl font-semibold mb-4">
          Quota — {stats.quota?.tier}
        </h2>
        <div className="space-y-3">
          <QuotaBar
            label="Memories"
            used={stats.total_memories}
            limit={stats.quota?.memories_limit ?? 0}
          />
          <QuotaBar
            label="Embed tokens today"
            used={stats.today.embed_tokens}
            limit={stats.quota?.embed_tokens_daily_limit ?? 0}
          />
        </div>
      </section>
    </main>
  );
}
