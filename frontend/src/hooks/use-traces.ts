import { keepPreviousData, useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { backoffPolling } from "@/lib/poll";
import type { Span, TraceDetail, TracesListRequest } from "@/openapi";

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

/** One eval matrix row's grade on a span, from `Span.eval_attributes.metrics`. */
export interface TraceScoreEntry {
  score: number | null;
  outcome: "scored" | "abstained" | "error";
  rationale: string;
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

  // Keyed by matrix row name.
  traceScores: Record<string, TraceScoreEntry>;
  evalMetrics: unknown;
  evalScore: number | null;
  trajectoryScore: number | null;
}

function evalBlock(evalAttributes: unknown): Record<string, unknown> {
  return evalAttributes != null && typeof evalAttributes === "object"
    ? (evalAttributes as Record<string, unknown>)
    : {};
}

function finiteOrNull(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function extractTraceScores(evalAttributes: unknown): Record<string, TraceScoreEntry> {
  const metrics = evalBlock(evalAttributes).metrics;
  if (metrics == null || typeof metrics !== "object") return {};
  const out: Record<string, TraceScoreEntry> = {};
  for (const [name, raw] of Object.entries(metrics as Record<string, unknown>)) {
    if (raw == null || typeof raw !== "object") continue;
    const m = raw as Record<string, unknown>;
    const error = typeof m.error === "string" ? m.error : "";
    const reason = typeof m.reason === "string" ? m.reason : "";
    out[name] = {
      outcome: error ? "error" : m.verdict === "abstain" ? "abstained" : "scored",
      rationale: error || reason,
      score: finiteOrNull(m.score),
    };
  }
  return out;
}

function extractTrajectoryScore(evalAttributes: unknown): number | null {
  const trajectory = evalBlock(evalAttributes).trajectory;
  return trajectory != null && typeof trajectory === "object"
    ? finiteOrNull((trajectory as Record<string, unknown>).score)
    : null;
}

/** The trace's headline: the trajectory score when the root carries one, else the span score. */
function extractEvalScore(evalAttributes: unknown): number | null {
  return extractTrajectoryScore(evalAttributes) ?? finiteOrNull(evalBlock(evalAttributes).score);
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
    evalMetrics: evalBlock(span.evalAttributes).metrics ?? null,
    evalScore: finiteOrNull(evalBlock(span.evalAttributes).score),
    events: Array.isArray(span.events) ? (span.events as SpanEvent[]) : [],
    inputs: span.inputAttribute ? (attrs[span.inputAttribute] ?? null) : null,
    model: pickModel(attrs),
    name: span.name ?? "",
    outputs: span.outputAttribute ? (attrs[span.outputAttribute] ?? null) : null,
    parentSpanId: span.parentSpanId || null,
    policyOutcome: (attrs.policy_outcome as string) ?? null,
    scopeName,
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
    traceScores: extractTraceScores(span.evalAttributes),
    trajectoryScore: extractTrajectoryScore(span.evalAttributes),
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
  error?: string | null;
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
  if (filters.signal_event) request.signalEvent = filters.signal_event;
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
  evalAttributes?: unknown;
  source?: string | null;
  traceStatus?: string | null;
}): TraceRow {
  const createdAt =
    row.receivedAt instanceof Date ? row.receivedAt.toISOString() : String(row.receivedAt ?? "");
  const statusMessage = row.statusMessage ?? "";
  return {
    applicationName: row.name ?? undefined,
    capability: row.capability,
    capabilityName: row.capabilityName ?? null,
    createdAt,
    durationNs: row.durationNs ?? undefined,
    error: row.statusCode === 2 ? statusMessage || "error" : null,
    evaluations: Object.keys(extractTraceScores(row.evalAttributes)).length,
    model: row.model ?? null,
    operation: row.operation ?? undefined,
    parentSpanId: null,
    project: row.project,
    score: extractEvalScore(row.evalAttributes) ?? undefined,
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
  const data = await apiClient.traces.tracesRetrieve({ traceId });
  return { ...data, transformedSpans: (data.spans ?? []).map(transformSpan) };
}

// A live trace refetches until the root span arrives or the settle window passes.
const LIVE_TRACE_POLL_MS = 4_000;

export function useTraceDetail(
  traceId: string | undefined,
  projectId: string | undefined,
  retainPreviousData = false
) {
  return useQuery({
    enabled: !!traceId && !!projectId,
    placeholderData: retainPreviousData ? keepPreviousData : undefined,
    queryFn: () => fetchTraceDetail(traceId!),
    queryKey: ["trace", traceId, projectId],
    refetchInterval: (query) =>
      query.state.data?.traceStatus === "live" ? LIVE_TRACE_POLL_MS : false,
  });
}
