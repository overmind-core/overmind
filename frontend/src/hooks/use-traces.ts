import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { backoffPolling } from "@/lib/poll";
import type { Span, TraceDetail, TracesListRequest, Verdict } from "@/openapi";

export function spanStatusLabel(statusCode: number): string {
  if (statusCode === 2) return "error";
  if (statusCode === 1) return "ok";
  return "unset";
}

export interface SpanEvent {
  name: string;
  attributes?: Record<string, unknown>;
  timeUnixNano?: number;
}

// Per-evaluator grade on a span: a Verdict row, or the invocations-summary
// entry the scorer leaves in `Span.feedback_score.trace_scoring`.
export interface TraceScoreEntry {
  score: number | null;
  passed: boolean | null;
  outcome: string;
  rationale: string;
  lane?: string;
}

export interface SpanRow {
  spanId: string;
  parentSpanId: string | null;
  name: string;
  scopeName: string;
  statusCode: number;
  statusMessage: string;
  durationNano: number;
  startTimeUnixNano: number;
  endTimeUnixNano: number;
  spanAttributes: Record<string, unknown>;
  inputs: unknown | null;
  outputs: unknown | null;
  events: SpanEvent[];
  policyOutcome: string | null;
  traceId: string;

  spanType: "llm_call" | "tool_call" | null;
  totalTokens: number | null;
  totalCost: number | null;
  model: string | null;

  // Bound at ingest from the SDK-stamped `overmind.capability.id` attribute.
  capability: string | null;
  capabilityName: string | null;

  // Keyed by evaluator name.
  traceScores: Record<string, TraceScoreEntry>;
  executionConflict: ExecutionConflict | null;
  skippedMembers: string[];
}

function extractTraceScores(feedbackScore: unknown): Record<string, TraceScoreEntry> {
  if (feedbackScore == null || typeof feedbackScore !== "object") return {};
  const block = (feedbackScore as Record<string, unknown>).trace_scoring;
  if (block == null || typeof block !== "object") return {};
  const out: Record<string, TraceScoreEntry> = {};
  for (const [name, entry] of Object.entries(block as Record<string, unknown>)) {
    // `_`-prefixed keys are composed markers, not evaluator entries.
    if (name.startsWith("_") || entry == null || typeof entry !== "object") continue;
    out[name] = entry as TraceScoreEntry;
  }
  return out;
}

/** Enabled members the pass excluded persist as `not_applicable` rows with a
 *  `skip:*` clause — display surfaces never show them as grades. */
function isSkipVerdict(verdict: Verdict): boolean {
  return (
    verdict.outcome === "not_applicable" &&
    Array.isArray(verdict.unmet) &&
    verdict.unmet.some((clause) => typeof clause === "string" && clause.startsWith("skip:"))
  );
}

/**
 * Latest displayable verdicts keyed by span id then evaluator name. Expects
 * the API's newest-first ordering (`-updated_at`), so the first row per
 * (span, evaluator) is the latest rescore series.
 */
function verdictScoresBySpan(
  verdicts: readonly Verdict[]
): Record<string, Record<string, TraceScoreEntry>> {
  const out: Record<string, Record<string, TraceScoreEntry>> = {};
  for (const verdict of verdicts) {
    if (isSkipVerdict(verdict)) continue;
    if (verdict.evaluatorName === "grounding" && verdict.outcome !== "scored") continue;
    out[verdict.targetId] ??= {};
    const spanScores = out[verdict.targetId];
    if (spanScores[verdict.evaluatorName]) continue;
    spanScores[verdict.evaluatorName] = {
      outcome: verdict.outcome,
      passed: verdict.passed,
      rationale: verdict.explanation,
      score: verdict.score,
    };
  }
  return out;
}

// Stamped by the composer into `trace_scoring._execution.conflict` when
// outcome-lane judges land >0.5 apart instead of averaging silently.
export interface ExecutionConflict {
  lane: string;
  spread: number;
  members: Record<string, number>;
}

export function extractExecutionConflict(feedbackScore: unknown): ExecutionConflict | null {
  if (feedbackScore == null || typeof feedbackScore !== "object") return null;
  const block = (feedbackScore as Record<string, unknown>).trace_scoring;
  if (block == null || typeof block !== "object") return null;
  const execution = (block as Record<string, unknown>)._execution;
  if (execution == null || typeof execution !== "object") return null;
  const conflict = (execution as Record<string, unknown>).conflict;
  if (conflict == null || typeof conflict !== "object") return null;
  const { lane, spread, members } = conflict as Record<string, unknown>;
  if (typeof spread !== "number" || members == null || typeof members !== "object") return null;
  const numeric = Object.entries(members as Record<string, unknown>).filter(
    (pair): pair is [string, number] => typeof pair[1] === "number"
  );
  if (numeric.length < 2) return null;
  return {
    lane: typeof lane === "string" ? lane : "",
    members: Object.fromEntries(numeric),
    spread,
  };
}

