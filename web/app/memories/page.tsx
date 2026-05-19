import { serverApiFetch } from "@/lib/server-api";
import { MemoriesView } from "./memories-view";

export const dynamic = "force-dynamic";

interface SearchParams {
  tab?: "browse" | "stale";
  type?: string;
  tag?: string;
  stale_mode?: "updated" | "accessed";
  stale_days?: string;
}

interface TagsResponse {
  tags: string[];
}

interface StatsResponse {
  memory_count: number;
  total_storage_bytes: number;
  storage_bytes_updated_at: string | null;
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(2)} GB`;
}

function formatRelative(iso: string | null): string {
  if (!iso) return "not yet computed";
  const ms = Date.now() - new Date(iso).getTime();
  if (ms < 60_000) return "moments ago";
  if (ms < 3600_000) return `${Math.floor(ms / 60_000)} min ago`;
  if (ms < 86400_000) return `${Math.floor(ms / 3600_000)} h ago`;
  return `${Math.floor(ms / 86400_000)} d ago`;
}

export default async function MemoriesPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  // Two server-side fetches in parallel — tags for autocomplete, stats for header panel.
  const [tagsResp, statsResp] = await Promise.all([
    serverApiFetch<TagsResponse>("/api/web/memories/management/tags", { redirectTo: "/memories" }),
    serverApiFetch<StatsResponse>("/api/web/memories/management/stats", { redirectTo: "/memories" }),
  ]);
  const allTags = tagsResp?.tags ?? [];

  return (
    <main className="mx-auto max-w-6xl px-4 py-10 space-y-6">
      <header>
        <h1 className="text-3xl font-bold">Memories</h1>
        <p className="text-sm text-gray-500 mt-1">
          Browse, filter, and clean up your stored memories.
        </p>
      </header>
      {statsResp && (
        <div className="rounded border bg-gray-50 px-4 py-3 flex flex-wrap items-baseline gap-x-6 gap-y-1 text-sm">
          <span>
            <span className="font-semibold">{statsResp.memory_count.toLocaleString()}</span>
            <span className="text-gray-500"> memories</span>
          </span>
          <span>
            <span className="font-semibold">{formatBytes(statsResp.total_storage_bytes)}</span>
            <span className="text-gray-500"> storage</span>
          </span>
          <span className="text-gray-400 text-xs">
            storage recomputed {formatRelative(statsResp.storage_bytes_updated_at)}
          </span>
        </div>
      )}
      <MemoriesView allTags={allTags} initialTab={sp.tab ?? "browse"} initialType={sp.type} initialTag={sp.tag} />
    </main>
  );
}
