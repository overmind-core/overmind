// Parsed by the browser's own URL rules: `//host`, `/\host` and tab/newline
// tricks all resolve off-origin and are rejected as open redirects.
export function safeNextPath(raw: string | null | undefined): string | null {
  if (!raw?.startsWith("/")) return null;
  try {
    const url = new URL(raw, window.location.origin);
    if (
      url.origin !== window.location.origin ||
      decodeURIComponent(url.pathname).replace(/\/+$/, "") === "/login"
    )
      return null;
    return url.pathname + url.search + url.hash;
  } catch {
    return null;
  }
}
