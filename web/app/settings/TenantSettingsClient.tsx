/**
 * Client component for retention days input + save button.
 */
"use client";

import { useState } from "react";
import { csrfHeaders } from "@/lib/csrf";

interface TenantSettingsClientProps {
  initialRetentionDays: number;
}

export function TenantSettingsClient({
  initialRetentionDays,
}: TenantSettingsClientProps) {
  const [retentionDays, setRetentionDays] = useState(initialRetentionDays);
  const [status, setStatus] = useState<"idle" | "saving" | "success" | "error">(
    "idle"
  );
  const [errorMsg, setErrorMsg] = useState("");

  async function save() {
    setStatus("saving");
    setErrorMsg("");

    try {
      const res = await fetch("/api/web/tenant", {
        method: "PATCH",
        headers: csrfHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({ retention_days: retentionDays }),
        credentials: "same-origin",
      });

      if (res.ok) {
        setStatus("success");
        setTimeout(() => setStatus("idle"), 3000);
      } else {
        const body = await res.text();
        setStatus("error");
        setErrorMsg(`HTTP ${res.status}: ${body.slice(0, 200)}`);
      }
    } catch (err) {
      setStatus("error");
      setErrorMsg(err instanceof Error ? err.message : "unknown error");
    }
  }

  const isDirty = retentionDays !== initialRetentionDays;

  return (
    <section className="border border-gray-300 rounded p-4 space-y-4">
      <h2 className="text-xl font-semibold">Data retention</h2>
      <p className="text-sm text-gray-600">
        Memories older than this many days will be automatically deleted.
      </p>

      <div className="space-y-2">
        <label htmlFor="retention" className="block text-sm font-medium">
          Retention days: {retentionDays}
        </label>
        <input
          id="retention"
          type="range"
          min="7"
          max="3650"
          value={retentionDays}
          onChange={(e) => setRetentionDays(parseInt(e.target.value, 10))}
          disabled={status === "saving"}
          className="w-full"
        />
        <p className="text-xs text-gray-500">
          {retentionDays <= 30
            ? "Short retention (aggressive cleanup)"
            : retentionDays <= 365
              ? "Standard retention"
              : "Long retention"}
        </p>
      </div>

      <div className="flex gap-2">
        <button
          onClick={save}
          disabled={!isDirty || status === "saving"}
          className="bg-blue-600 text-white rounded px-4 py-2 disabled:opacity-50 hover:bg-blue-700"
        >
          {status === "saving" ? "Saving…" : "Save"}
        </button>
      </div>

      {status === "success" && (
        <p className="text-green-700 text-sm">Settings saved.</p>
      )}
      {status === "error" && (
        <p className="text-red-700 text-sm">Failed: {errorMsg}</p>
      )}
    </section>
  );
}
