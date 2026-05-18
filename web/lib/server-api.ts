import { cookies } from "next/headers";
import { redirect } from "next/navigation";

const API_BASE = process.env.MEM_MCP_API_URL ?? "http://127.0.0.1:8080";

export interface ServerApiOptions extends Omit<RequestInit, "headers"> {
  /** Path to redirect to after login (sent as ?next=…). If unset, no redirect on 401 — caller gets null. */
  redirectTo?: string;
  /** Additional headers to merge in (Cookie header is forwarded automatically). */
  headers?: Record<string, string>;
}

/**
 * Server-side fetch helper that forwards the incoming request's cookies
 * to the FastAPI backend. On 401 with `redirectTo` set, triggers a Next.js
 * server redirect to /auth/login?next=<redirectTo>.
 *
 * Returns parsed JSON on 2xx, or null on other non-2xx errors.
 */
export async function serverApiFetch<T = unknown>(
  path: string,
  options: ServerApiOptions = {},
): Promise<T | null> {
  const { redirectTo, headers: extraHeaders, ...init } = options;

  const cookieStore = await cookies();
  const cookieHeader = cookieStore
    .getAll()
    .map((c) => `${c.name}=${c.value}`)
    .join("; ");

  const res = await fetch(`${API_BASE}${path}`, {
    cache: "no-store",
    ...init,
    headers: {
      ...extraHeaders,
      ...(cookieHeader ? { Cookie: cookieHeader } : {}),
    },
  });

  if (res.status === 401 && redirectTo) {
    redirect(`/auth/login?next=${encodeURIComponent(redirectTo)}`);
  }

  if (!res.ok) return null;
  return (await res.json()) as T;
}
