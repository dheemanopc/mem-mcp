/**
 * Client component for identity list + unlink/promote actions + add another button.
 */
"use client";

import { useState } from "react";
import { getCsrfToken } from "@/lib/csrf";

interface Identity {
  id: string;
  email: string;
  provider: string;
  is_primary: boolean;
  linked_at: string;
  last_seen_at: string;
}

interface IdentityActionsClientProps {
  identities: Identity[];
}

export function IdentityActionsClient({
  identities,
}: IdentityActionsClientProps) {
  const [status, setStatus] = useState<
    "idle" | "unlinking" | "promoting" | "linking" | "error"
  >("idle");
  const [errorMsg, setErrorMsg] = useState("");
  const [list, setList] = useState(identities);

  async function handleUnlink(identityId: string) {
    if (!window.confirm("Unlink this identity?")) return;
    setStatus("unlinking");
    setErrorMsg("");

    try {
      const csrfToken = getCsrfToken();

      const res = await fetch(`/api/web/identities/${identityId}`, {
        method: "DELETE",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "same-origin",
      });

      if (res.ok) {
        setList(list.filter((i) => i.id !== identityId));
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

  async function handlePromote(identityId: string) {
    setStatus("promoting");
    setErrorMsg("");

    try {
      const csrfToken = document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrf_token="))
        ?.split("=")[1];

      const res = await fetch(`/api/web/identities/${identityId}/promote`, {
        method: "POST",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "same-origin",
      });

      if (res.ok) {
        setList(
          list.map((i) => ({
            ...i,
            is_primary: i.id === identityId,
          }))
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

  async function handleAddAnother() {
    setStatus("linking");
    setErrorMsg("");

    try {
      const csrfToken = document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrf_token="))
        ?.split("=")[1];

      const res = await fetch("/api/web/identities/link/start", {
        method: "POST",
        headers: csrfToken ? { "X-CSRF-Token": csrfToken } : {},
        credentials: "same-origin",
      });

      if (res.ok) {
        const data = await res.json();
        window.location.href = data.authorize_url;
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
        <p className="text-gray-600">No linked identities yet.</p>
      ) : (
        <div className="overflow-x-auto border rounded">
          <table className="w-full text-sm">
            <thead className="bg-gray-50">
              <tr>
                <th className="px-4 py-2 text-left font-semibold">Email</th>
                <th className="px-4 py-2 text-left font-semibold">Provider</th>
                <th className="px-4 py-2 text-left font-semibold">Primary</th>
                <th className="px-4 py-2 text-left font-semibold">Linked</th>
                <th className="px-4 py-2 text-left font-semibold">Actions</th>
              </tr>
            </thead>
            <tbody>
              {list.map((identity) => (
                <tr key={identity.id} className="border-t">
                  <td className="px-4 py-2 font-mono text-xs">{identity.email}</td>
                  <td className="px-4 py-2">{identity.provider}</td>
                  <td className="px-4 py-2">
                    {identity.is_primary ? (
                      <span className="text-green-700 font-semibold">Yes</span>
                    ) : (
                      "No"
                    )}
                  </td>
                  <td className="px-4 py-2 text-xs text-gray-600">
                    {new Date(identity.linked_at).toLocaleDateString()}
                  </td>
                  <td className="px-4 py-2 space-x-2">
                    {!identity.is_primary && (
                      <button
                        onClick={() => handlePromote(identity.id)}
                        disabled={status !== "idle"}
                        className="text-blue-600 hover:underline disabled:opacity-50 text-xs"
                      >
                        Promote
                      </button>
                    )}
                    {!identity.is_primary && (
                      <button
                        onClick={() => handleUnlink(identity.id)}
                        disabled={status !== "idle"}
                        className="text-red-600 hover:underline disabled:opacity-50 text-xs"
                      >
                        Unlink
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <button
        onClick={handleAddAnother}
        disabled={status !== "idle"}
        className="bg-blue-600 text-white rounded px-4 py-2 hover:bg-blue-700 disabled:opacity-50"
      >
        {status === "linking" ? "Redirecting…" : "Add another identity"}
      </button>

      {status === "error" && (
        <p className="text-red-700 text-sm">Failed: {errorMsg}</p>
      )}
    </section>
  );
}
