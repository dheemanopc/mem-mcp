"use client";

import { useState, useEffect } from "react";

interface ProvisionableUser {
  email: string;
  sub: string;
  workspace_domain: string | null;
}

interface DanglingUsersResponse {
  provisionable: ProvisionableUser[];
  dangling_uninvited: string[];
  orphan_identities: string[];
  skipped_existing: number;
}

interface ReconcileResult {
  provisioned: string[];
  provisioned_count: number;
  skipped_existing: number;
  dangling_uninvited: string[];
  orphan_identities: string[];
}

export default function AdminDanglingUsersPage() {
  const [data, setData] = useState<DanglingUsersResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [reconciling, setReconciling] = useState(false);
  const [lastResult, setLastResult] = useState<ReconcileResult | null>(null);

  useEffect(() => {
    fetchData();
  }, []);

  const fetchData = async () => {
    setLoading(true);
    try {
      const res = await fetch("/api/web/admin/dangling-users");
      if (!res.ok) throw new Error(`Failed to fetch: ${res.status}`);
      const json = (await res.json()) as DanglingUsersResponse;
      setData(json);
    } catch (err) {
      console.error("Failed to fetch dangling users:", err);
    } finally {
      setLoading(false);
    }
  };

  const handleReconcile = async () => {
    if (!confirm("Reconcile now? This will provision missing users and auto-consume matching team invites.")) {
      return;
    }
    setReconciling(true);
    try {
      const res = await fetch("/api/web/admin/dangling-users/reconcile", {
        method: "POST",
      });
      if (!res.ok) throw new Error(`Failed to reconcile: ${res.status}`);
      const result = (await res.json()) as ReconcileResult;
      setLastResult(result);
      // Refresh list
      await fetchData();
    } catch (err) {
      console.error("Reconciliation failed:", err);
      alert("Reconciliation failed. Check console.");
    } finally {
      setReconciling(false);
    }
  };

  if (loading) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-12">
        <h1 className="text-3xl font-bold mb-6">Dangling users</h1>
        <p className="text-gray-500">Loading...</p>
      </main>
    );
  }

  if (!data) {
    return (
      <main className="mx-auto max-w-5xl px-4 py-12">
        <h1 className="text-3xl font-bold mb-6">Dangling users</h1>
        <p className="text-red-600">Failed to load data.</p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-5xl px-4 py-12">
      <div className="flex items-center justify-between mb-6">
        <h1 className="text-3xl font-bold">Dangling users</h1>
        <button
          onClick={handleReconcile}
          disabled={reconciling}
          className="px-4 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-gray-400"
        >
          {reconciling ? "Reconciling..." : "Reconcile now"}
        </button>
      </div>

      {lastResult && (
        <div className="mb-6 p-4 bg-green-50 border border-green-200 rounded">
          <h3 className="font-semibold text-green-800">Last reconciliation</h3>
          <ul className="text-sm text-green-700 mt-2">
            <li>Provisioned: {lastResult.provisioned_count}</li>
            <li>Skipped (already existed): {lastResult.skipped_existing}</li>
          </ul>
        </div>
      )}

      {/* Provisionable users */}
      <section className="mb-8">
        <h2 className="text-xl font-bold mb-3">
          Provisionable ({data.provisionable.length})
        </h2>
        {data.provisionable.length === 0 ? (
          <p className="text-gray-500">None.</p>
        ) : (
          <div className="overflow-x-auto border rounded">
            <table className="w-full text-sm">
              <thead className="bg-gray-100 border-b">
                <tr>
                  <th className="px-4 py-2 text-left">Email</th>
                  <th className="px-4 py-2 text-left">Sub</th>
                  <th className="px-4 py-2 text-left">Workspace domain</th>
                </tr>
              </thead>
              <tbody>
                {data.provisionable.map((user) => (
                  <tr key={user.sub} className="border-b hover:bg-gray-50">
                    <td className="px-4 py-2 font-mono text-xs">{user.email}</td>
                    <td className="px-4 py-2 font-mono text-xs break-all">{user.sub}</td>
                    <td className="px-4 py-2">{user.workspace_domain || "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </section>

      {/* Dangling uninvited */}
      <section className="mb-8">
        <h2 className="text-xl font-bold mb-3">
          Dangling (in Cognito, not invited) ({data.dangling_uninvited.length})
        </h2>
        {data.dangling_uninvited.length === 0 ? (
          <p className="text-gray-500">None.</p>
        ) : (
          <ul className="space-y-1">
            {data.dangling_uninvited.map((email) => (
              <li key={email} className="text-sm font-mono px-4 py-2 bg-yellow-50 border border-yellow-100 rounded">
                {email}
              </li>
            ))}
          </ul>
        )}
      </section>

      {/* Orphan identities */}
      <section>
        <h2 className="text-xl font-bold mb-3">
          Orphans (in DB, not in Cognito) ({data.orphan_identities.length})
        </h2>
        {data.orphan_identities.length === 0 ? (
          <p className="text-gray-500">None.</p>
        ) : (
          <ul className="space-y-1">
            {data.orphan_identities.map((email) => (
              <li key={email} className="text-sm font-mono px-4 py-2 bg-red-50 border border-red-100 rounded">
                {email}
              </li>
            ))}
          </ul>
        )}
      </section>
    </main>
  );
}
