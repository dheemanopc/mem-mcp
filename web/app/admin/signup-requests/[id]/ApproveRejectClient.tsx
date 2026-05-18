"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";

export function ApproveRejectClient({ id, email }: { id: string; email: string }) {
  const router = useRouter();
  const [mode, setMode] = useState<"idle" | "approving" | "rejecting">("idle");
  const [notes, setNotes] = useState("");
  const [reason, setReason] = useState("");
  const [sendEmail, setSendEmail] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function call(path: string, body: object) {
    setBusy(true);
    setErr(null);
    try {
      const res = await fetch(`/api/web/admin/signup-requests/${id}/${path}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      if (res.ok) {
        router.push("/admin/signup-requests");
        router.refresh();
      } else {
        const b = await res.json().catch(() => ({}));
        setErr(b.detail ?? "failed");
      }
    } catch {
      setErr("network error");
    } finally {
      setBusy(false);
    }
  }

  if (mode === "idle") {
    return (
      <div className="flex gap-3 pt-4 border-t">
        <button
          onClick={() => setMode("approving")}
          className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700"
        >
          Approve
        </button>
        <button
          onClick={() => setMode("rejecting")}
          className="px-4 py-2 border border-red-600 text-red-600 rounded hover:bg-red-50"
        >
          Reject
        </button>
      </div>
    );
  }

  if (mode === "approving") {
    return (
      <div className="pt-4 border-t space-y-3">
        <p className="text-sm text-gray-700">
          Approve <strong>{email}</strong> and send them the welcome email.
        </p>
        <textarea
          value={notes}
          onChange={(e) => setNotes(e.target.value)}
          placeholder="Internal notes (optional)"
          className="w-full border rounded px-3 py-2 h-20 text-sm"
        />
        {err && <p className="text-red-600 text-sm">{err}</p>}
        <div className="flex gap-2">
          <button
            disabled={busy}
            onClick={() => call("approve", { notes: notes.trim() || null })}
            className="px-4 py-2 bg-green-600 text-white rounded hover:bg-green-700 disabled:bg-gray-400"
          >
            {busy ? "Approving..." : "Confirm approval"}
          </button>
          <button
            disabled={busy}
            onClick={() => setMode("idle")}
            className="px-4 py-2 border rounded"
          >
            Cancel
          </button>
        </div>
      </div>
    );
  }

  // rejecting
  return (
    <div className="pt-4 border-t space-y-3">
      <p className="text-sm text-gray-700">
        Reject <strong>{email}</strong>.
      </p>
      <input
        type="text"
        required
        value={reason}
        onChange={(e) => setReason(e.target.value)}
        placeholder="Reason (required, stored internally)"
        className="w-full border rounded px-3 py-2 text-sm"
      />
      <textarea
        value={notes}
        onChange={(e) => setNotes(e.target.value)}
        placeholder="Notes for the rejection email (optional, only used if you check the box below)"
        className="w-full border rounded px-3 py-2 h-20 text-sm"
      />
      <label className="flex items-center gap-2 text-sm">
        <input
          type="checkbox"
          checked={sendEmail}
          onChange={(e) => setSendEmail(e.target.checked)}
        />
        Send rejection email to applicant
      </label>
      {err && <p className="text-red-600 text-sm">{err}</p>}
      <div className="flex gap-2">
        <button
          disabled={busy || !reason.trim()}
          onClick={() =>
            call("reject", {
              reason: reason.trim(),
              notes: notes.trim() || null,
              send_email: sendEmail,
            })
          }
          className="px-4 py-2 bg-red-600 text-white rounded hover:bg-red-700 disabled:bg-gray-400"
        >
          {busy ? "Rejecting..." : "Confirm rejection"}
        </button>
        <button
          disabled={busy}
          onClick={() => setMode("idle")}
          className="px-4 py-2 border rounded"
        >
          Cancel
        </button>
      </div>
    </div>
  );
}
