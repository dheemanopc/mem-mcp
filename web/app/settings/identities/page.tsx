/**
 * Identities settings page — list + link/unlink/promote actions.
 * Server component that fetches /api/web/identities.
 */

import { serverApiFetch } from "@/lib/server-api";
import { IdentityActionsClient } from "./IdentityActionsClient";

async function fetchIdentities() {
  return (await serverApiFetch<any[]>("/api/web/identities", {
    redirectTo: "/settings/identities",
  })) ?? [];
}

export default async function IdentitiesPage() {
  const identities = await fetchIdentities();

  return (
    <main className="mx-auto max-w-3xl px-4 py-12 space-y-6">
      <h1 className="text-3xl font-bold">Linked identities</h1>
      <p className="text-sm text-gray-600">
        Sign in via any of these identities. Promote one as your primary.
      </p>
      <IdentityActionsClient identities={identities} />
    </main>
  );
}
