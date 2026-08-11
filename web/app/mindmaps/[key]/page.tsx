import Link from "next/link";
import { notFound } from "next/navigation";
import { serverApiFetch } from "@/lib/server-api";
import { MapView, type MapPayload } from "./map-view";

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
    <main className="mx-auto max-w-4xl px-4 py-8">
      <nav className="mb-4">
        <Link
          href="/mindmaps"
          className="text-sm text-slate-500 hover:text-slate-800 dark:text-slate-400 dark:hover:text-slate-200"
        >
          ← All maps
        </Link>
      </nav>
      <MapView mapKey={mapKey} data={data} />
    </main>
  );
}