/** Names only — the skip reason lives on the Verdict row. */
export function extractSkippedMembers(feedbackScore: unknown): string[] {
  if (feedbackScore == null || typeof feedbackScore !== "object") return [];
  const block = (feedbackScore as Record<string, unknown>).trace_scoring;
  if (block == null || typeof block !== "object") return [];
  const skipped = (block as Record<string, unknown>)._skipped_members;
  if (!Array.isArray(skipped)) return [];
  return skipped.filter((name): name is string => typeof name === "string");
}

/**
 * The task execution score for a trace: the scorer-persisted
 * `trace_scoring._execution.score`. The only score copy on the span —
 * per-evaluator grades live on Verdict rows.
 */
function extractExecutionScore(feedbackScore: unknown): number | null {
  if (feedbackScore == null || typeof feedbackScore !== "object") return null;
  const block = (feedbackScore as Record<string, unknown>).trace_scoring;
  if (block == null || typeof block !== "object") return null;
  const execution = (block as Record<string, unknown>)._execution;
  if (execution == null || typeof execution !== "object") return null;
  const score = (execution as Record<string, unknown>).score;
  return typeof score === "number" && Number.isFinite(score) ? score : null;
}

/** How many evaluators graded the unit — `trace_scoring._execution.evaluations`. */
function extractEvaluationsCount(feedbackScore: unknown): number {
  if (feedbackScore == null || typeof feedbackScore !== "object") return 0;
  const block = (feedbackScore as Record<string, unknown>).trace_scoring;
  if (block == null || typeof block !== "object") return 0;
  const execution = (block as Record<string, unknown>)._execution;
  if (execution != null && typeof execution === "object") {
    const evaluations = (execution as Record<string, unknown>).evaluations;
    if (typeof evaluations === "number" && Number.isFinite(evaluations)) return evaluations;
  }
  return Object.keys(extractTraceScores(feedbackScore)).length;
}

function pickNumber(attrs: Record<string, unknown>, ...keys: string[]): number | null {
  for (const key of keys) {
    const value = attrs[key];
    if (value == null) continue;
    const n = typeof value === "string" ? Number(value) : (value as number);
    if (Number.isFinite(n)) return n;
  }
  return null;
}

// Precedence mirrors the backend's `_model_from_span_attributes`.
export function pickModel(attrs: Record<string, unknown>): string | null {
  for (const key of [
    "genai.model",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "genai.response.model",
    "llm.model",
    "model",
  ]) {
    const value = attrs[key];
    if (typeof value === "string" && value.trim()) return value.trim();
  }
  return null;
}

function pickSpanType(attrs: Record<string, unknown>, scopeName: string): SpanRow["spanType"] {
  const explicit = attrs["overmind.span.type"];
  if (explicit === "llm_call" || explicit === "tool_call") return explicit;
  // Heuristic fallbacks aligned with backend _classify_span_type.
  if (attrs["genai.model"] || attrs["llm.model"] || attrs["gen_ai.system"]) return "llm_call";
  if (attrs["tool.name"] || scopeName === "mcp" || scopeName === "shell") return "tool_call";
  return null;
}

const transformSpan = (span: Span): SpanRow => {
  const attrs = { ...((span.attributes ?? {}) as Record<string, unknown>) };

  const startNano = span.startTimeNs ?? 0;
  const endNano = span.endTimeNs ?? 0;
  const durationNano = span.durationNs ?? Math.max(0, endNano - startNano);
  const scopeName = span.scopeName || span.operation || "";

  const promptTokens = pickNumber(attrs, "genai.prompt_tokens", "genai.usage.prompt_tokens");
  const completionTokens = pickNumber(
    attrs,
    "genai.completion_tokens",
    "genai.usage.completion_tokens"
  );
  const reportedTotalTokens = pickNumber(
    attrs,
    "genai.total_tokens",
    "genai.usage.total_tokens",
    "llm.usage.total_tokens",
    "gen_ai.usage.total_tokens"
  );

  const resourceAttrs = (span.resourceAttrs ?? {}) as Record<string, unknown>;
  const capabilityName =
    (attrs["overmind.capability.name"] as string | undefined) ??
    (resourceAttrs["overmind.capability.name"] as string | undefined) ??
    null;

  return {
    capability: span.capability ?? null,
    capabilityName,
    durationNano,
    endTimeUnixNano: endNano,
    events: Array.isArray(span.events) ? (span.events as SpanEvent[]) : [],
    executionConflict: extractExecutionConflict(span.feedbackScore),
    inputs:
      attrs["overmind.input_data"] ??
      attrs["overmind.input.data"] ??
      attrs.inputs ??
      attrs["traceloop.entity.input"] ??
      null,
    model: pickModel(attrs),
    name: span.name ?? "",
    outputs:
      attrs["overmind.output_data"] ??
      attrs["overmind.output.data"] ??
      attrs.outputs ??
      attrs["traceloop.entity.output"] ??
      null,
    parentSpanId: span.parentSpanId || null,
    policyOutcome: (attrs.policy_outcome as string) ?? null,
    scopeName,
    skippedMembers: extractSkippedMembers(span.feedbackScore),
    spanAttributes: attrs,
    spanId: span.spanId,
    spanType: pickSpanType(attrs, scopeName),
    startTimeUnixNano: startNano,
    statusCode: span.statusCode ?? 0,
    statusMessage: span.statusMessage ?? "",
    totalCost: pickNumber(
      attrs,
      "genai.cost",
      "overmind.cost",
      "llm.usage.total_cost",
      "cost",
      "response_cost",
      "gen_ai.usage.cost"
    ),
    totalTokens:
      reportedTotalTokens ??
      (promptTokens != null && completionTokens != null ? promptTokens + completionTokens : null),
    traceId: span.traceId ?? "",
    traceScores: extractTraceScores(span.feedbackScore),
  };
};

