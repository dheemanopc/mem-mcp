/**
 * Settings page — main profile, retention, danger zone (data deletion CTA).
 * Server component that fetches /api/web/me and renders profile info.
 */

import { TenantSettingsClient } from "./TenantSettingsClient";

async function fetchMe() {
  try {
    const res = await fetch("http://127.0.0.1:8080/api/web/me", {
      cache: "no-store",
      credentials: "include",
    });
    if (!res.ok) return null;
    return res.json();
  } catch {
    return null;
  }
}

export default async function SettingsPage() {
  const me = await fetchMe();

  if (!me) {
    return (
      <main className="mx-auto max-w-3xl px-4 py-12">
        <h1 className="text-3xl font-bold">Settings</h1>
        <p className="text-red-600 mt-4">Not signed in.</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-3xl px-4 py-12 space-y-8">
      <h1 className="text-3xl font-bold">Settings</h1>

      <section className="space-y-2">
        <h2 className="text-xl font-semibold">Profile</h2>
        <dl className="grid grid-cols-3 gap-2">
          <dt className="text-gray-500">Email</dt>
          <dd className="col-span-2 font-mono">{me.tenant.email}</dd>
          <dt className="text-gray-500">Tier</dt>
          <dd className="col-span-2">{me.tenant.tier}</dd>
          <dt className="text-gray-500">Status</dt>
          <dd className="col-span-2">{me.tenant.status}</dd>
        </dl>
      </section>

      <TenantSettingsClient initialRetentionDays={me.tenant.retention_days} />

      <section className="border border-red-300 rounded p-4 space-y-2">
        <h2 className="text-xl font-semibold text-red-700">Danger zone</h2>
        <p className="text-sm text-gray-600">
          Delete all your data permanently. 24h grace period to cancel.
        </p>
        <a
          href="/settings/delete"
          className="inline-block bg-red-600 text-white rounded px-3 py-1.5 hover:bg-red-700"
        >
          Request deletion
        </a>
      </section>
    </main>
  );
}
