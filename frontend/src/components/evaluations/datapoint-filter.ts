export type VerdictFilter = "all" | "passed" | "failed";

// Mirrors the shared "good" score tier (scoreChipClass) so the filter agrees
// with the chip colors in the table.
const PASS_THRESHOLD = 0.7;

export function scoreVerdict(
  value: number | null | undefined,
  passed: boolean | null | undefined
): "passed" | "failed" | null {
  if (passed === true) return "passed";
  if (passed === false) return "failed";
  if (value == null) return null;
  return value >= PASS_THRESHOLD ? "passed" : "failed";
}

export function matchesSearch(query: string, texts: Array<string | null | undefined>): boolean {
  const q = query.trim().toLowerCase();
  if (!q) return true;
  return texts.some((t) => !!t && t.toLowerCase().includes(q));
}

export type SortDir = "asc" | "desc";

/** Null/undefined sort last in both directions. */
export function sortRows<T>(
  rows: T[],
  value: (row: T) => number | string | null | undefined,
  dir: SortDir
): T[] {
  const mul = dir === "asc" ? 1 : -1;
  return [...rows].sort((a, b) => {
    const va = value(a);
    const vb = value(b);
    if (va == null && vb == null) return 0;
    if (va == null) return 1;
    if (vb == null) return -1;
    if (typeof va === "string" || typeof vb === "string")
      return mul * String(va).localeCompare(String(vb));
    return mul * (va - vb);
  });
}
