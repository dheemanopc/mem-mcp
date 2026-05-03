/**
 * Identities settings page — list + link/unlink/promote actions.
 * Server component that fetches /api/web/identities.
 */

import { IdentityActionsClient } from "./IdentityActionsClient";

async function fetchIdentities() {
  try {
    const res = await fetch("http://127.0.0.1:8080/api/web/identities", {
      cache: "no-store",
      credentials: "include",
    });
    if (!res.ok) return [];
    return res.json();
  } catch {
    return [];
  }
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
