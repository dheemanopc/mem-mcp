/**
 * Reads the csrf_token cookie set by the CSRF middleware (double-submit
 * pattern, see src/mem_mcp/web/csrf.py). Returns "" if absent — caller
 * should handle that as an unauthenticated/uninitialised state.
 */
export function getCsrfToken(): string {
  if (typeof document === "undefined") return "";
  return (
    document.cookie
      .split("; ")
      .find((c) => c.startsWith("csrf_token="))
      ?.split("=")[1] ?? ""
  );
}

/**
 * Convenience: returns headers object including the CSRF header.
 * Merge with any other headers you need.
 */
export function csrfHeaders(
  extra: Record<string, string> = {}
): Record<string, string> {
  const token = getCsrfToken();
  return token ? { ...extra, "X-CSRF-Token": token } : extra;
}
