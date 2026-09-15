import { useMemo } from "react";

import { useQuery } from "@tanstack/react-query";

import apiClient from "@/client";
import { humanizeKey } from "@/lib/label-case";
import { backoffPolling } from "@/lib/poll";
import { scorePct } from "@/lib/utils";
import type {
  TaskExecutionList,
  TaskExecutionsListBindingSourceEnum,
  TaskExecutionsListRequest,
} from "@/openapi";

// Shape of one `TaskExecution.step_results` entry (untyped JSON in the schema).
export interface ExecutionStepResult {
  evaluator?: string;
  display_name?: string;
  role?: "step" | "outcome" | string;
  segment?: string[];
  score?: number | null;
  passed?: boolean | null;
  outcome?: string;
  rationale?: string;
  delivery?: string;
  clears_outstanding?: boolean;
}

export function executionStepResults(raw: unknown): ExecutionStepResult[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((e): e is ExecutionStepResult => e != null && typeof e === "object");
}

export function executionRouteFlags(raw: unknown): string[] {
  if (!Array.isArray(raw)) return [];
  return raw.filter((f): f is string => typeof f === "string");
}

export function isUnboundExecution(row: TaskExecutionList): boolean {
  return row.bindingSource === "unbound" || !row.behaviour;
}

const GRAIN_MISMATCH_FLAGS = ["declared_grain_mismatch", "grain_mismatch"];

/** Chip label for an unbound row. A grain mismatch is the one cause the author
 * can fix in code — the declared key names a task of the other grain — so it is
 * named instead of hidden behind the generic "Unbound". */
export function unboundReason(row: Pick<TaskExecutionList, "routeFlags">): string {
  const flags = executionRouteFlags(row.routeFlags);
  return flags.some((flag) => GRAIN_MISMATCH_FLAGS.includes(flag)) ? "Grain mismatch" : "Unbound";
}

export function executionConversationId(row: Pick<TaskExecutionList, "conversationId">): string {
  return (row.conversationId ?? "").trim();
}

export function shortConversationId(id: string): string {
  return id.length > 8 ? `${id.slice(0, 8)}…` : id;
}

export function executionSessionScore(
  row: { sessionScore?: number | null } | undefined
): number | null {
  const score = row?.sessionScore;
  return typeof score === "number" ? score : null;
}

export function conversationGroupCaption(count: number, sessionScore: number | null): string {
  const turns = `${count} ${count === 1 ? "turn" : "turns"}`;
  if (sessionScore == null) return turns;
  return `${turns} · session ${scorePct(sessionScore)}%`;
}

export function outcomeDelivery(steps: ExecutionStepResult[]): string {
  const outcome = steps.find((step) => step.role === "outcome" && step.outcome === "scored");
  return (outcome?.delivery ?? "").trim().toLowerCase();
}

export function deliveryLabel(delivery: string): string {
  switch (delivery) {
    case "first_park":
      return "First park";
    case "delivered_wrong":
      return "Wrong artifact";
    case "outstanding":
      return "Outstanding";
    case "blocked":
      return "Blocked";
    case "delivered":
      return "Delivered";
    case "clarify":
      return "Clarify";
    default:
      return "";
  }
}

export function taskStateLabel(status: string): string {
  if (status === "outstanding") return "Outstanding";
  if (status === "delivered") return "Complete";
  return status;
}

function startedAtMs(row: TaskExecutionList): number {
  const t = row.startedAt;
  if (t == null) return 0;
  const ms = t instanceof Date ? t.getTime() : Date.parse(String(t));
  return Number.isFinite(ms) ? ms : 0;
}

/** Capability identity for handoff comparisons — unbound rows share one slot. */
export function executionCapabilitySlot(row: Pick<TaskExecutionList, "capability">): string {
  return row.capability ?? "unbound";
}

export function handoffTraceIds(rows: TaskExecutionList[]): Set<string> {
  const slotsByTrace = new Map<string, Set<string>>();
  for (const row of rows) {
    let slots = slotsByTrace.get(row.traceId);
    if (!slots) {
      slots = new Set();
      slotsByTrace.set(row.traceId, slots);
    }
    slots.add(executionCapabilitySlot(row));
  }
  return new Set([...slotsByTrace].filter(([, slots]) => slots.size >= 2).map(([id]) => id));
}

export function hasCapabilityHandoff(rows: TaskExecutionList[]): boolean {
  return new Set(rows.map(executionCapabilitySlot)).size >= 2;
}

export function sortExecutionsByStart(rows: TaskExecutionList[]): TaskExecutionList[] {
  return [...rows].sort((a, b) => startedAtMs(a) - startedAtMs(b));
}

export function groupExecutionsByTrace(
  rows: TaskExecutionList[],
  traceIds: Set<string>
): TaskExecutionList[] {
  const emitted = new Set<string>();
  const out: TaskExecutionList[] = [];
  for (const row of rows) {
    if (!traceIds.has(row.traceId)) {
      out.push(row);
      continue;
    }
    if (emitted.has(row.traceId)) continue;
    emitted.add(row.traceId);
    out.push(...sortExecutionsByStart(rows.filter((r) => r.traceId === row.traceId)));
  }
  return out;
}

