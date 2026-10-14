import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { notify } from "@/lib/notify";
import { backoffPolling } from "@/lib/poll";
import type {
  EvalMatrixRequest,
  EvalRun,
  EvalRunsListModeEnum,
  EvalRunsListStatusEnum,
  PaginatedEvalRunList,
} from "@/openapi";

/** Cached server-side for ~1h; mirror that here. */
export const useModelCatalogQuery = (enabled = true) =>
  useQuery({
    enabled,
    queryFn: () => apiClient.models.modelsCatalogRetrieve(),
    queryKey: ["model-catalog"],
    staleTime: 60 * 60_000,
  });

export const useProjectCapabilitiesQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.capabilities.capabilitiesList({
        ordering: "name",
        pageSize: 200,
        project: projectId,
      }),
    queryKey: ["eval-capabilities", projectId],
  });

export const useProjectDatasetsForEvalQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.datasets.datasetsList({
        intent: "eval",
        pageSize: 200,
        project: projectId,
      }),
    queryKey: ["datasets", "list", projectId, "eval"],
  });

/** Project-wide rows have a null capability; a capability-scoped query returns
 *  both its own rows and the project-wide ones. */
export const useEvalMatrixQuery = (projectId: string | undefined, capabilityId?: string) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.evalMatrix.evalMatrixList({ ordering: "name", pageSize: 500, project: projectId }),
    queryKey: ["eval-matrix", projectId],
    select: (data) =>
      capabilityId
        ? data.results.filter((row) => !row.capability || row.capability === capabilityId)
        : data.results,
  });

const invalidateMatrix = (qc: ReturnType<typeof useQueryClient>) => {
  qc.invalidateQueries({ queryKey: ["eval-matrix"] });
  qc.invalidateQueries({ queryKey: ["eval-sets"] });
};

export const useCreateEvalMatrixRowMutation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: EvalMatrixRequest) =>
      apiClient.evalMatrix.evalMatrixCreate({ evalMatrixRequest: input }),
    onSuccess: () => invalidateMatrix(qc),
  });
};

export const useDeleteEvalMatrixRowMutation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.evalMatrix.evalMatrixDestroy({ id }),
    onError: (e) => notify.error(e, "Couldn't delete the row"),
    onSuccess: () => invalidateMatrix(qc),
  });
};

/** Membership-scoped server-side, with no project/capability filter — `projectId`
 *  only keys the cache; consumers narrow `.results` themselves. */
export const useEvalSetsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () => apiClient.evalSets.evalSetsList({ ordering: "name", pageSize: 200 }),
    queryKey: ["eval-sets", projectId],
  });

export const isActiveRunStatus = (status: string | undefined) =>
  status === "running" || status === "pending";

const runsPolling = (results: EvalRun[] | undefined) => {
  if (!results) return 6_000;
  return results.some((r) => isActiveRunStatus(r.status)) ? 3_500 : false;
};

/** DRF default max_page_size for eval-runs list (see PageNumberPagination). */
const EVAL_RUNS_API_PAGE_SIZE = 100;
const EVAL_RUNS_MAX_PAGES = 50;

export const fetchAllEvalRuns = async (projectId: string): Promise<PaginatedEvalRunList> => {
  const results: EvalRun[] = [];
  let page = 1;
  let count = 0;

  while (page <= EVAL_RUNS_MAX_PAGES) {
    const data = await apiClient.evalRuns.evalRunsList({
      ordering: "-created_at",
      page,
      pageSize: EVAL_RUNS_API_PAGE_SIZE,
      project: projectId,
    });
    count = data.count;
    results.push(...data.results);
    if (!data.next || results.length >= count || data.results.length === 0) break;
    page += 1;
  }

  return { count, next: null, previous: null, results };
};

export const useAllEvalRunsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () => fetchAllEvalRuns(projectId!),
    queryKey: ["eval-runs", "all", projectId],
    refetchInterval: (q) => backoffPolling(q, () => runsPolling(q.state.data?.results)),
  });

export type EvalRunsQueryParams = {
  projectId: string | undefined;
  page: number;
  pageSize: number;
  ordering?: string;
  search?: string;
  status?: EvalRunsListStatusEnum | "";
  mode?: EvalRunsListModeEnum | "";
  capability?: string;
  dataset?: string;
};

