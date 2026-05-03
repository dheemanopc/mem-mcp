"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

function getCsrfToken(): string {
  return document.cookie.split("; ").find((c) => c.startsWith("csrf_token="))?.split("=")[1] ?? "";
}

export function MemoryActionsClient({ id, deleted }: { id: string; deleted: boolean }) {
  const router = useRouter();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function call(method: string, path: string, body?: object) {
    setBusy(true);
    setError("");
    try {
      const res = await fetch(path, {
        method,
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": getCsrfToken(),
        },
        credentials: "same-origin",
        body: body ? JSON.stringify(body) : undefined,
      });
      if (!res.ok) {
        const text = await res.text();
        setError(`HTTP ${res.status}: ${text.slice(0, 200)}`);
      } else {
        router.refresh();
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : "unknown");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex gap-3 items-center">
      {!deleted ? (
        <button
          onClick={() => { if (confirm("Delete this memory?")) call("DELETE", `/api/web/memories/${id}`); }}
          disabled={busy}
          className="bg-red-600 text-white rounded px-3 py-1.5 disabled:opacity-50"
        >
          Delete
        </button>
      ) : (
        <button
          onClick={() => call("POST", `/api/web/memories/${id}/undelete`)}
          disabled={busy}
          className="bg-green-600 text-white rounded px-3 py-1.5 disabled:opacity-50"
        >
          Undelete
        </button>
      )}
      {error && <span className="text-red-600 text-sm">{error}</span>}
    </div>
  );
}
