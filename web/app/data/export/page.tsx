import Link from "next/link";

export default function ExportPage() {
  return (
    <main className="mx-auto max-w-2xl px-4 py-12 space-y-6">
      <h1 className="text-3xl font-bold">Export your data</h1>
      <p className="text-gray-600">
        Download a JSON file containing all your memories (current versions, history,
        and soft-deleted) plus your audit log. Per DPDP right-to-access.
      </p>
      <p className="text-sm text-gray-500">
        The file is named <code className="bg-gray-100 px-1 rounded">mem-mcp-export-&lt;tenant_id&gt;-&lt;timestamp&gt;.json</code>.
      </p>
      <a
        href="/api/web/data/export"
        download
        className="inline-block bg-blue-600 text-white rounded px-4 py-2 font-medium"
      >
        Download export
      </a>
      <p className="text-xs text-gray-400">
        Large exports may take a few seconds to generate. Don't close the tab while downloading.
      </p>
      <Link href="/settings" className="text-sm text-blue-600 underline">← back to settings</Link>
    </main>
  );
}