export const useEvalRunsQuery = ({
  projectId,
  page,
  pageSize,
  ordering,
  search,
  status,
  mode,
  capability,
  dataset,
}: EvalRunsQueryParams) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.evalRuns.evalRunsList({
        capability: capability || undefined,
        dataset: dataset || undefined,
        mode: mode || undefined,
        ordering: ordering || undefined,
        page,
        pageSize,
        project: projectId,
        search: search?.trim() || undefined,
        status: status || undefined,
      }),
    queryKey: [
      "eval-runs",
      projectId,
      page,
      pageSize,
      ordering ?? null,
      search?.trim() || null,
      status || null,
      mode || null,
      capability || null,
      dataset || null,
    ],
    refetchInterval: (q) => backoffPolling(q, () => runsPolling(q.state.data?.results)),
  });

/** Server-side DISTINCT over the runs, so the filter can neither offer a
 *  dataset with no runs nor omit one that has them. */
export const useEvalRunDatasetsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () => apiClient.evalRuns.evalRunsDatasetsRetrieve({ project: projectId! }),
    queryKey: ["eval-runs", "datasets", projectId],
  });

/** Every target launched together shares a group; its baseline is the run with
 *  `isBaseline`. */
export const useEvalRunGroupQuery = (projectId: string | undefined, group: string | undefined) =>
  useQuery({
    enabled: !!projectId && !!group,
    queryFn: () =>
      apiClient.evalRuns.evalRunsList({
        group,
        ordering: "created_at",
        pageSize: 50,
        project: projectId,
      }),
    queryKey: ["eval-runs", "group", projectId, group],
    refetchInterval: (q) => backoffPolling(q, () => runsPolling(q.state.data?.results)),
  });

export const useEvalRunQuery = (id: string | undefined) =>
  useQuery({
    enabled: !!id,
    queryFn: () => apiClient.evalRuns.evalRunsRetrieve({ id: id! }),
    queryKey: ["eval-run", id],
    refetchInterval: (q) =>
      backoffPolling(q, () => (isActiveRunStatus(q.state.data?.status) ? 4_000 : false)),
  });

export const useRelaunchEvalRunMutation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.evalRuns.evalRunsRunCreate({ id }),
    onSuccess: (run) => {
      qc.invalidateQueries({ queryKey: ["eval-run", run.id] });
      qc.invalidateQueries({ queryKey: ["eval-run-rows", run.id] });
      qc.invalidateQueries({ queryKey: ["eval-runs"] });
    },
  });
};

/** 404s when the run has no baseline in its group, so it never retries. */
export const useEvalRunComparisonQuery = (
  id: string | undefined,
  enabled: boolean,
  active = false
) =>
  useQuery({
    enabled: !!id && enabled,
    queryFn: () => apiClient.evalRuns.evalRunsComparisonRetrieve({ id: id! }),
    queryKey: ["eval-run-comparison", id],
    refetchInterval: (q) => backoffPolling(q, active ? 4_000 : false),
    retry: false,
  });

export const useEvalRunRowsQuery = (runId: string | undefined, active = false) =>
  useQuery({
    enabled: !!runId,
    queryFn: () =>
      apiClient.evalRunRows.evalRunRowsList({ ordering: "row_index", pageSize: 1000, run: runId }),
    queryKey: ["eval-run-rows", runId],
    refetchInterval: (q) => backoffPolling(q, active ? 4_000 : false),
  });

export const useEvalRunRowQuery = (id: number | undefined) =>
  useQuery({
    enabled: id !== undefined,
    queryFn: () => apiClient.evalRunRows.evalRunRowsRetrieve({ id: id! }),
    queryKey: ["eval-run-row", id],
  });

/** Live trace scores (`sourceType` span/trace/session) and run scores share
 *  the table; `traceId` narrows to one trace's spans. */
export const useTraceEvalScoresQuery = (
  projectId: string | undefined,
  traceId: string | undefined
) =>
  useQuery({
    enabled: !!projectId && !!traceId,
    queryFn: () =>
      apiClient.evalScores.evalScoresList({
        ordering: "-created_at",
        pageSize: 500,
        project: projectId,
        traceId,
      }),
    queryKey: ["eval-scores", "trace", projectId, traceId],
  });
