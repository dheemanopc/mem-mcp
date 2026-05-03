import Link from "next/link";
import { MemoryActionsClient } from "./MemoryActionsClient";

async function fetchMemory(id: string, includeHistory: boolean) {
  const qs = includeHistory ? "?history=true" : "";
  const res = await fetch(`http://127.0.0.1:8080/api/web/memories/${id}${qs}`, { cache: "no-store" });
  if (!res.ok) return null;
  return res.json();
}

export default async function MemoryDetailPage({ params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  const data = await fetchMemory(id, true);
  if (!data) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-12">
        <h1 className="text-3xl font-bold">Memory not found</h1>
        <Link href="/memories" className="text-blue-600 underline">← back</Link>
      </main>
    );
  }
  return (
    <main className="mx-auto max-w-3xl px-4 py-12 space-y-6">
      <Link href="/memories" className="text-blue-600 underline text-sm">← back to memories</Link>
      <header className="space-y-2">
        <div className="flex justify-between items-start">
          <h1 className="text-2xl font-bold">{data.memory.type} — version {data.memory.version}</h1>
          <span className="text-xs font-mono text-gray-400">{data.memory.id}</span>
        </div>
        <div className="flex flex-wrap gap-1">
          {(data.memory.tags ?? []).map((t: string) => (
            <span key={t} className="text-xs bg-gray-100 px-2 py-0.5 rounded">{t}</span>
          ))}
        </div>
        <p className="text-xs text-gray-500">
          created {data.memory.created_at} · updated {data.memory.updated_at}
          {data.memory.deleted_at && <span className="text-red-600"> · DELETED at {data.memory.deleted_at}</span>}
        </p>
      </header>
      <article className="prose max-w-none">
        <pre className="bg-gray-100 rounded p-4 whitespace-pre-wrap font-sans">{data.memory.content}</pre>
      </article>
      <MemoryActionsClient id={data.memory.id} deleted={!!data.memory.deleted_at} />
      {data.history?.length > 0 && (
        <section className="space-y-3">
          <h2 className="text-xl font-semibold">History</h2>
          <ul className="space-y-2">
            {data.history.map((h: any) => (
              <li key={h.id} className="border rounded p-3 text-sm bg-gray-50">
                <div className="flex justify-between text-xs text-gray-500 mb-1">
                  <span>v{h.version}</span>
                  <span>{h.created_at}</span>
                </div>
                <pre className="whitespace-pre-wrap font-sans">{h.content}</pre>
              </li>
            ))}
          </ul>
        </section>
      )}
    </main>
  );
}
