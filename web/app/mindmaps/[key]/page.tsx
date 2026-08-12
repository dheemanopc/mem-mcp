import Link from "next/link";
import { notFound } from "next/navigation";
import { serverApiFetch } from "@/lib/server-api";
import { GraphView, type MapPayload } from "./graph-view";

export const dynamic = "force-dynamic";

export default async function MindmapDetailPage({
  params,
}: {
  params: Promise<{ key: string }>;
}) {
  const { key } = await params;
  const mapKey = decodeURIComponent(key);

  // include=all / depth=full: the cheap defaults exist to keep an AGENT's
  // context small. A human reading one map in a browser wants the whole thing.
  const data = await serverApiFetch<MapPayload>(
    `/api/web/mindmaps/${encodeURIComponent(mapKey)}?include=all&depth=full`,
    { redirectTo: `/mindmaps/${key}` },
  );

  if (!data) notFound();

  return (
    // The canvas wants width: this page runs wider than the reading pages.
    <main className="mx-auto max-w-[1400px] px-4 py-6">
      <nav className="mb-4">
        <Link
          href="/mindmaps"
          className="text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
        >
          ← All maps
        </Link>
      </nav>
      <GraphView mapKey={mapKey} data={data} />
    </main>
  );
}
