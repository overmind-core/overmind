/** Every dataset query and mutation. Nested JSON blobs (reports, rows, chat)
 *  come back verbatim from the server, so their keys stay snake_case here. */

import { useEffect, useRef } from "react";

import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient, { fetchWithAuth } from "@/client";
import { notify } from "@/lib/notify";
import type {
  Cell,
  ColumnStat,
  Dataset,
  DatasetPair,
  IntentEnum,
  PaginatedDatasetList,
  PositionEnum,
  SourceRequest,
} from "@/openapi";

export type Intent = IntentEnum;
export type CellState = "queued" | "running" | "ok" | "failed";

export interface ColumnInfo {
  name: string;
  type: string;
  null_rate?: number;
}

export interface CapabilityRank {
  capability_id: string;
  name: string;
  score: number;
  reason: string;
}

/** A page of the grid: `marks` says which rows are new and which values changed. */
export interface RowsPage {
  rows: Array<Record<string, unknown> & { _index: number }>;
  total: number;
  columns: ColumnInfo[];
  offset: number;
  limit: number;
  marks: Record<string, { added?: boolean; before?: Record<string, unknown> }>;
}

export interface RowFilter {
  field: string;
  op: "contains" | "not_contains" | "equals" | "not_equals" | "empty" | "not_empty";
  value?: string;
}

export interface RowsParams {
  offset: number;
  limit: number;
  sort?: string;
  dir?: "asc" | "desc";
  filters?: RowFilter[];
  search?: string;
  diff?: boolean;
}

export type DatasetEvent =
  | { type: "replay.done" }
  | { type: "land_started" | "land_progress" | "land_done" | "land_failed"; [k: string]: unknown }
  | { type: "run_started" | "run_done" | "run_failed"; cell_id?: string; error?: string }
  | {
      type: "cell_started" | "cell_done" | "cell_failed";
      cell_id: string;
      version?: string;
      state?: CellState;
      rows?: number;
      error?: string;
      cached?: boolean;
    }
  | { type: "dataset_changed" };

/** The query that produced a traces selection, not the ids it matched. */
export interface TraceSelectionSpec {
  filters?: Record<string, string | undefined>;
  search?: string;
  ordering?: string;
  excludeTraceIds?: string[];
  limit?: number | null;
}

/** Wire form (snake_case) — the server reads it as-is. */
export function traceSelectionBody(selection: TraceSelectionSpec) {
  const filters: Record<string, string> = {};
  for (const [key, value] of Object.entries(selection.filters ?? {})) {
    if (value !== undefined && value !== "") filters[key] = value;
  }
  return {
    exclude_trace_ids: selection.excludeTraceIds ?? [],
    filters,
    limit: selection.limit ?? null,
    ordering: selection.ordering ?? "",
    search: selection.search ?? "",
  };
}

export const INTENT_LABEL: Record<string, string> = {
  eval: "Eval",
  explore: "Data exploration",
  pending: "Pending",
  train: "Train",
};

export const intentOf = (dataset: Dataset | null | undefined): Intent =>
  (dataset?.intent ?? "pending") as Intent;

export const cellsOf = (dataset: Dataset | null | undefined): Cell[] =>
  Array.isArray(dataset?.cells) ? (dataset.cells as Cell[]) : [];

export const isVisibleDatasetColumn = (column: { name: string }): boolean =>
  column.name !== "source_row" &&
  column.name !== "_overmind_provenance" &&
  column.name !== "_overmind_document_id";

export const columnsOf = (cell: Cell | null | undefined): ColumnInfo[] =>
  Array.isArray(cell?.columns) ? (cell.columns as ColumnInfo[]).filter(isVisibleDatasetColumn) : [];

export const fitOf = (cell: Cell | null | undefined): { ok: boolean; reason: string } =>
  cell?.fits && typeof cell.fits === "object"
    ? (cell.fits as { ok: boolean; reason: string })
    : { ok: false, reason: "" };

export const rankOf = (dataset: Dataset | null | undefined): CapabilityRank[] =>
  Array.isArray(dataset?.capabilityRank) ? (dataset.capabilityRank as CapabilityRank[]) : [];

export const activeCellOf = (dataset: Dataset | null | undefined): Cell | null => {
  const ran = cellsOf(dataset).filter((c) => c.state === "ok" && c.fingerprint);
  if (dataset?.active) {
    const chosen = ran.find((c) => c.id === dataset.active);
    if (chosen) return chosen;
  }
  return ran.at(-1) ?? null;
};

export const isBusy = (ds: Dataset | null | undefined): boolean =>
  !!ds && (ds.state === "running" || ds.state === "landing");

/** The used-version block consumers carry (`cell_info`). */
export interface UsedVersionInfo {
  id: string;
  version: string;
  title: string;
  rows: number;
  datasetId: string;
}

export const usedVersionOf = (raw: unknown): UsedVersionInfo | null => {
  if (!raw || typeof raw !== "object" || !("id" in raw)) return null;
  const r = raw as Record<string, unknown>;
  return {
    datasetId: String(r.dataset_id ?? ""),
    id: String(r.id),
    rows: Number(r.rows ?? 0),
    title: String(r.title ?? ""),
    version: String(r.version ?? ""),
  };
};

