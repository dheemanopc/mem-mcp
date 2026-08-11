import Link from "next/link";
import { serverApiFetch } from "@/lib/server-api";

export const dynamic = "force-dynamic";

interface MapSummary {
  map_key: string;
  root_memory_id: string;
  title: string;
  state: string;
  open_loop_count: number;
  last_owner_write_at: string | null;
  created_at: string;
}

interface QueuedQuestion {
  map_key: string;
  map_title: string;
  memory_id: string;
  node_role: string;
  content: string;
  decision_criteria: string | null;
  asked_at: string;
}

function formatRelative(iso: string | null): string {
  if (!iso) return "—";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return "moments ago";
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)} min ago`;
  if (ms < 86_400_000) return `${Math.floor(ms / 3_600_000)} h ago`;
  return `${Math.floor(ms / 86_400_000)} d ago`;
}

export default async function MindmapsPage() {
  const [queueRes, listRes] = await Promise.all([
    serverApiFetch<{ questions: QueuedQuestion[] }>("/api/web/mindmaps/queue", {
      redirectTo: "/mindmaps",
    }),
    serverApiFetch<{ maps: MapSummary[] }>("/api/web/mindmaps?state=all", {
      redirectTo: "/mindmaps",
    }),
  ]);

  const questions = queueRes?.questions ?? [];
  const maps = listRes?.maps ?? [];
  const live = maps.filter((m) => m.state === "live");
  const archived = maps.filter((m) => m.state !== "live");

  return (
    <main className="mx-auto max-w-4xl px-4 py-8">
      <header className="mb-8">
        <h1 className="text-2xl font-semibold text-slate-900 dark:text-slate-100">
          Mind maps
        </h1>
        <p className="mt-1 text-sm text-slate-600 dark:text-slate-400">
          Answer open questions here, then tell the agent to reload. It picks up only
          what changed instead of re-reading the whole map.
        </p>
      </header>

      {/* The inbox leads, not the catalogue: the failure mode of agent-driven
          thinking is never noticing you were asked something. */}
      <section className="mb-10">
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Waiting on you
          {questions.length > 0 && (
            <span className="ml-2 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
              {questions.length}
            </span>
          )}
        </h2>

        {questions.length === 0 ? (
          <p className="rounded-lg border border-slate-200 bg-slate-50 px-4 py-6 text-sm text-slate-500 dark:border-slate-800 dark:bg-slate-900/40 dark:text-slate-400">
            Nothing is blocked on you.
          </p>
        ) : (
          <ul className="space-y-3">
            {questions.map((q) => (
              <li
                key={q.memory_id}
                className="rounded-lg border border-amber-200 bg-amber-50/60 p-4 dark:border-amber-900/50 dark:bg-amber-950/20"
              >
                <Link
                  href={`/mindmaps/${encodeURIComponent(q.map_key)}#${q.memory_id}`}
                  className="block"
                >
                  <p className="text-xs text-slate-500 dark:text-slate-400">
                    {q.map_title} · asked {formatRelative(q.asked_at)}
                  </p>
                  <p className="mt-1 text-sm font-medium text-slate-900 dark:text-slate-100">
                    {q.content}
                  </p>
                  {q.decision_criteria && (
                    <p className="mt-2 border-l-2 border-amber-300 pl-3 text-xs text-slate-600 dark:border-amber-700 dark:text-slate-400">
                      <span className="font-medium">What turns on this:</span>{" "}
                      {q.decision_criteria}
                    </p>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section>
        <h2 className="mb-3 text-sm font-medium uppercase tracking-wide text-slate-500 dark:text-slate-400">
          Live maps
        </h2>
        {live.length === 0 ? (
          <p className="text-sm text-slate-500 dark:text-slate-400">No live maps.</p>
        ) : (
          <ul className="divide-y divide-slate-200 rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
            {live.map((m) => (
              <li key={m.root_memory_id}>
                <Link
                  href={`/mindmaps/${encodeURIComponent(m.map_key)}`}
                  className="flex items-center justify-between gap-4 px-4 py-3 hover:bg-slate-50 dark:hover:bg-slate-900/40"
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-slate-900 dark:text-slate-100">
                      {m.title}
                    </span>
                    <span className="block truncate text-xs text-slate-500 dark:text-slate-400">
                      {m.map_key}
                    </span>
                  </span>
                  {m.open_loop_count > 0 && (
                    <span className="shrink-0 rounded-full bg-amber-100 px-2 py-0.5 text-xs font-semibold text-amber-800 dark:bg-amber-900/40 dark:text-amber-300">
                      {m.open_loop_count} open
                    </span>
                  )}
                </Link>
              </li>
            ))}
          </ul>
        )}

        {archived.length > 0 && (
          <details className="mt-6">
            <summary className="cursor-pointer text-sm text-slate-500 hover:text-slate-700 dark:text-slate-400 dark:hover:text-slate-200">
              Archived ({archived.length}) — read-only
            </summary>
            <ul className="mt-2 divide-y divide-slate-200 rounded-lg border border-slate-200 dark:divide-slate-800 dark:border-slate-800">
              {archived.map((m) => (
                <li key={m.root_memory_id}>
                  <Link
                    href={`/mindmaps/${encodeURIComponent(m.map_key)}`}
                    className="block px-4 py-3 text-sm text-slate-600 hover:bg-slate-50 dark:text-slate-400 dark:hover:bg-slate-900/40"
                  >
                    {m.title}
                  </Link>
                </li>
              ))}
            </ul>
          </details>
        )}
      </section>
    </main>
  );
}