export function executionCapabilityLabel(
  row: Pick<TaskExecutionList, "capability">,
  nameById: Map<string, string>
): string {
  if (!row.capability) return "Unbound";
  return nameById.get(row.capability) ?? shortConversationId(row.capability);
}

export function capabilityHandoffChain(
  rows: TaskExecutionList[],
  nameById: Map<string, string>
): string[] {
  const chain: string[] = [];
  let lastSlot: string | null = null;
  for (const row of sortExecutionsByStart(rows)) {
    const slot = executionCapabilitySlot(row);
    if (slot === lastSlot) continue;
    lastSlot = slot;
    chain.push(executionCapabilityLabel(row, nameById));
  }
  return chain;
}

export interface ExecutionIntent {
  text: string;
  source: string;
  current?: string;
}

export function executionIntent(raw: unknown): ExecutionIntent | null {
  if (raw == null || typeof raw !== "object") return null;
  const { text, source, running } = raw as {
    text?: unknown;
    source?: unknown;
    running?: unknown;
  };
  const current = typeof text === "string" ? text.trim() : "";
  const runningText = typeof running === "string" ? running.trim() : "";
  const display = runningText || current;
  if (!display) return null;
  const intent: ExecutionIntent = {
    source: typeof source === "string" ? source : "",
    text: display,
  };
  if (runningText && current && runningText !== current) {
    intent.current = current;
  }
  return intent;
}

export function scoredStepResults(steps: ExecutionStepResult[]): ExecutionStepResult[] {
  return steps.filter((s) => s.outcome === "scored" && (s.score != null || s.passed != null));
}

// Failure rows are backend-appended gate/cap failures that explain a zeroed turn.
export function stepRoleResults(steps: ExecutionStepResult[]): ExecutionStepResult[] {
  return scoredStepResults(steps).filter((s) => s.role === "step" || s.role === "failure");
}

export function stepVerdictLabel(step: ExecutionStepResult): string {
  if (typeof step.score === "number") return `${scorePct(step.score)}%`;
  if (step.passed === true) return "Pass";
  if (step.passed === false) return "Fail";
  return "—";
}

const BEHAVIOUR_STEP = /^behaviour-.+-step-(.+)$/;

export function stepActionLabel(step: ExecutionStepResult): string {
  const displayName = (step.display_name ?? "").trim();
  if (displayName) return displayName.replace(/^Step:\s*/i, "");
  const name = (step.evaluator ?? "").trim();
  const slug = name.match(BEHAVIOUR_STEP)?.[1];
  if (slug) return humanizeKey(slug);
  const segment = Array.isArray(step.segment) ? step.segment : [];
  const last = [...segment].reverse().find((s) => typeof s === "string" && s.trim());
  return typeof last === "string" ? toolActionLabel(last) : "Step";
}

export function toolActionLabel(qualname: string): string {
  const tail = qualname.split(".").filter(Boolean).at(-1) || qualname;
  return humanizeKey(tail);
}

/** A gate/cap failure row explains the score before anything else; then the
 *  outcome-role rationale; then the first scored step that wrote one. */
export function scoreRationale(steps: ExecutionStepResult[]): string {
  const scored = scoredStepResults(steps);
  const failure = scored.find((s) => s.role === "failure" && s.rationale?.trim());
  if (failure?.rationale) return failure.rationale.trim();
  const outcome = scored.find((s) => s.role === "outcome" && s.rationale?.trim());
  if (outcome?.rationale) return outcome.rationale.trim();
  return scored.find((s) => s.rationale?.trim())?.rationale?.trim() ?? "";
}

const STEP_GATE = 0.25;

function isGatedStep(step: ExecutionStepResult): boolean {
  if (step.role === "failure") return true;
  if (step.outcome !== "scored" || step.role !== "step" || typeof step.score !== "number") {
    return false;
  }
  return step.passed === false || step.score <= STEP_GATE;
}

export function gatedStep(steps: ExecutionStepResult[]): ExecutionStepResult | null {
  return steps.find(isGatedStep) ?? null;
}

export function turnFailureLine(steps: ExecutionStepResult[]): string {
  const step = gatedStep(steps);
  if (!step) return "";
  const label = stepActionLabel(step);
  const rationale = step.rationale?.trim() ?? "";
  return rationale ? `Failed: ${label}. ${rationale}` : `Failed: ${label}.`;
}

export function anchorTail(qualname: string): string {
  const parts = qualname.split(".");
  return parts.length > 1 ? parts.slice(-2).join(".") : qualname;
}

export interface TaskExecutionsListParams {
  page?: number;
  pageSize?: number;
  ordering?: string;
  project?: string;
  capability?: string;
  behaviour?: string;
  bindingSource?: TaskExecutionsListBindingSourceEnum;
  traceId?: string;
  search?: string;
  /** "conversation" paginates whole conversations server-side. */
  group?: string;
  filters?: Record<string, string | undefined>;
  enabled?: boolean;
}