export type TraceStatus = "completed" | "live" | "interrupted";

export interface TraceRow {
  readonly traceId: string;
  readonly spanId: string;
  readonly parentSpanId: string | null;
  traceStatus?: TraceStatus;
  project?: string | null;
  capability?: string | null;
  capabilityName?: string | null;
  traceGroup?: string;
  applicationName?: string;
  operation?: string;
  serviceName?: string;
  spanType?: string;
  statusCode?: number;
  statusMessage?: string;
  durationNs?: number;
  totalTokens?: number | null;
  totalCost?: number | null;
  model?: string | null;
  score?: number;
  evaluations?: number;
  conflict?: ExecutionConflict | null;
  error?: string | null;
  scoringPending?: boolean;
  source?: string;
  readonly createdAt: string;
  readonly startTimeNs: number;
}

interface TracesListResponse {
  count: number;
  next?: string | null;
  previous?: string | null;
  results: TraceRow[];
}

export interface TracesListParams {
  project_id: string;
  page?: number;
  pageSize?: number;
  search?: string;
  ordering?: string;
  filters?: Record<string, string | undefined>;
  enabled?: boolean;
}

/** Filter value for spans ingest left unbound to any capability. */
export const UNBOUND_CAPABILITY = "__unbound__";

export type TraceRef = { id: string; capabilityId?: string | null };

function cleanFilterMap(filters?: Record<string, string | undefined>): Record<string, string> {
  const clean: Record<string, string> = {};
  if (!filters) return clean;
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== "") clean[k] = v;
  }
  return clean;
}

export function buildTracesListRequest(params: {
  projectId: string;
  page: number;
  pageSize?: number;
  search?: string;
  ordering?: string;
  filters: Record<string, string>;
}): TracesListRequest {
  const { projectId, page, pageSize, search, ordering, filters } = params;
  const request: TracesListRequest = {
    ordering,
    page,
    pageSize,
    project: projectId,
    search,
  };
  if (filters.all_spans) request.allSpans = filters.all_spans === "true";
  if (filters.capability === UNBOUND_CAPABILITY) request.unbound = true;
  else if (filters.capability) request.capability = filters.capability;
  if (filters.trace_id) request.traceId = filters.trace_id;
  if (filters.session) request.session = filters.session;
  if (filters.span_type) request.spanType = filters.span_type as TracesListRequest["spanType"];
  if (filters.status_code) request.statusCode = Number(filters.status_code);
  if (filters.name) request.name = filters.name;
  if (filters.model) request.model = filters.model;
  if (filters.has_model) request.hasModel = filters.has_model === "true";
  // The bare backend params already match `icontains`; there is no `__icontains` param.
  const operation = filters.operation__icontains ?? filters.operation;
  if (operation) request.operation = operation;
  const serviceName = filters.service_name__icontains ?? filters.service_name;
  if (serviceName) request.serviceName = serviceName;
  if (filters.min_duration_ms) request.minDurationMs = Number(filters.min_duration_ms);
  if (filters.max_duration_ms) request.maxDurationMs = Number(filters.max_duration_ms);
  if (filters.total_tokens__gte) request.totalTokensGte = Number(filters.total_tokens__gte);
  if (filters.total_tokens__lte) request.totalTokensLte = Number(filters.total_tokens__lte);
  if (filters.total_cost__gte) request.totalCostGte = Number(filters.total_cost__gte);
  if (filters.total_cost__lte) request.totalCostLte = Number(filters.total_cost__lte);
  if (filters.has_error) request.hasError = filters.has_error === "true";
  if (filters.received_at__gte) request.receivedAtGte = new Date(filters.received_at__gte);
  if (filters.received_at__lte) request.receivedAtLte = new Date(filters.received_at__lte);
  if (filters.start_time_ns__gte) request.startTimeNsGte = Number(filters.start_time_ns__gte);
  if (filters.start_time_ns__lte) request.startTimeNsLte = Number(filters.start_time_ns__lte);
  return request;
}

