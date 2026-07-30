const HTTP_PROTOCOLS = new Set(["http:", "https:"]);

/**
 * Resolve a media path returned by our API without allowing the response to
 * make the browser contact an unrelated origin. Invalid input is rendered as
 * unavailable media instead of throwing during React render.
 */
export function resolveApiMediaUrl(value: string, apiBaseUrl: string, pageOrigin: string): string {
  try {
    const pageUrl = new URL(pageOrigin);
    const apiUrl = new URL(apiBaseUrl || "/", pageUrl);
    // Preserve the API client's base-path semantics (including a trailing
    // `/api/`) while still enforcing the API origin as the trust boundary.
    const resolved = new URL(value, apiUrl);
    if (!HTTP_PROTOCOLS.has(resolved.protocol) || resolved.origin !== apiUrl.origin) return "";
    return resolved.toString();
  } catch {
    return "";
  }
}