export const datasetDisplayName = (ds: { name?: string | null; id: string }): string =>
  ds.name?.trim() || `Dataset ${ds.id.slice(0, 8)}`;

const datasetKeys = {
  all: ["datasets"] as const,
  columns: (id: string, cell: string | undefined) => ["datasets", id, "columns", cell] as const,
  detail: (id: string) => ["datasets", "detail", id] as const,
  list: (projectId: string | undefined, filters: DatasetListFilters) =>
    ["datasets", "list", projectId, filters] as const,
  rows: (id: string, cell: string | undefined, params: RowsParams) =>
    ["datasets", id, "rows", cell, params] as const,
  usage: (id: string, cell: string) => ["datasets", id, "usage", cell] as const,
};

export function invalidateDataset(qc: ReturnType<typeof useQueryClient>, id?: string) {
  if (id) void qc.invalidateQueries({ queryKey: ["datasets", id] });
  if (id) void qc.invalidateQueries({ queryKey: datasetKeys.detail(id) });
  void qc.invalidateQueries({ queryKey: ["datasets", "list"] });
  void qc.invalidateQueries({ queryKey: ["capability-detail"] });
}

export interface DatasetListFilters {
  capability?: string;
  intent?: string;
  search?: string;
  ordering?: string;
  page?: number;
  pageSize?: number;
}

export function useDatasetsQuery(projectId: string | undefined, filters: DatasetListFilters = {}) {
  return useQuery<PaginatedDatasetList>({
    enabled: !!projectId,
    placeholderData: keepPreviousData,
    queryFn: () =>
      apiClient.datasets.datasetsList({
        capability: filters.capability || undefined,
        intent: filters.intent || undefined,
        ordering: filters.ordering || undefined,
        page: filters.page,
        pageSize: filters.pageSize ?? 100,
        project: projectId!,
        search: filters.search || undefined,
      }),
    queryKey: datasetKeys.list(projectId, filters),
  });
}

export async function fetchProjectDatasets(projectId: string, signal?: AbortSignal) {
  const datasets = new Map<string, Dataset>();
  for (let page = 1; ; page += 1) {
    const result = await apiClient.datasets.datasetsList(
      { page, pageSize: 100, project: projectId },
      { signal }
    );
    for (const dataset of result.results) datasets.set(dataset.id, dataset);
    if (!result.next) return [...datasets.values()];
  }
}

export function useProjectDatasetsQuery(projectId: string | undefined) {
  return useQuery({
    enabled: !!projectId,
    queryFn: ({ signal }) => fetchProjectDatasets(projectId!, signal),
    queryKey: ["datasets", "list", projectId, "inventory"],
    refetchInterval: (query) => (query.state.data?.some(isBusy) ? 3000 : 15000),
  });
}

/** Polls while the dataset is busy; the SSE stream fills the gaps. */
export function useDatasetQuery(id: string | undefined) {
  return useQuery<Dataset>({
    enabled: !!id,
    placeholderData: keepPreviousData,
    queryFn: () => apiClient.datasets.datasetsRetrieve({ id: id! }),
    queryKey: datasetKeys.detail(id ?? ""),
    refetchInterval: (query) => (isBusy(query.state.data) ? 3000 : false),
  });
}

/** Raw fetch: rows carry user column names as keys, which the generated client
 *  would camelize. */
export function useRowsQuery(
  id: string | undefined,
  cell: string | undefined,
  params: RowsParams,
  enabled = true
) {
  return useQuery<RowsPage>({
    enabled: !!id && enabled,
    placeholderData: keepPreviousData,
    queryFn: async () => {
      const search = new URLSearchParams({
        limit: String(params.limit),
        offset: String(params.offset),
      });
      if (cell) search.set("cell", cell);
      if (params.sort) search.set("sort", params.sort);
      if (params.dir) search.set("dir", params.dir);
      if (params.filters?.length) search.set("filters", JSON.stringify(params.filters));
      if (params.search) search.set("search", params.search);
      if (params.diff) search.set("diff", "1");
      const res = await fetchWithAuth(`/api/datasets/${id}/rows/?${search}`);
      if (!res.ok) {
        const detail = (await res.json().catch(() => null))?.detail;
        throw new Error(typeof detail === "string" ? detail : `Rows failed (HTTP ${res.status}).`);
      }
      return (await res.json()) as RowsPage;
    },
    queryKey: datasetKeys.rows(id ?? "", cell, params),
    retry: false,
  });
}

export function useColumnsQuery(id: string | undefined, cell: string | undefined, enabled = true) {
  return useQuery<ColumnStat[]>({
    enabled: !!id && enabled,
    queryFn: () => apiClient.datasets.datasetsColumnsList({ cell, id: id! }),
    queryKey: datasetKeys.columns(id ?? "", cell),
    retry: false,
  });
}

