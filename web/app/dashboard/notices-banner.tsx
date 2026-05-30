"use client";

/**
 * NoticesBanner — polls /api/web/notices (at-most-once queue items) and
 * /api/web/pending-state (sticky surface from plugins like reminders) and
 * renders both as small inline banners at the top of the dashboard.
 *
 * Notices drain on read (server marks delivered); sticky items re-appear
 * on every poll until the user acts on them (e.g. dismisses a reminder
 * via chat). A future iteration can add a dismiss button that calls the
 * reminders MCP tool directly.
 */

import { useEffect, useState } from "react";

const POLL_MS = 30_000;

interface Notice {
  id: string;
  plugin_id: string;
  kind: string;
  text: string;
}

export function NoticesBanner() {
  const [notices, setNotices] = useState<Notice[]>([]);
  const [pending, setPending] = useState<string[]>([]);

  useEffect(() => {
    let cancelled = false;

    async function poll() {
      try {
        const [nRes, pRes] = await Promise.all([
          fetch("/api/web/notices", { credentials: "include" }),
          fetch("/api/web/pending-state", { credentials: "include" }),
        ]);
        if (cancelled) return;
        if (nRes.ok) {
          const data = await nRes.json();
          // Notices drain on each call — accumulate so the user doesn't
          // miss any between renders.
          setNotices((prev) => [...prev, ...(data.notices ?? [])]);
        }
        if (pRes.ok) {
          const data = await pRes.json();
          // Pending state is fresh each call — replace.
          setPending(data.items ?? []);
        }
      } catch {
        // Network blips are silent; next tick will retry.
      }
    }

    void poll();
    const id = setInterval(poll, POLL_MS);
    return () => {
      cancelled = true;
      clearInterval(id);
    };
  }, []);

  const dismissNotice = (id: string) => {
    setNotices((prev) => prev.filter((n) => n.id !== id));
  };

  if (notices.length === 0 && pending.length === 0) return null;

  return (
    <div className="space-y-2">
      {pending.map((text, i) => (
        <div
          key={`pending-${i}`}
          className="border border-amber-300 bg-amber-50 text-amber-900 rounded-md px-4 py-3 text-sm"
        >
          {text}
        </div>
      ))}
      {notices.map((n) => (
        <div
          key={n.id}
          className="border border-blue-300 bg-blue-50 text-blue-900 rounded-md px-4 py-3 text-sm flex items-start gap-3"
        >
          <span className="flex-1">{n.text}</span>
          <button
            type="button"
            onClick={() => dismissNotice(n.id)}
            className="text-blue-600 hover:text-blue-900 font-medium text-xs"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      ))}
    </div>
  );
}
