"use client";

import { useState, Suspense } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";

const CLIENTS = [
  { value: "claude_code", label: "Claude Code (CLI)" },
  { value: "claude_chat", label: "Claude.ai (chat)" },
  { value: "chatgpt", label: "ChatGPT (Developer Mode)" },
  { value: "cursor", label: "Cursor" },
  { value: "other", label: "Other / custom MCP client" },
];

function RequestAccessContent() {
  const searchParams = useSearchParams();
  const justVerified = searchParams.get("verified") === "1";

  const [email, setEmail] = useState("");
  const [name, setName] = useState("");
  const [reason, setReason] = useState("");
  const [clientIntent, setClientIntent] = useState<string[]>([]);
  const [website, setWebsite] = useState(""); // honeypot
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function toggle(value: string) {
    setClientIntent((prev) =>
      prev.includes(value) ? prev.filter((v) => v !== value) : [...prev, value]
    );
  }

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    try {
      const res = await fetch("/api/public/signup-requests", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          email: email.trim().toLowerCase(),
          name: name.trim() || null,
          reason: reason.trim() || null,
          client_intent: clientIntent,
          website,
        }),
      });
      if (res.status === 202) {
        setSubmitted(true);
      } else if (res.status === 429) {
        setError("Too many submissions. Please try again later.");
      } else {
        const body = await res.json().catch(() => ({}));
        setError(body.detail ?? "Could not submit. Please try again.");
      }
    } catch {
      setError("Network error. Please try again.");
    } finally {
      setSubmitting(false);
    }
  }

  if (submitted) {
    return (
      <main className="mx-auto max-w-xl px-4 py-12 prose">
        <h1>Check your email</h1>
        <p>
          We sent a verification link to <strong>{email}</strong>. Click the
          link to put your request in the review queue. The link expires in 24
          hours.
        </p>
        <p>
          Didn&apos;t get the email? Check your spam folder, or wait a few
          minutes and{" "}
          <button
            onClick={() => setSubmitted(false)}
            className="text-blue-600 hover:underline"
          >
            try again
          </button>
          .
        </p>
      </main>
    );
  }

  return (
    <main className="mx-auto max-w-xl px-4 py-12">
      <h1 className="text-3xl font-bold mb-2">Request access</h1>
      <p className="text-gray-600 mb-6">
        mem-mcp is in closed beta. Tell us a little about how you&apos;d use it
        and we&apos;ll get back to you.
      </p>

      {justVerified && (
        <div className="mb-6 p-4 bg-green-50 border border-green-200 rounded text-green-800">
          Email verified. Your request is now in the queue — we&apos;ll be in
          touch soon.
        </div>
      )}

      <form onSubmit={onSubmit} className="space-y-4">
        <div>
          <label className="block text-sm font-medium mb-1">
            Email <span className="text-red-500">*</span>
          </label>
          <input
            type="email"
            required
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full border rounded px-3 py-2"
            placeholder="you@example.com"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">Name</label>
          <input
            type="text"
            value={name}
            onChange={(e) => setName(e.target.value)}
            className="w-full border rounded px-3 py-2"
            placeholder="Optional"
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-1">
            Why do you want access?
          </label>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            className="w-full border rounded px-3 py-2 h-24"
            placeholder="Optional — a sentence or two helps us prioritise."
            maxLength={1000}
          />
        </div>

        <div>
          <label className="block text-sm font-medium mb-2">
            Which clients do you plan to connect?
          </label>
          <div className="space-y-1">
            {CLIENTS.map((c) => (
              <label key={c.value} className="flex items-center gap-2 text-sm">
                <input
                  type="checkbox"
                  checked={clientIntent.includes(c.value)}
                  onChange={() => toggle(c.value)}
                />
                {c.label}
              </label>
            ))}
          </div>
        </div>

        {/* Honeypot field — hidden via CSS; bots will fill it, humans won't see it */}
        <div className="hidden" aria-hidden="true">
          <label>
            Website
            <input
              type="text"
              tabIndex={-1}
              autoComplete="off"
              value={website}
              onChange={(e) => setWebsite(e.target.value)}
            />
          </label>
        </div>

        {error && (
          <div className="p-3 bg-red-50 border border-red-200 rounded text-red-700 text-sm">
            {error}
          </div>
        )}

        <button
          type="submit"
          disabled={submitting || !email}
          className="px-6 py-2 bg-blue-600 text-white rounded hover:bg-blue-700 disabled:bg-gray-400 transition"
        >
          {submitting ? "Submitting..." : "Submit request"}
        </button>

        <p className="text-sm text-gray-500">
          Already approved?{" "}
          <Link href="/auth/login" className="text-blue-600 hover:underline">
            Sign in
          </Link>
          .
        </p>
      </form>
    </main>
  );
}

export default function RequestAccessPage() {
  return (
    <Suspense>
      <RequestAccessContent />
    </Suspense>
  );
}
