"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

function getCsrfToken(): string {
  return document.cookie.split("; ").find((c) => c.startsWith("csrf_token="))?.split("=")[1] ?? "";
}

interface Props {
  mode?: "confirm" | "cancel";
}

export function DeleteFlowClient({ mode = "confirm" }: Props) {
  const router = useRouter();
  const [step, setStep] = useState<"intro" | "confirm" | "submitting" | "done">("intro");
  const [cancelToken, setCancelToken] = useState("");
  const [error, setError] = useState("");

  async function submitDelete() {
    setStep("submitting");
    setError("");
    try {
      const res = await fetch("/api/web/data/delete", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": getCsrfToken(),
        },
        credentials: "same-origin",
      });
      if (!res.ok) {
        setError(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
        setStep("intro");
        return;
      }
      setStep("done");
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "unknown");
      setStep("intro");
    }
  }

  async function submitCancel() {
    setStep("submitting");
    setError("");
    try {
      const res = await fetch("/api/web/data/delete/cancel", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": getCsrfToken(),
        },
        credentials: "same-origin",
        body: JSON.stringify({ cancel_token: cancelToken }),
      });
      if (!res.ok) {
        setError(`HTTP ${res.status}: ${(await res.text()).slice(0, 200)}`);
        setStep("intro");
        return;
      }
      router.refresh();
    } catch (e) {
      setError(e instanceof Error ? e.message : "unknown");
      setStep("intro");
    }
  }

  if (mode === "cancel") {
    return (
      <div className="space-y-2 mt-2">
        <input
          type="text"
          placeholder="paste cancel token from email"
          value={cancelToken}
          onChange={(e) => setCancelToken(e.target.value)}
          className="w-full border rounded px-3 py-1.5 font-mono text-sm"
          disabled={step === "submitting"}
        />
        <button
          onClick={submitCancel}
          disabled={!cancelToken || step === "submitting"}
          className="bg-orange-600 text-white rounded px-3 py-1.5 disabled:opacity-50"
        >
          {step === "submitting" ? "Cancelling…" : "Cancel deletion"}
        </button>
        {error && <p className="text-sm text-red-700">{error}</p>}
      </div>
    );
  }

  // confirm mode
  if (step === "done") {
    return (
      <section className="border border-red-300 rounded p-4 bg-red-50">
        <h2 className="text-xl font-semibold text-red-700">Deletion requested</h2>
        <p className="mt-2">
          We've started the 24-hour grace period. Check your email for the cancel link.
        </p>
      </section>
    );
  }
  if (step === "intro") {
    return (
      <section className="space-y-4">
        <p className="text-gray-700">
          This will permanently delete all your memories, audit log, identities, and connected
          applications. After a 24-hour grace period, deletion is irreversible.
        </p>
        <p className="text-gray-700">
          We strongly recommend you{" "}
          <a href="/data/export" className="text-blue-600 underline">export your data</a>{" "}
          first.
        </p>
        <button
          onClick={() => setStep("confirm")}
          className="bg-red-600 text-white rounded px-4 py-2"
        >
          Continue to delete
        </button>
        {error && <p className="text-sm text-red-700">{error}</p>}
      </section>
    );
  }
  if (step === "confirm") {
    return (
      <section className="space-y-4 border border-red-400 bg-red-50 rounded p-4">
        <p className="font-semibold">Are you sure?</p>
        <p className="text-sm text-gray-600">
          You'll have 24 hours to cancel via the email link.
        </p>
        <div className="flex gap-3">
          <button
            onClick={submitDelete}
            disabled={step !== "confirm"}
            className="bg-red-600 text-white rounded px-4 py-2"
          >
            Yes, delete my account
          </button>
          <button
            onClick={() => setStep("intro")}
            className="border rounded px-4 py-2"
          >
            Cancel
          </button>
        </div>
        {error && <p className="text-sm text-red-700">{error}</p>}
      </section>
    );
  }
  return null;
}
