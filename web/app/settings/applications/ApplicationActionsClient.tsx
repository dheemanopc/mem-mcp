/**
 * Client component for OAuth clients list + revoke button.
 */
"use client";

import { useState } from "react";
import { getCsrfToken } from "@/lib/csrf";

interface OAuthClient {
  id: string;
  client_name: string;
  software_id: string;
  review_status: string;
  disabled: boolean;
  created_at: string;
  last_used_at: string | null;
}

interface ApplicationActionsClientProps {
  clients: OAuthClient[];
}

export function ApplicationActionsClient({
  clients,
}: ApplicationActionsClientProps) {
  const [status, setStatus] = useState<"idle" | "revoking" | "error">("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [list, setList] = useState(clients);

  async function handleRevoke(clientId: string) {
    if (!window.confirm("Revoke this application? It will lose access to your account."))
      return;
    setStatus("revoking");
    setErrorMsg("");

    try {
      const csrfToken = getCsrfToken();

      const res = await fetch(`/api/web/clients/${clientId}`, {
        method: "DELETE",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "same-origin",
      });

      if (res.ok) {
        setList(
          list.map((c) =>
            c.id === clientId ? { ...c, disabled: true } : c
          )
        );
        setStatus("idle");
      } else {
        const body = await res.text();
        setStatus("error");
        setErrorMsg(`HTTP ${res.status}: ${body.slice(0, 200)}`);
        setTimeout(() => setStatus("idle"), 3000);
      }
    } catch (err) {
      setStatus("error");
      setErrorMsg(err instanceof Error ? err.message : "unknown error");
      setTimeout(() => setStatus("idle"), 3000);
    }
  }

  return (
    <section className="space-y-4">
      {list.length === 0 ? (
        <p className="text-gray-600">No connected applications.</p>
      ) : (
        <div className="overflow-x-auto border rounded">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">
                  Application
                </th>
                <th className="px-4 py-2 text-left font-semibold">Status</th>
                <th className="px-4 py-2 text-left font-semibold">Created</th>
                <th className="px-4 py-2 text-left font-semibold">Last used</th>
                <th className="px-4 py-2 text-left font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.map((client) => (
                <tr key={client.id} className="border-t">
                  <td className="px-4 py-2">
                    <div>
                      <div className="font-semibold">{client.client_name}</div>
                      <div className="text-xs text-gray-500">
                        {client.software_id}
                      </div>
                    </div>
                  </td>
                  <td className="px-4 py-2">
                    {client.disabled ? (
                      <span className="text-gray-500">Revoked</span>
                    ) : (
                      <span className="text-green-700 font-semibold">Active</span>
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-600">
                    {new Date(client.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-600">
                    {client.last_used_at
                      ? new Date(client.last_used_at).toLocaleDateString()
                      : "Never"}
                  </td>
                  <td className="px-4 py-2">
                    {!client.disabled && (
                      <button
                        onClick={() => handleRevoke(client.id)}
                        disabled={status !== "idle"}
                        className="text-red-600 hover:underline disabled:opacity-50 text-xs"
                      >
                        Revoke
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {status === "error" && (
        <p className="text-red-700 text-sm">Failed: {errorMsg}</p>
      )}
    </section>
  );
}