export interface CreateDatasetInput {
  projectId: string;
  name: string;
  brief?: string;
  intent?: Intent;
  capabilityId?: string | null;
  source?: SourceRequest;
}

export function useCreateDatasetMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: CreateDatasetInput) =>
      apiClient.datasets.datasetsCreate({
        datasetCreateRequest: {
          brief: input.brief,
          capability: input.capabilityId,
          intent: input.intent,
          name: input.name,
          project: input.projectId,
          source: input.source,
        },
      }),
    onSuccess: () => invalidateDataset(qc),
  });
}

export type SplitPosition = PositionEnum;

export interface CreateDatasetSplitInput {
  projectId: string;
  name: string;
  brief?: string;
  capabilityId?: string | null;
  source: SourceRequest;
  evalPercent: number;
  position: SplitPosition;
  groupBy?: string[];
  stratifyBy?: string;
  deduplicate?: boolean;
}

/** One source landed as `<name> train` and `<name> eval`. */
export function useCreateDatasetSplitMutation() {
  const qc = useQueryClient();
  return useMutation<DatasetPair, Error, CreateDatasetSplitInput>({
    mutationFn: (input) =>
      apiClient.datasets.datasetsSplitCreate({
        datasetSplitCreateRequest: {
          brief: input.brief,
          capability: input.capabilityId,
          deduplicate: input.deduplicate,
          evalPercent: input.evalPercent,
          groupBy: input.groupBy,
          name: input.name,
          position: input.position,
          project: input.projectId,
          source: input.source,
          stratifyBy: input.stratifyBy,
        },
      }),
    onSuccess: () => invalidateDataset(qc),
  });
}

export function useAttachDatasetSourceMutation(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (sourceRequest: SourceRequest) =>
      apiClient.datasets.datasetsSourceCreate({ id, sourceRequest }),
    onError: (err) => notify.error(err, "Couldn't attach the source"),
    onSuccess: () => invalidateDataset(qc, id),
  });
}

export interface DatasetPatch {
  name?: string;
  capability?: string | null;
  intent?: Intent;
  active?: string | null;
}

export function usePatchDatasetMutation(id: string) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (patch: DatasetPatch) =>
      apiClient.datasets.datasetsPartialUpdate({ id, patchedDatasetRequest: patch }),
    onError: (e) => notify.error(e, "Couldn't update dataset"),
    onSuccess: () => invalidateDataset(qc, id),
  });
}

export function useDeleteDatasetMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.datasets.datasetsDestroy({ id }),
    onError: (e) => notify.error(e, "Couldn't delete dataset"),
    onSuccess: () => invalidateDataset(qc),
  });
}

export async function downloadExport(
  id: string,
  cell: string | undefined,
  fmt: "jsonl" | "csv",
  filename: string
) {
  const params = new URLSearchParams({ fmt });
  if (cell) params.set("cell", cell);
  const res = await fetchWithAuth(`/api/datasets/${id}/export/?${params}`);
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail;
    throw new Error(typeof detail === "string" ? detail : `Export failed (HTTP ${res.status}).`);
  }
  const blob = await res.blob();
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${filename}.${fmt}`;
  a.click();
  URL.revokeObjectURL(url);
}

/** Fetch-based SSE (EventSource cannot send an Authorization header). Replays the
 *  backlog on every connect, then streams; reconnects after a pause. */
export function useDatasetEvents(
  datasetId: string | undefined,
  onEvent: (event: DatasetEvent, meta: { live: boolean }) => void
) {
  const onEventRef = useRef(onEvent);
  useEffect(() => {
    onEventRef.current = onEvent;
  });

  useEffect(() => {
    if (!datasetId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let controller: AbortController | null = null;
    let live = false;

    const consume = (line: string) => {
      if (!line.startsWith("data: ")) return;
      try {
        const event = JSON.parse(line.slice(6)) as DatasetEvent;
        if (event.type === "replay.done") live = true;
        onEventRef.current(event, { live });
      } catch {
        // A malformed frame carries nothing worth surfacing.
      }
    };

    const connect = async () => {
      if (cancelled) return;
      live = false;
      controller = new AbortController();
      let buffer = "";
      try {
        const res = await fetchWithAuth(`/api/datasets/${datasetId}/events/`, {
          signal: controller.signal,
        });
        if (!res.ok || !res.body) throw new Error(`events ${res.status}`);
        const reader = res.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split("\n");
          buffer = lines.pop() ?? "";
          for (const line of lines) consume(line.trimEnd());
        }
      } catch {
        // Aborted or dropped — reconnect below.
      }
      if (!cancelled) timer = setTimeout(connect, 3_000);
    };

    void connect();
    return () => {
      cancelled = true;
      controller?.abort();
      if (timer) clearTimeout(timer);
    };
  }, [datasetId]);
}

export const useCancelDatasetMutation = (id: string) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.datasets.datasetsCancelCreate({ id }),
    onError: (error) => notify.error(error, "Cancellation request failed"),
    onSuccess: () => invalidateDataset(qc, id),
  });
};
