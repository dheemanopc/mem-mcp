import Link from "next/link";
import { serverApiFetch } from "@/lib/server-api";
import { ApproveRejectClient } from "./ApproveRejectClient";

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

export default async function SignupRequestDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const data = await serverApiFetch<{ requests: SignupRequest[] }>(
    `/api/web/admin/signup-requests`,
    { redirectTo: `/admin/signup-requests/${id}` }
  );
  const req = data?.requests.find((r) => r.id === id);

  if (!req) {
    return (
      <main className="mx-auto max-w-2xl px-4 py-12">
        <h1 className="text-3xl font-bold mb-4">Request not found</h1>
        <Link href="/admin/signup-requests" className="text-blue-600 hover:underline">
          ← back to list
        </Link>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-12 space-y-6">
      <Link href="/admin/signup-requests" className="text-blue-600 hover:underline text-sm">
        ← back to list
      </Link>

      <div>
        <h1 className="text-2xl font-bold">{req.email}</h1>
        {req.name && <p className="text-gray-600">{req.name}</p>}
        <p className="text-sm text-gray-500 mt-1">
          Submitted {new Date(req.submitted_at).toLocaleString()}
          {req.email_verified_at &&
            ` · verified ${new Date(req.email_verified_at).toLocaleString()}`}
        </p>
        <p className="mt-2">
          Status: <strong>{req.status.replace("_", " ")}</strong>
          {req.reviewed_by && ` — by ${req.reviewed_by}`}
        </p>
      </div>

      {req.reason && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Reason</h2>
          <p className="whitespace-pre-wrap text-gray-800">{req.reason}</p>
        </section>
      )}

      {req.client_intent.length > 0 && (
        <section>
          <h2 className="text-lg font-semibold mb-2">Clients</h2>
          <ul className="list-disc list-inside text-gray-800">
            {req.client_intent.map((c) => (
              <li key={c}>{c}</li>
            ))}
          </ul>
        </section>
      )}

      {req.status === "pending" && <ApproveRejectClient id={req.id} email={req.email} />}
    </main>
  );
}
