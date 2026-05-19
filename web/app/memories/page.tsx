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

export default async function MemoriesPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  // Fetch tag universe once — used by the autocomplete chip selector.
  const tagsResp = await serverApiFetch<TagsResponse>(
    "/api/web/memories/management/tags",
    { redirectTo: "/memories" }
  );
  const allTags = tagsResp?.tags ?? [];

  return (
    <main className="mx-auto max-w-6xl px-4 py-10 space-y-6">
      <header>
        <h1 className="text-3xl font-bold">Memories</h1>
        <p className="text-sm text-gray-500 mt-1">
          Browse, filter, and clean up your stored memories.
        </p>
      </header>
      <MemoriesView allTags={allTags} initialTab={sp.tab ?? "browse"} initialType={sp.type} initialTag={sp.tag} />
    </main>
  );
}
