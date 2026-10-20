// Parsed by the browser's own URL rules: `//host`, `/\host` and tab/newline
// tricks all resolve off-origin and are rejected as open redirects.
export function safeNextPath(raw: string | null | undefined): string | null {
  if (!raw?.startsWith("/")) return null;
  try {
    return new URL(raw, window.location.origin).origin === window.location.origin ? raw : null;
  } catch {
    return null;
  }
}
