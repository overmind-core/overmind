import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { backoffPolling } from "@/lib/poll";
import type { Session, SessionsListRequest } from "@/openapi";

export interface SessionRow {
  readonly id: string;
  readonly externalId: string;
  readonly name: string;
  project: string;
  capability: string | null;
  capabilityName: string | null;
  traceCount: number;
  spanCount: number;
  firstSpanNs: number | null;
  lastSpanNs: number | null;
  sessionScore: number | null;
  totalTokens: number | null;
  totalCost: number | null;
  model: string | null;
  readonly createdAt: string;
}

export function nsToIso(ns: number | null | undefined): string | null {
  if (ns == null || ns <= 0) return null;
  return new Date(ns / 1_000_000).toISOString();
}

const transformSession = (row: Session): SessionRow => ({
  capability: row.capability,
  capabilityName: row.capabilityName ?? null,
  createdAt: row.createdAt instanceof Date ? row.createdAt.toISOString() : String(row.createdAt),
  externalId: row.externalId,
  firstSpanNs: row.firstSpanNs ?? null,
  id: row.id,
  lastSpanNs: row.lastSpanNs ?? null,
  model: row.model ?? null,
  name: row.name,
  project: row.project,
  sessionScore: row.sessionScore ?? null,
  spanCount: row.spanCount,
  totalCost: row.totalCost ?? null,
  totalTokens: row.totalTokens ?? null,
  traceCount: row.traceCount,
});

interface SessionsListResponse {
  count: number;
  next?: string | null;
  previous?: string | null;
  results: SessionRow[];
}

export interface SessionsListParams {
  project_id: string;
  capability?: string;
  page?: number;
  pageSize?: number;
  search?: string;
  ordering?: string;
  /** Defaults to true. */
  enabled?: boolean;
}

export function useSessionsList(params: SessionsListParams) {
  const { project_id, capability, page = 1, pageSize, search, ordering, enabled = true } = params;

  return useQuery({
    enabled: enabled && !!project_id,
    queryFn: async (): Promise<SessionsListResponse> => {
      const request: SessionsListRequest = {
        capability: capability || undefined,
        ordering,
        page,
        pageSize,
        project: project_id,
        search: search || undefined,
      };
      const raw = await apiClient.sessions.sessionsList(request);
      return {
        count: raw.count,
        next: raw.next,
        previous: raw.previous,
        results: (raw.results ?? []).map(transformSession),
      };
    },
    queryKey: ["sessions", project_id, capability, page, pageSize, search, ordering],
    refetchInterval: (q) => backoffPolling(q, 10_000),
  });
}

export function useSessionDetail(sessionId: string | undefined) {
  return useQuery({
    enabled: !!sessionId,
    queryFn: async (): Promise<SessionRow> =>
      transformSession(await apiClient.sessions.sessionsRetrieve({ id: sessionId! })),
    queryKey: ["session", sessionId],
  });
}
