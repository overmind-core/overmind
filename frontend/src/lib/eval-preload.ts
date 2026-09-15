/** Eval-set preload lifecycle, hand-parsed from ``improvementMetadata`` — the
 *  backend exposes no OpenAPI field for it. */

export type EvalPreloadStatus = "pending" | "running" | "ready" | "failed" | "empty";

export interface EvalPreload {
  status: EvalPreloadStatus;
  startedAt?: string;
  finishedAt?: string | null;
  error?: string | null;
  counts?: Record<string, number>;
}

const STATUSES = new Set<EvalPreloadStatus>(["pending", "running", "ready", "failed", "empty"]);

function isEvalPreloadStatus(value: unknown): value is EvalPreloadStatus {
  return typeof value === "string" && STATUSES.has(value as EvalPreloadStatus);
}

export function isActiveEvalPreloadStatus(status: EvalPreloadStatus | null | undefined): boolean {
  return status === "pending" || status === "running";
}

function parseCounts(value: unknown): Record<string, number> | undefined {
  if (!value || typeof value !== "object") return undefined;
  const out: Record<string, number> = {};
  for (const [key, raw] of Object.entries(value as Record<string, unknown>)) {
    if (typeof raw === "number" && Number.isFinite(raw)) {
      out[key] = raw;
    }
  }
  return Object.keys(out).length > 0 ? out : undefined;
}

function parseEvalPreloadBlob(blob: unknown): EvalPreload | null {
  if (!blob || typeof blob !== "object") return null;
  const record = blob as Record<string, unknown>;
  if (!isEvalPreloadStatus(record.status)) return null;

  const finishedAt = record.finished_at;
  const errorValue = record.error;

  let error: string | null | undefined;
  if (typeof errorValue === "string") {
    error = errorValue;
  } else if (errorValue === null) {
    error = null;
  }

  return {
    counts: parseCounts(record.counts),
    error,
    finishedAt:
      finishedAt === null || typeof finishedAt === "string"
        ? (finishedAt as string | null)
        : undefined,
    startedAt: typeof record.started_at === "string" ? record.started_at : undefined,
    status: record.status,
  };
}

export function getEvalPreload(capability: { improvementMetadata?: unknown }): EvalPreload | null {
  const meta = capability.improvementMetadata;
  if (!meta || typeof meta !== "object") return null;
  return parseEvalPreloadBlob((meta as Record<string, unknown>).eval_preload);
}

/** Fallback for capabilities created before eval_preload was written. */
export function resolveEvalPreloadStatus(
  capability: { improvementMetadata?: unknown },
  options?: { defaultSetMemberCount?: number }
): EvalPreloadStatus | null {
  const preload = getEvalPreload(capability);
  if (preload) return preload.status;
  if ((options?.defaultSetMemberCount ?? 0) > 0) return "ready";
  return null;
}

const EVAL_PRELOAD_POLL_MS = 3_000;

export function evalPreloadRefetchInterval(
  status: EvalPreloadStatus | null | undefined
): number | false {
  return isActiveEvalPreloadStatus(status) ? EVAL_PRELOAD_POLL_MS : false;
}
