import { DeleteFlowClient } from "./DeleteFlowClient";
import Link from "next/link";

async function fetchMe() {
  const res = await fetch("http://127.0.0.1:8080/api/web/me", { cache: "no-store" });
  if (!res.ok) return null;
  return res.json();
}

export default async function DeletePage() {
  const me = await fetchMe();
  if (!me) {
    return (
      <main className="mx-auto max-w-2xl px-4 py-12">
        <h1 className="text-3xl font-bold">Delete account</h1>
        <p className="text-red-600">Not signed in.</p>
      </main>
    );
  }
  const isPending = me.tenant.status === "pending_deletion";
  return (
    <main className="mx-auto max-w-2xl px-4 py-12 space-y-6">
      <h1 className="text-3xl font-bold">Delete account</h1>
      {isPending ? (
        <PendingBanner />
      ) : (
        <DeleteFlowClient />
      )}
      <Link href="/settings" className="text-sm text-blue-600 underline">← back to settings</Link>
    </main>
  );
}

function PendingBanner() {
  return (
    <section className="border-2 border-orange-400 bg-orange-50 rounded p-4 space-y-3">
      <h2 className="text-xl font-semibold text-orange-700">Deletion pending</h2>
      <p>Your account is scheduled for permanent deletion. You have a 24-hour grace period to cancel.</p>
      <p className="text-sm text-gray-600">
        To cancel, click the link in the confirmation email we sent (or paste the cancel token below).
      </p>
      <CancelInputClient />
    </section>
  );
}

// Inline client component for the cancel input
function CancelInputClient() {
  return <DeleteFlowClient mode="cancel" />;
}
