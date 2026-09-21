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

export interface DatapointRow {
  key: string;
  rowIndex: number | null;
  fallbackSampleId: string;
}

// EvalSample has no datapoint FK. Dataset/optimizer runs share rowIndex;
// trace-filter runs share sourceTraceId; otherwise each sample is its own row.
export function sampleRowKey(s: {
  id?: string;
  rowIndex?: number | null;
  sourceTraceId?: string;
}): string | null {
  if (!s.id) return null;
  if (s.rowIndex != null) return `row:${s.rowIndex}`;
  if (s.sourceTraceId) return `trace:${s.sourceTraceId}`;
  return `sample:${s.id}`;
}

export function groupSampleRows(
  samples: Array<{
    id?: string;
    rowIndex?: number | null;
    sourceTraceId?: string;
    variant?: string | null;
  }>
): {
  datapointRows: DatapointRow[];
  sampleMap: Map<string, Map<string, string>>;
  sampleVariantMap: Map<string, string>;
} {
  const sampleMap = new Map<string, Map<string, string>>();
  const seen = new Map<string, { rowIndex: number | null; sampleId: string }>();
  const sampleVariantMap = new Map<string, string>();

  for (const s of samples) {
    const key = sampleRowKey(s);
    if (!key || !s.id) continue;
    if (!sampleMap.has(key)) sampleMap.set(key, new Map());
    if (s.variant) {
      sampleMap.get(key)!.set(s.variant, s.id);
      sampleVariantMap.set(s.id, s.variant);
    }
    if (!seen.has(key)) seen.set(key, { rowIndex: s.rowIndex ?? null, sampleId: s.id });
  }

  return {
    datapointRows: [...seen.entries()].map(([key, first]) => ({
      fallbackSampleId: first.sampleId,
      key,
      rowIndex: first.rowIndex,
    })),
    sampleMap,
    sampleVariantMap,
  };
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
