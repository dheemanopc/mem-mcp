import Link from "next/link";
import { serverApiFetch } from "@/lib/server-api";
import { ThreadView } from "./ThreadView";

export const dynamic = "force-dynamic";

interface MemoryRecord {
  id: string;
  content: string;
  type: string;
  tags: string[];
  version: number;
  is_current: boolean;
  parent_id: string | null;
  supersedes: string | null;
  superseded_by: string | null;
  created_at: string;
  updated_at: string;
  deleted_at: string | null;
}

interface ThreadResponse {
  root: MemoryRecord;
  replies: MemoryRecord[];
}

export default async function MemoryDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const data = await serverApiFetch<ThreadResponse>(
    `/api/web/memories/${id}/thread`,
    { redirectTo: `/memories/${id}` }
  );

  if (!data) {
    // thread_get rejects replies — fall back to the single-memory endpoint
    // so we can offer a link up to the parent.
    const single = await serverApiFetch<{ memory: MemoryRecord }>(
      `/api/web/memories/${id}`,
      { redirectTo: `/memories/${id}` }
    );
    return (
      <main className="mx-auto max-w-3xl px-4 py-12 space-y-4">
        <Link href="/memories" className="text-primary hover:underline text-sm">
          ← back to memories
        </Link>
        {single ? (
          <div className="rounded border border-warn bg-warn-muted p-4 text-sm text-ink">
            This memory is a reply, not a root.{" "}
            {single.memory.parent_id && (
              <Link
                href={`/memories/${single.memory.parent_id}`}
                className="text-primary hover:underline"
              >
                Open its parent thread →
              </Link>
            )}
          </div>
        ) : (
          <h1 className="text-3xl font-bold">Memory not found</h1>
        )}
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-10 space-y-4">
      <Link href="/memories" className="text-primary hover:underline text-sm">
        ← back to memories
      </Link>
      <ThreadView root={data.root} replies={data.replies} />
    </main>
  );
}
