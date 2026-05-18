/**
 * Applications settings page — list OAuth clients + revoke actions.
 * Server component that fetches /api/web/clients.
 */

import { serverApiFetch } from "@/lib/server-api";
import { ApplicationActionsClient } from "./ApplicationActionsClient";

async function fetchClients() {
  return (await serverApiFetch<any[]>("/api/web/clients", {
    redirectTo: "/settings/applications",
  })) ?? [];
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
