import Link from "next/link";
import { serverApiFetch } from "@/lib/server-api";

interface SignupRequest {
  id: string;
  email: string;
  name: string | null;
  reason: string | null;
  client_intent: string[];
  status: string;
  submitted_at: string;
  email_verified_at: string | null;
  reviewed_at: string | null;
  reviewed_by: string | null;
}

interface RequestsResponse {
  requests: SignupRequest[];
}

interface SearchParams {
  status?: string;
}

export default async function AdminSignupRequestsPage({
  searchParams,
}: {
  searchParams: Promise<SearchParams>;
}) {
  const sp = await searchParams;
  const status = sp.status ?? "pending";
  const qs = status === "all" ? "" : `?status=${encodeURIComponent(status)}`;
  const data = await serverApiFetch<RequestsResponse>(
    `/api/web/admin/signup-requests${qs}`,
    { redirectTo: "/admin/signup-requests" }
  );

  const requests = data?.requests ?? [];

  return (
    <main className="mx-auto max-w-5xl px-4 py-12">
      <h1 className="text-3xl font-bold mb-6">Signup requests</h1>

      <nav className="flex gap-2 mb-6 text-sm">
        {["pending", "approved", "rejected", "awaiting_verification", "all"].map((s) => (
          <Link
            key={s}
            href={`/admin/signup-requests?status=${s}`}
            className={`px-3 py-1 rounded border ${
              status === s
                ? "bg-blue-600 text-white border-blue-600"
                : "bg-white text-gray-700 border-gray-300 hover:bg-gray-50"
            }`}
          >
            {s.replace("_", " ")}
          </Link>
        ))}
      </nav>

      {requests.length === 0 ? (
        <p className="text-gray-500">No requests in this state.</p>
      ) : (
        <ul className="space-y-3">
          {requests.map((r) => (
            <li
              key={r.id}
              className="border rounded p-4 flex justify-between items-start gap-4"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="font-mono text-sm">{r.email}</span>
                  {r.name && <span className="text-gray-500">— {r.name}</span>}
                  <StatusBadge status={r.status} />
                </div>
                {r.reason && (
                  <p className="text-sm text-gray-700 mt-1 line-clamp-2">
                    {r.reason}
                  </p>
                )}
                <div className="text-xs text-gray-500 mt-2">
                  Submitted {new Date(r.submitted_at).toLocaleString()}
                  {r.email_verified_at &&
                    ` · verified ${new Date(r.email_verified_at).toLocaleString()}`}
                  {r.client_intent.length > 0 &&
                    ` · clients: ${r.client_intent.join(", ")}`}
                </div>
              </div>
              <Link
                href={`/admin/signup-requests/${r.id}`}
                className="text-blue-600 hover:underline text-sm shrink-0"
              >
                Review →
              </Link>
            </li>
          ))}
        </ul>
      )}
    </main>
  );
}

function StatusBadge({ status }: { status: string }) {
  const colors: Record<string, string> = {
    pending: "bg-yellow-100 text-yellow-800",
    awaiting_verification: "bg-gray-100 text-gray-700",
    approved: "bg-green-100 text-green-800",
    rejected: "bg-red-100 text-red-800",
    expired: "bg-gray-100 text-gray-500",
  };
  const cls = colors[status] ?? "bg-gray-100 text-gray-700";
  return (
    <span className={`text-xs px-2 py-0.5 rounded ${cls}`}>
      {status.replace("_", " ")}
    </span>
  );
}
