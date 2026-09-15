// Do not reduce "formatted" to pretty-printed JSON: models already emit pretty
// JSON, so the toggle would be a no-op.

export type ViewMode = "formatted" | "raw";

function formatValue(value: unknown, depth: number): string {
  if (value === null || typeof value !== "object") {
    return typeof value === "string" ? value : String(value);
  }
  const pad = "  ".repeat(depth);
  const entries: Array<[string, unknown]> = Array.isArray(value)
    ? value.map((v, i) => [String(i), v] as [string, unknown])
    : Object.entries(value as Record<string, unknown>);
  if (entries.length === 0) return `${pad}${Array.isArray(value) ? "[]" : "{}"}`;
  return entries
    .map(([key, v]) => {
      if (v !== null && typeof v === "object") {
        return `${pad}${key}:\n${formatValue(v, depth + 1)}`;
      }
      const scalar = typeof v === "string" ? v : String(v);
      if (scalar.includes("\n")) {
        const block = scalar
          .split("\n")
          .map((line) => `${pad}  ${line}`)
          .join("\n");
        return `${pad}${key}:\n${block}`;
      }
      return `${pad}${key}: ${scalar}`;
    })
    .join("\n");
}

export function toFormatted(raw: string): string {
  const trimmed = raw.trim();
  if (!trimmed) return raw;
  let parsed: unknown;
  try {
    parsed = JSON.parse(trimmed);
  } catch {
    return raw; // not JSON — identical in both modes
  }
  // JSON scalars ("42", "\"hi\"") gain nothing from reformatting.
  if (parsed === null || typeof parsed !== "object") return raw;
  return formatValue(parsed, 0);
}

export const renderPayload = (text: string, mode: ViewMode): string =>
  mode === "formatted" ? toFormatted(text) : text;

const USEFUL_KEYS = [
  "id",
  "status",
  "summary",
  "name",
  "title",
  "error",
  "kind",
  "query",
  "ref",
  "text",
  "score",
  "type",
] as const;
const ARRAY_PREVIEW = 3;
const STRING_MAX = 240;

function isEmptyPayload(value: unknown): boolean {
  if (value == null) return true;
  if (typeof value === "string") {
    const t = value.trim();
    return !t || t === "{}" || t === "[]" || t === "null" || t === "undefined";
  }
  if (Array.isArray(value)) return value.length === 0;
  if (typeof value === "object") return Object.keys(value as object).length === 0;
  return false;
}

function coercePayload(value: unknown): unknown {
  if (typeof value !== "string") return value;
  const trimmed = value.trim();
  if (isEmptyPayload(trimmed)) return null;
  try {
    return JSON.parse(trimmed);
  } catch {
    return trimmed;
  }
}

function clipString(value: string): string {
  return value.length > STRING_MAX ? `${value.slice(0, STRING_MAX)}…` : value;
}

function pickUseful(item: unknown): unknown {
  if (typeof item === "string") return clipString(item);
  if (item == null || typeof item !== "object" || Array.isArray(item)) return item;
  const rec = item as Record<string, unknown>;
  const picked: Record<string, unknown> = {};
  for (const key of USEFUL_KEYS) {
    const val = rec[key];
    if (val == null || val === "") continue;
    picked[key] = typeof val === "string" ? clipString(val) : val;
  }
  if (Object.keys(picked).length) return picked;
  const scalars = Object.entries(rec)
    .filter(([, val]) => val != null && (typeof val !== "object" || val === null))
    .slice(0, ARRAY_PREVIEW);
  return Object.fromEntries(
    scalars.map(([key, val]) => [key, typeof val === "string" ? clipString(val) : val])
  );
}

function summarize(value: unknown): unknown {
  if (typeof value === "string") return clipString(value);
  if (Array.isArray(value)) {
    const items = value.slice(0, ARRAY_PREVIEW).map(pickUseful);
    if (value.length > ARRAY_PREVIEW) return { count: value.length, items };
    return items;
  }
  if (value == null || typeof value !== "object") return value;
  const out: Record<string, unknown> = {};
  for (const [key, val] of Object.entries(value as Record<string, unknown>)) {
    if (Array.isArray(val)) {
      const items = val.slice(0, ARRAY_PREVIEW).map(pickUseful);
      out[key] = val.length > ARRAY_PREVIEW ? { count: val.length, items } : items;
      continue;
    }
    out[key] = summarize(val);
  }
  return out;
}

export function formatPayload(value: unknown): string {
  const parsed = coercePayload(value);
  if (isEmptyPayload(parsed)) return "";
  if (typeof parsed === "string") return clipString(parsed);
  return formatValue(summarize(parsed), 0);
}
