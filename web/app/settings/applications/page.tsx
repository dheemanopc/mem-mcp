/**
 * Applications settings page — list OAuth clients + revoke actions.
 * Server component that fetches /api/web/clients.
 */

import { ApplicationActionsClient } from "./ApplicationActionsClient";

async function fetchClients() {
  try {
    const res = await fetch("http://127.0.0.1:8080/api/web/clients", {
      cache: "no-store",
      credentials: "include",
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
}

export default async function ApplicationsPage() {
  const clients = await fetchClients();

  return (
    <main className="mx-auto max-w-3xl px-4 py-12 space-y-6">
      <h1 className="text-3xl font-bold">Connected applications</h1>
      <p className="text-sm text-gray-600">
        Applications that have access to your account. Revoke access anytime.
      </p>
      <ApplicationActionsClient clients={clients} />
    </main>
  );
}
