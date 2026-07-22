const HTTP_PROTOCOLS = new Set(["http:", "https:"]);
const SAFE_INLINE_IMAGE = /^data:image\/(?:png|jpe?g|gif|webp);base64,[a-z0-9+/=\s]+$/i;

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

/** Allow raster data URLs and web URLs only; block executable/unknown schemes. */
export function resolveSafeImageUrl(value: string, pageOrigin: string): string {
  const candidate = value.trim();
  if (!candidate) return "";
  if (SAFE_INLINE_IMAGE.test(candidate)) return candidate;

  try {
    const pageUrl = new URL(pageOrigin);
    const resolved = new URL(candidate, pageUrl);
    if (resolved.protocol === "https:") return resolved.toString();
    // HTTP is useful for local deployments, but must never create mixed
    // content on an HTTPS page.
    if (resolved.protocol === "http:" && pageUrl.protocol === "http:") {
      return resolved.toString();
    }
    return "";
  } catch {
    return "";
  }
}
