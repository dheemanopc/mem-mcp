import { serverApiFetch } from "@/lib/server-api";
import { KiteConfirmButtons } from "./confirm-buttons";

export const dynamic = "force-dynamic";

type IntentSummary = {
  intent_id: string;
  api_key_last4: string;
  user_id: string | null;
  has_auto_login: boolean;
  orders_enabled: boolean;
  expires_at: string;
  created_by_client_id: string;
};

export default async function KiteConfirmPage({
  searchParams,
}: {
  searchParams: Promise<{ intent?: string; return_to?: string }>;
}) {
  const { intent, return_to } = await searchParams;
  if (!intent) {
    return (
      <main className="mx-auto max-w-xl p-8">
        <h1 className="text-xl font-semibold">Missing setup link</h1>
        <p className="mt-2 text-gray-600">Ask the partner app to resend a valid setup link.</p>
      </main>
    );
  }

  const summary = await serverApiFetch<IntentSummary>(
    `/api/web/skills/kite/intent/${encodeURIComponent(intent)}`,
    { redirectTo: `/skills/kite/confirm?intent=${encodeURIComponent(intent)}${return_to ? `&return_to=${encodeURIComponent(return_to)}` : ""}` }
  );

  if (!summary) {
    return (
      <main className="mx-auto max-w-xl p-8">
        <h1 className="text-xl font-semibold">Link expired or already used</h1>
        <p className="mt-2 text-gray-600">Ask the partner app to send a fresh setup link.</p>
      </main>
    );
  }

  // Inline server component content + client component for buttons
  return (
    <main className="mx-auto max-w-xl p-8 space-y-6">
      <h1 className="text-2xl font-semibold">Connect Kite to memsys</h1>

      <div className="rounded border border-yellow-300 bg-yellow-50 p-4 text-sm">
        <strong>Heads up:</strong> Confirming will let <code>{summary.created_by_client_id}</code> access your
        Zerodha account through memsys.{" "}
        {summary.orders_enabled
          ? "Trade placement is ENABLED — the partner can place orders on your behalf."
          : "Trade placement is disabled (read-only access only)."}
      </div>

      <dl className="grid grid-cols-2 gap-y-2 text-sm">
        <dt className="text-gray-500">Zerodha user_id</dt>
        <dd>{summary.user_id ?? "—"}</dd>
        <dt className="text-gray-500">API key</dt>
        <dd>{summary.api_key_last4}</dd>
        <dt className="text-gray-500">Auto-login</dt>
        <dd>{summary.has_auto_login ? "Enabled" : "Manual access_token mode"}</dd>
        <dt className="text-gray-500">Orders enabled</dt>
        <dd>{summary.orders_enabled ? "Yes" : "No"}</dd>
        <dt className="text-gray-500">Expires</dt>
        <dd>{new Date(summary.expires_at).toLocaleString()}</dd>
      </dl>

      <KiteConfirmButtons intentId={summary.intent_id} returnTo={return_to} />
    </main>
  );
}
