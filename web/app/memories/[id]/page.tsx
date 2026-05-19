import Link from "next/link";
import { serverApiFetch } from "@/lib/server-api";
import { ThreadView } from "./ThreadView";
import { Card } from "@/components/ui/Card";
import { MarkdownContent } from "@/components/ui/MarkdownContent";

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

function fmt(iso: string): string {
  try {
    return new Date(iso).toLocaleString();
  } catch {
    return iso;
  }
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
    // so we can offer a full render with breadcrumb.
    const single = await serverApiFetch<{ memory: MemoryRecord }>(
      `/api/web/memories/${id}`,
      { redirectTo: `/memories/${id}` }
    );
    return (
      <main className="mx-auto max-w-3xl px-4 py-10 space-y-4">
        <Link href="/memories" className="text-primary hover:underline text-sm">
          ← back to memories
        </Link>
        {single ? (
          <>
            {single.memory.parent_id && (
              <div className="rounded border border-border bg-surface-subtle p-3 text-sm text-ink-muted">
                <div className="flex items-start gap-2">
                  <span className="text-ink-faint">┃</span>
                  <div>
                    This memory is a reply in a thread.{" "}
                    <Link
                      href={`/memories/${single.memory.parent_id}#${id}`}
                      className="text-primary hover:underline"
                    >
                      Open full thread →
                    </Link>
                  </div>
                </div>
              </div>
            )}
            <Card variant="default" pad="lg">
              <div className="space-y-3">
                <div className="flex justify-between items-start text-xs">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="uppercase text-ink-muted font-medium">
                      {single.memory.type}
                    </span>
                    {single.memory.deleted_at && (
                      <span className="text-danger font-medium">deleted</span>
                    )}
                  </div>
                  <code className="text-ink-faint font-mono">
                    {single.memory.id.slice(0, 8)}
                  </code>
                </div>
                <MarkdownContent
                  content={single.memory.content}
                  variant="full"
                />
                <div className="flex flex-wrap items-center justify-between gap-3 pt-2 border-t border-border text-xs">
                  <div className="flex flex-wrap gap-1">
                    {single.memory.tags.map((t) => (
                      <span
                        key={t}
                        className="bg-surface-subtle px-2 py-0.5 rounded text-ink-muted"
                      >
                        {t}
                      </span>
                    ))}
                  </div>
                  <span
                    className="text-ink-faint"
                    title={`created ${fmt(single.memory.created_at)}`}
                  >
                    {fmt(single.memory.updated_at)}
                  </span>
                </div>
              </div>
            </Card>
          </>
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
