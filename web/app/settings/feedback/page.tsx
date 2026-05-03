/**
 * Settings > Feedback page — client component with textarea + submit handler.
 * Posts to /api/web/feedback with CSRF protection.
 */
"use client";

import { useState } from "react";

export default function FeedbackPage() {
  const [text, setText] = useState("");
  const [status, setStatus] = useState<"idle" | "submitting" | "success" | "error">(
    "idle"
  );
  const [errorMsg, setErrorMsg] = useState("");

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (!text.trim()) return;

    setStatus("submitting");
    setErrorMsg("");

    try {
      // Extract CSRF token from cookie
      const csrfToken = document.cookie
        .split("; ")
        .find((c) => c.startsWith("csrf_token="))
        ?.split("=")[1];

      const res = await fetch("/api/web/feedback", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          ...(csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
        },
        body: JSON.stringify({ text, metadata: {} }),
        credentials: "same-origin",
      });

      if (res.ok) {
        setStatus("success");
        setText("");
      } else {
        const body = await res.text();
        setStatus("error");
        setErrorMsg(`HTTP ${res.status}: ${body.slice(0, 200)}`);
      }
    } catch (err) {
      setStatus("error");
      setErrorMsg(
        err instanceof Error ? err.message : "unknown error"
      );
    }
  }

  return (
    <main className="mx-auto max-w-2xl px-4 py-12 space-y-6">
      <h1 className="text-3xl font-bold">Send feedback</h1>
      <p className="text-gray-600">
        Bug reports, feature requests, anything. The operator triages weekly.
      </p>

      <form onSubmit={submit} className="space-y-4">
        <textarea
          className="w-full border rounded p-3 min-h-[160px]"
          placeholder="What's on your mind?"
          maxLength={4096}
          value={text}
          onChange={(e) => setText(e.target.value)}
          disabled={status === "submitting"}
        />

        <div className="flex justify-between items-center">
          <span className="text-sm text-gray-500">
            {text.length} / 4096
          </span>
          <button
            type="submit"
            disabled={status === "submitting" || !text.trim()}
            className="bg-blue-600 text-white rounded px-4 py-2 disabled:opacity-50"
          >
            {status === "submitting" ? "Sending…" : "Send"}
          </button>
        </div>

        {status === "success" && (
          <p className="text-green-700">
            Thanks — feedback received.
          </p>
        )}
        {status === "error" && (
          <p className="text-red-700">Failed: {errorMsg}</p>
        )}
      </form>
    </main>
  );
}
