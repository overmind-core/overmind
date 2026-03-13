import { useQuery } from "@tanstack/react-query";

import type { SpanResponseModel } from "@/api";
import apiClient from "@/client";
import { getTimeRangeStartTimestamp } from "@/lib/formatters";

type TimeRange = "all" | "past5m" | "past1h" | "past24h" | "past7d" | "past30d";

interface FetchTracesParams {
  project_id: string;
  /** Use timeRange in queryKey for stable caching; start_timestamp is computed at fetch time */
  timeRange?: TimeRange;
  root_only?: boolean;
  limit?: number;
  offset?: number;
  promptSlug?: string;
  promptVersion?: string;
  /**
   * Backend DRF-style filter strings (`field;operator;value`).
   * Passed directly to the `?filter=` query param.
   */
  filter?: string[];
}

/** OpenTelemetry status codes: 0=UNSET, 1=OK, 2=ERROR */
export function spanStatusLabel(statusCode: number): string {
  if (statusCode === 2) return "error";
  if (statusCode === 1) return "ok";
  return "unset";
}

export interface SpanRow {
  spanId: string;
  parentSpanId: string | null;
  name: string;
  scopeName: string;
  statusCode: number;
  durationNano: number;
  startTimeUnixNano: number;
  endTimeUnixNano: number;
  spanAttributes: Record<string, unknown>;
  resourceAttributes: Record<string, unknown>;
  inputs: unknown | null;
  outputs: unknown | null;
  policyOutcome: string | null;
  scopeVersion: string | null;
  events: unknown[];
  links: unknown[];
  traceId: string;
  agentId: string | null;
  judgeScore?: { rating: "up" | "down"; text: string | null };
  agentScore?: { rating: "up" | "down"; text: string | null };
  feedbackScores?: Record<string, unknown>;
  cost: number;
}

export const transformSpan = (span: SpanResponseModel): SpanRow => {
  const feedbackScore = { ...span.spanAttributes?.feedback_score } as
    | Record<string, unknown>
    | undefined;
  return {
    agentId: span.agentId ?? null,
    agentScore:
      (feedbackScore?.agent_feedback as { rating: "up" | "down"; text: string | null }) ??
      undefined,
    cost: (span.spanAttributes?.cost as number | undefined) ?? 0,
    durationNano: span.durationNano,
    endTimeUnixNano: span.endTimeUnixNano,
    events: span.events ?? [],
    feedbackScores: feedbackScore,
    inputs: span.inputs ?? span.spanAttributes?.["traceloop.entity.input"] ?? null,
    judgeScore:
      (feedbackScore?.judge_feedback as { rating: "up" | "down"; text: string | null }) ??
      undefined,
    links: span.links ?? [],
    name: span.name ?? "",
    outputs: span.outputs ?? span.spanAttributes?.["traceloop.entity.output"] ?? null,
    parentSpanId: span.parentSpanId ?? null,
    policyOutcome: span.policyOutcome ?? null,
    resourceAttributes: span.resourceAttributes ?? {},
    scopeName: span.scopeName ?? "",
    scopeVersion: span.scopeVersion ?? null,
    spanAttributes: span.spanAttributes ?? {},
    spanId: span.spanId,
    startTimeUnixNano: span.startTimeUnixNano,
    statusCode: span.statusCode,
    traceId: span.traceId,
  };
};

export function useTracesList(params: FetchTracesParams) {
  const {
    project_id,
    timeRange = "all",
    root_only = true,
    limit = 100,
    offset = 0,
    promptSlug,
    promptVersion,
    filter,
  } = params;

  return useQuery({
    queryFn: async () => {
      const data = await apiClient.traces.listTracesApiV1TracesListGet({
        limit,
        offset,
        projectId: project_id,
        promptSlug,
        promptVersion,
        query: filter,
        rootOnly: root_only,
        startTimestamp: new Date(getTimeRangeStartTimestamp(timeRange)),
      });
      return {
        ...data,
        hasMore: data.count > limit + offset,
        traces: data.traces.map(transformSpan),
      };
    },
    queryKey: ["traces", project_id, timeRange, limit, offset, promptSlug, promptVersion, filter],
  });
}

export function useTraceDetail(traceId: string | undefined, projectId: string | undefined) {
  return useQuery({
    enabled: !!traceId && !!projectId,
    queryFn: async () => {
      const data = await apiClient.traces.getTraceByIdApiV1TracesTraceTraceIdGet({
        projectId: projectId!,
        traceId: traceId!,
      });
      return { ...data, spans: data.spans.map(transformSpan) };
    },
    queryKey: ["trace", traceId, projectId],
  });
}
