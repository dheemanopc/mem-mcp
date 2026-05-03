import Link from "next/link";

interface SearchParams {
  cursor?: string;
  type?: string;
  tag?: string;
}

async function fetchMemories(params: SearchParams) {
  const qs = new URLSearchParams();
  if (params.cursor) qs.set("cursor", params.cursor);
  if (params.type) qs.set("type", params.type);
  if (params.tag) {
    for (const t of params.tag.split(",")) qs.append("tag", t.trim());
  }
  const res = await fetch(`http://127.0.0.1:8080/api/web/memories?${qs.toString()}`, { cache: "no-store" });
  if (!res.ok) return null;
  return res.json();
}

export default async function MemoriesPage({ searchParams }: { searchParams: Promise<SearchParams> }) {
  const sp = await searchParams;
  const data = await fetchMemories(sp);
  return (
    <main className="mx-auto max-w-5xl px-4 py-12 space-y-6">
      <h1 className="text-3xl font-bold">Memories</h1>
      <form className="flex flex-wrap gap-3 items-end" method="GET">
        <label className="flex flex-col">
          <span className="text-sm text-gray-600">Type</span>
          <select name="type" defaultValue={sp.type ?? ""} className="border rounded px-2 py-1">
            <option value="">all</option>
            <option value="note">note</option>
            <option value="decision">decision</option>
            <option value="fact">fact</option>
            <option value="snippet">snippet</option>
            <option value="question">question</option>
          </select>
        </label>
        <label className="flex flex-col">
          <span className="text-sm text-gray-600">Tags (comma-separated)</span>
          <input name="tag" defaultValue={sp.tag ?? ""} className="border rounded px-2 py-1" placeholder="project:foo,bar" />
        </label>
        <button className="bg-blue-600 text-white rounded px-4 py-1.5">Filter</button>
      </form>
      {data === null ? (
        <p className="text-red-600">Could not load memories. Are you signed in?</p>
      ) : (
        <>
          <p className="text-sm text-gray-500">{data.results?.length ?? 0} of {data.results?.length ?? "?"} matching</p>
          <ul className="space-y-3">
            {(data.results ?? []).map((m: any) => (
              <li key={m.id} className="border rounded p-4 hover:bg-gray-50">
                <Link href={`/memories/${m.id}`} className="block">
                  <div className="flex justify-between items-start mb-1">
                    <span className="text-xs uppercase text-gray-500">{m.type}</span>
                    <span className="text-xs text-gray-400 font-mono">{m.id?.slice(0, 8)}</span>
                  </div>
                  <p className="line-clamp-3 text-gray-900">{m.content}</p>
                  <div className="mt-2 flex flex-wrap gap-1">
                    {(m.tags ?? []).map((t: string) => (
                      <span key={t} className="text-xs bg-gray-100 px-2 py-0.5 rounded">{t}</span>
                    ))}
                  </div>
                </Link>
              </li>
            ))}
          </ul>
          {data.next_cursor && (
            <Link href={`/memories?cursor=${data.next_cursor}${sp.type ? `&type=${sp.type}` : ""}${sp.tag ? `&tag=${sp.tag}` : ""}`} className="text-blue-600 underline">
              Next page →
            </Link>
          )}
        </>
      )}
    </main>
  );
}
