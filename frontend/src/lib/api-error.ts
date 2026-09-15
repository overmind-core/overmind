/**
 * Thrown by the `client.ts` middleware for non-2xx responses, in place of the
 * generated client's `ResponseError` whose message is always the literal
 * "Response returned an error code".
 */
export class ApiError extends Error {
  readonly status: number;
  readonly hasDetail: boolean;
  readonly code: string | null;

  constructor(status: number, message: string, hasDetail: boolean, code: string | null = null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.hasDetail = hasDetail;
    this.code = code;
  }
}

export const NETWORK_ERROR_MESSAGE =
  "Could not reach the server. Check your connection and try again.";

export function statusFallbackMessage(status: number): string {
  if (status === 401) return "Your session has expired. Sign in again.";
  if (status === 403) return "You don't have access to this.";
  if (status === 404) return "Not found. It may have been deleted.";
  if (status === 409) return "This conflicts with an existing record.";
  if (status === 429) return "Too many requests. Try again in a moment.";
  if (status >= 500) return "The server hit an unexpected error. Try again.";
  return `Request failed (HTTP ${status}).`;
}

/**
 * Body shapes the backend produces: `{"detail": …}` (possibly nested),
 * `{"error": …}`, OpenAI-style `{"error": {"message"}}`, bare arrays, and DRF
 * field errors `{"name": ["…"]}`.
 */
export function extractErrorDetail(body: string): string | null {
  if (!body) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null;
  }
  return detailFrom(parsed, 0);
}

/** Machine-readable `code` some endpoints send alongside `detail`. */
export function extractErrorCode(body: string): string | null {
  if (!body) return null;
  let parsed: unknown;
  try {
    parsed = JSON.parse(body);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return null;
  const code = (parsed as Record<string, unknown>).code;
  if (typeof code === "string") return code;
  if (Array.isArray(code) && typeof code[0] === "string") return code[0];
  return null;
}

function detailFrom(value: unknown, depth: number): string | null {
  if (depth > 4) return null;
  if (typeof value === "string") return value.trim() || null;
  if (Array.isArray(value)) {
    const parts = value.map((v) => detailFrom(v, depth + 1)).filter(Boolean);
    return parts.length ? parts.join(" ") : null;
  }
  if (!value || typeof value !== "object") return null;
  const obj = value as Record<string, unknown>;
  for (const key of ["detail", "message", "error"]) {
    if (key in obj) {
      const nested = detailFrom(obj[key], depth + 1);
      if (nested) return nested;
    }
  }
  // DRF field errors: {"name": ["This field is required."]}
  const fieldParts: string[] = [];
  for (const [key, v] of Object.entries(obj)) {
    if (key === "code" || key === "type") continue;
    const nested = detailFrom(v, depth + 1);
    if (nested) fieldParts.push(`${key}: ${nested}`);
  }
  return fieldParts.length ? fieldParts.join("; ") : null;
}