function mapTraceListRow(row: {
  receivedAt?: Date | null;
  startTimeNs?: number | null;
  statusMessage?: string | null;
  statusCode?: number | null;
  capability?: string | null;
  capabilityName?: string | null;
  name?: string | null;
  durationNs?: number | null;
  model?: string | null;
  operation?: string | null;
  project?: string | null;
  serviceName?: string | null;
  spanId: string;
  spanType?: string | null;
  totalCost?: number | null;
  totalTokens?: number | null;
  traceId: string;
  feedbackScore?: unknown;
  source?: string | null;
  scoringPending?: boolean | null;
  traceStatus?: string | null;
}): TraceRow {
  const createdAt =
    row.receivedAt instanceof Date ? row.receivedAt.toISOString() : String(row.receivedAt ?? "");
  const statusMessage = row.statusMessage ?? "";
  return {
    applicationName: row.name ?? undefined,
    capability: row.capability,
    capabilityName: row.capabilityName ?? null,
    conflict: extractExecutionConflict(row.feedbackScore),
    createdAt,
    durationNs: row.durationNs ?? undefined,
    error: row.statusCode === 2 ? statusMessage || "error" : null,
    evaluations: extractEvaluationsCount(row.feedbackScore),
    model: row.model ?? null,
    operation: row.operation ?? undefined,
    parentSpanId: null,
    project: row.project,
    score: extractExecutionScore(row.feedbackScore) ?? undefined,
    scoringPending: row.scoringPending ?? undefined,
    serviceName: row.serviceName ?? undefined,
    source: row.source ?? undefined,
    spanId: row.spanId,
    spanType: row.spanType ?? undefined,
    startTimeNs: row.startTimeNs ?? 0,
    statusCode: row.statusCode ?? undefined,
    statusMessage,
    totalCost: row.totalCost ?? null,
    totalTokens: row.totalTokens ?? null,
    traceId: row.traceId,
    traceStatus: (row.traceStatus as TraceStatus | null) ?? undefined,
  };
}

export function useTracesList(params: TracesListParams) {
  const { project_id, page = 1, pageSize, search, ordering, filters, enabled = true } = params;
  const cleanFilters = cleanFilterMap(filters);

  return useQuery({
    enabled: enabled && !!project_id,
    queryFn: async (): Promise<TracesListResponse> => {
      const raw = await apiClient.traces.tracesList(
        buildTracesListRequest({
          filters: cleanFilters,
          ordering,
          page,
          pageSize,
          projectId: project_id,
          search,
        })
      );
      return {
        count: raw.count,
        next: raw.next,
        previous: raw.previous,
        results: (raw.results ?? []).map(mapTraceListRow),
      };
    },
    queryKey: ["traces", project_id, page, search, ordering, cleanFilters, pageSize],
    refetchInterval: (i) =>
      backoffPolling(i, !i.state.data?.count || i.state.data.count <= 0 ? 5_000 : 10_000),
  });
}

export type TraceDetailView = TraceDetail & { transformedSpans: SpanRow[] };

async function fetchTraceDetail(traceId: string): Promise<TraceDetailView> {
  const [data, verdictPage] = await Promise.all([
    apiClient.traces.tracesRetrieve({ traceId }),
    apiClient.verdicts.verdictsList({ pageSize: 1000, traceId }),
  ]);
  const verdictScores = verdictScoresBySpan(verdictPage.results ?? []);
  return {
    ...data,
    transformedSpans: (data.spans ?? []).map((span) => {
      const row = transformSpan(span);
      // Verdict rows carry the per-evaluator grades; the slim block only
      // contributes the invocations summary on a multi-entry root.
      return { ...row, traceScores: { ...verdictScores[row.spanId], ...row.traceScores } };
    }),
  };
}

// A live trace refetches until the root span arrives or the settle window passes.
const LIVE_TRACE_POLL_MS = 4_000;

export function useTraceDetail(traceId: string | undefined, projectId: string | undefined) {
  return useQuery({
    enabled: !!traceId && !!projectId,
    queryFn: () => fetchTraceDetail(traceId!),
    queryKey: ["trace", traceId, projectId],
    refetchInterval: (query) =>
      query.state.data?.traceStatus === "live" ? LIVE_TRACE_POLL_MS : false,
  });
}