function cleanFilterMap(filters?: Record<string, string | undefined>): Record<string, string> {
  const clean: Record<string, string> = {};
  if (!filters) return clean;
  for (const [k, v] of Object.entries(filters)) {
    if (v !== undefined && v !== "") clean[k] = v;
  }
  return clean;
}

export function buildTaskExecutionsListRequest(params: {
  project?: string;
  page?: number;
  pageSize?: number;
  search?: string;
  ordering?: string;
  capability?: string;
  behaviour?: string;
  bindingSource?: TaskExecutionsListBindingSourceEnum;
  traceId?: string;
  group?: string;
  filters: Record<string, string>;
}): TaskExecutionsListRequest {
  const {
    project,
    page,
    pageSize,
    search,
    ordering,
    capability,
    behaviour,
    bindingSource,
    traceId,
    group,
    filters,
  } = params;
  const request: TaskExecutionsListRequest = {
    behaviour,
    bindingSource,
    capability: capability ?? (filters.capability || undefined),
    group,
    ordering,
    page,
    pageSize,
    project,
    search,
    traceId: traceId ?? (filters.trace_id || undefined),
  };
  if (filters.model) request.model = filters.model;
  if (filters.has_model) request.hasModel = filters.has_model === "true";
  const operation = filters.operation__icontains ?? filters.operation;
  if (operation) request.operation = operation;
  const serviceName = filters.service_name__icontains ?? filters.service_name;
  if (serviceName) request.serviceName = serviceName;
  if (filters.span_type) request.spanType = filters.span_type;
  if (filters.status_code) request.statusCode = Number(filters.status_code);
  if (filters.min_duration_ms) request.minDurationMs = Number(filters.min_duration_ms);
  if (filters.max_duration_ms) request.maxDurationMs = Number(filters.max_duration_ms);
  if (filters.total_tokens__gte) request.totalTokensGte = Number(filters.total_tokens__gte);
  if (filters.total_tokens__lte) request.totalTokensLte = Number(filters.total_tokens__lte);
  if (filters.total_cost__gte) request.totalCostGte = Number(filters.total_cost__gte);
  if (filters.total_cost__lte) request.totalCostLte = Number(filters.total_cost__lte);
  if (filters.has_error) request.hasError = filters.has_error === "true";
  if (filters.status) request.status = filters.status;
  if (filters.received_at__gte) request.receivedAtGte = new Date(filters.received_at__gte);
  if (filters.received_at__lte) request.receivedAtLte = new Date(filters.received_at__lte);
  if (filters.started_at__gte) request.startedAtGte = new Date(filters.started_at__gte);
  if (filters.started_at__lte) request.startedAtLte = new Date(filters.started_at__lte);
  return request;
}

export function useTaskExecutionsList(params: TaskExecutionsListParams) {
  const {
    page = 1,
    pageSize,
    ordering,
    project,
    capability,
    behaviour,
    bindingSource,
    traceId,
    search,
    group,
    filters,
    enabled = true,
  } = params;
  const cleanFilters = cleanFilterMap(filters);
  return useQuery({
    enabled,
    queryFn: () =>
      apiClient.taskExecutions.taskExecutionsList(
        buildTaskExecutionsListRequest({
          behaviour,
          bindingSource,
          capability,
          filters: cleanFilters,
          group,
          ordering,
          page,
          pageSize,
          project,
          search,
          traceId,
        })
      ),
    queryKey: [
      "task-executions",
      page,
      pageSize,
      ordering,
      project,
      capability,
      behaviour,
      bindingSource,
      traceId,
      search,
      group,
      cleanFilters,
    ],
    refetchInterval: (i) =>
      backoffPolling(i, !i.state.data?.count || i.state.data.count <= 0 ? 5_000 : 10_000),
  });
}

export function useTraceExecutions(traceId: string | undefined, project: string | undefined) {
  const query = useTaskExecutionsList({
    enabled: !!traceId && !!project,
    pageSize: 100,
    project,
    traceId,
  });
  const rows = useMemo(() => sortExecutionsByStart(query.data?.results ?? []), [query.data]);
  return { ...query, rows };
}

export function useTaskExecutionDetail(id: string | undefined) {
  return useQuery({
    enabled: !!id,
    queryFn: () => apiClient.taskExecutions.taskExecutionsRetrieve({ id: id! }),
    queryKey: ["task-execution", id],
  });
}

/** Server-assembled thread payload: one request replaces the per-turn detail
 *  and full-trace fetches the conversation sheet used to fan out. */
export function useConversationTurns(conversationId: string | null, project: string | undefined) {
  return useQuery({
    enabled: !!conversationId,
    queryFn: () =>
      apiClient.taskExecutions.taskExecutionsConversationTurnsList({
        conversationId: conversationId!,
        project,
      }),
    queryKey: ["conversation-turns", conversationId, project],
  });
}
