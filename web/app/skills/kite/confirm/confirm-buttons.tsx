"use client";
import { useState } from "react";

export function KiteConfirmButtons({ intentId, returnTo }: { intentId: string; returnTo?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function call(action: "confirm" | "cancel") {
    setBusy(true);
    setError(null);
    try {
      const res = await fetch(`/api/web/skills/kite/intent/${encodeURIComponent(intentId)}/${action}`, {
        method: "POST",
        credentials: "include",
      });
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? body.error ?? `Request failed (${res.status})`);
        setBusy(false);
        return;
      }
      const status = action === "confirm" ? "success" : "cancelled";
      if (returnTo) {
        window.location.href = `${returnTo}${returnTo.includes("?") ? "&" : "?"}status=${status}`;
      } else {
        window.location.href = `/dashboard?kite_setup=${status}`;
      }
    } catch (e) {
      setError(String(e));
      setBusy(false);
    }
  }

  return (
    <div className="flex gap-3">
      <button
        onClick={() => call("confirm")}
        disabled={busy}
        className="rounded bg-blue-600 px-4 py-2 text-white disabled:opacity-50"
      >
        Confirm
      </button>
      <button
        onClick={() => call("cancel")}
        disabled={busy}
        className="rounded border px-4 py-2 disabled:opacity-50"
      >
        Cancel
      </button>
      {error && <p className="text-sm text-red-600">{error}</p>}
    </div>
  );
}
