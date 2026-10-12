import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { notify } from "@/lib/notify";
import { backoffPolling } from "@/lib/poll";
import type {
  AuthorJudgeEvaluatorRequestRequest,
  EvalRunList,
  EvalRunsListStatusEnum,
  EvalSetAddMembersRequestRequest,
  EvalSetRequest,
  GenerateEvaluatorPromptRequestRequest,
  PaginatedEvalRunListList,
  PatchedEvalSetMemberUpdateRequest,
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

/** Unpaginated — a flat array, not a DRF page. Internal sentinel evaluators are
 *  excluded server-side. */
export const useEvaluatorCatalogQuery = (
  projectId: string | undefined,
  capabilityId?: string | undefined
) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.evaluators.evaluatorsCatalogList({ capability: capabilityId, project: projectId }),
    queryKey: ["evaluator-catalog", projectId, capabilityId ?? null],
  });

/** One series per evaluator, `points` ordered oldest→newest with scores
 *  normalised to 0–100. The whole history arrives once; charts window it. */
export const useEvaluatorScoreHistoryQuery = (capabilityId: string | undefined) =>
  useQuery({
    enabled: !!capabilityId,
    queryFn: () => apiClient.evaluators.evaluatorsScoreHistoryList({ capability: capabilityId! }),
    queryKey: ["evaluator-score-history", capabilityId],
  });

/** The list endpoint serves a slim EvaluatorList without
 *  config/checklist/rubric; this fetches the full row on demand. */
export const useEvaluatorDetailQuery = (id: string | undefined, enabled: boolean) =>
  useQuery({
    enabled: !!id && enabled,
    queryFn: () => apiClient.evaluators.evaluatorsRetrieve({ id: id! }),
    queryKey: ["evaluator", id],
    staleTime: 60_000,
  });

export const useAuthorJudgeEvaluatorMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: AuthorJudgeEvaluatorRequestRequest) =>
      apiClient.evaluators.evaluatorsAuthorCreate({
        authorJudgeEvaluatorRequestRequest: input,
      }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["evaluators", projectId] });
      qc.invalidateQueries({ queryKey: ["evaluator-catalog", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-sets"] });
    },
  });
};

export const useEditJudgeEvaluatorMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: ({ id, input }: { id: string; input: AuthorJudgeEvaluatorRequestRequest }) =>
      apiClient.evaluators.evaluatorsAuthorUpdate({
        authorJudgeEvaluatorRequestRequest: input,
        id,
      }),
    onSuccess: (_data, { id }) => {
      qc.invalidateQueries({ queryKey: ["evaluators", projectId] });
      qc.invalidateQueries({ queryKey: ["evaluator-catalog", projectId] });
      qc.invalidateQueries({ queryKey: ["evaluator", id] });
      // Eval-set detail embeds its members, so refresh sets + set detail too.
      qc.invalidateQueries({ queryKey: ["eval-sets"] });
      qc.invalidateQueries({ queryKey: ["eval-set"] });
    },
  });
};

export const useGenerateEvaluatorPromptMutation = () =>
  useMutation({
    mutationFn: (input: GenerateEvaluatorPromptRequestRequest) =>
      apiClient.evaluators.evaluatorsGeneratePromptCreate({
        generateEvaluatorPromptRequestRequest: input,
      }),
  });

/** Membership-scoped server-side, with no project/capability filter — `projectId`
 *  only keys the cache; consumers narrow `.results` themselves. */
export const useEvalSetsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () => apiClient.evalSets.evalSetsList({ ordering: "name", pageSize: 200 }),
    queryKey: ["eval-sets", projectId],
  });

export const useCreateEvalSetMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: EvalSetRequest) =>
      apiClient.evalSets.evalSetsCreate({ evalSetRequest: input }),
    onSuccess: () => qc.invalidateQueries({ queryKey: ["eval-sets", projectId] }),
  });
};

/** The backend re-points the owning capability's `active_eval_set` first, so the
 *  capability detail goes stale too. */
export const useDeleteEvalSetMutation = (
  projectId: string | undefined,
  capabilityId?: string | undefined
) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (setId: string) => apiClient.evalSets.evalSetsDestroy({ id: setId }),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval-sets", projectId] });
      if (capabilityId) qc.invalidateQueries({ queryKey: ["capability-detail", capabilityId] });
    },
  });
};

export const useAddEvalSetMembersMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { setId: string; body: EvalSetAddMembersRequestRequest }) =>
      apiClient.evalSets.evalSetsMembersCreate({
        evalSetAddMembersRequestRequest: input.body,
        id: input.setId,
      }),
    onSuccess: (_d, input) => {
      qc.invalidateQueries({ queryKey: ["eval-sets", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-set", input.setId] });
    },
  });
};

export const useUpdateEvalSetMemberMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: {
      setId: string;
      memberId: string;
      body: PatchedEvalSetMemberUpdateRequest;
    }) =>
      apiClient.evalSets.evalSetsMembersPartialUpdate({
        id: input.setId,
        memberId: input.memberId,
        patchedEvalSetMemberUpdateRequest: input.body,
      }),
    onSuccess: (_d, input) => {
      qc.invalidateQueries({ queryKey: ["eval-sets", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-set", input.setId] });
    },
  });
};

export const useRemoveEvalSetMemberMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (input: { setId: string; memberId: string }) =>
      apiClient.evalSets.evalSetsMembersDestroy({ id: input.setId, memberId: input.memberId }),
    onSuccess: (_d, input) => {
      qc.invalidateQueries({ queryKey: ["eval-sets", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-set", input.setId] });
    },
  });
};

/** Point the owning capability's `active_eval_set` at this set. */
export const useActivateEvalSetMutation = (
  projectId: string | undefined,
  capabilityId: string | undefined
) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (setId: string) => apiClient.evalSets.evalSetsActivateCreate({ id: setId }),
    onSuccess: (_d, setId) => {
      qc.invalidateQueries({ queryKey: ["eval-sets", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-set", setId] });
      qc.invalidateQueries({ queryKey: ["capability-detail", capabilityId] });
    },
  });
};

const isActiveRunStatus = (status: string | undefined) =>
  status === "running" || status === "pending";

/** DRF default max_page_size for eval-runs list (see PageNumberPagination). */
const EVAL_RUNS_API_PAGE_SIZE = 100;
const EVAL_RUNS_MAX_PAGES = 50;

export const fetchAllEvalRuns = async (projectId: string): Promise<PaginatedEvalRunListList> => {
  const results: EvalRunList[] = [];
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
    refetchInterval: (q) =>
      backoffPolling(q, () => {
        const results = q.state.data?.results;
        if (!results) return 6_000;
        return results.some((r) => isActiveRunStatus(r.status)) ? 3_500 : false;
      }),
  });

export type EvalRunsQueryParams = {
  projectId: string | undefined;
  page: number;
  pageSize: number;
  ordering?: string;
  search?: string;
  status?: EvalRunsListStatusEnum | "";
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
  capability,
  dataset,
}: EvalRunsQueryParams) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.evalRuns.evalRunsList({
        capability: capability || undefined,
        dataset: dataset || undefined,
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
      capability || null,
      dataset || null,
    ],
    refetchInterval: (q) =>
      backoffPolling(q, () => {
        const results = q.state.data?.results;
        if (!results) return 6_000;
        return results.some((r) => isActiveRunStatus(r.status)) ? 3_500 : false;
      }),
  });

/** Server-side DISTINCT over the runs, so the filter can neither offer a
 *  dataset with no runs nor omit one that has them. */
export const useEvalRunDatasetsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () => apiClient.evalRuns.evalRunsDatasetsRetrieve({ project: projectId! }),
    queryKey: ["eval-runs", "datasets", projectId],
  });

/** Runs of the same dataset, newest first. Capability-mode runs (null dataset) fall
 *  back to the project-wide list — the run detail exposes no capability id to match
 *  on. */
export const useComparableEvalRunsQuery = (
  projectId: string | undefined,
  datasetId: string | null | undefined
) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.evalRuns.evalRunsList({
        dataset: datasetId ?? undefined,
        ordering: "-created_at",
        pageSize: 100,
        project: projectId,
      }),
    queryKey: ["eval-runs-comparable", projectId, datasetId ?? null],
    staleTime: 30_000,
  });

export const useEvalRunQuery = (id: string | undefined) =>
  useQuery({
    enabled: !!id,
    queryFn: () => apiClient.evalRuns.evalRunsRetrieve({ id: id! }),
    queryKey: ["eval-run", id],
    refetchInterval: (q) =>
      backoffPolling(q, () => {
        const status = (q.state.data as { status?: string } | undefined)?.status;
        return status === "completed" || status === "failed" || status === "cancelled"
          ? false
          : 5_000;
      }),
  });

export const useEvalRunComparisonQuery = (id: string | undefined, status?: string) =>
  useQuery({
    enabled: !!id,
    queryFn: () => apiClient.evalRuns.evalRunsComparisonRetrieve({ id: id! }),
    queryKey: ["eval-run-comparison", id],
    refetchInterval: (q) =>
      backoffPolling(q, status === "running" || status === "pending" ? 4_000 : false),
  });

export const useRelaunchEvalRunMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.evalRuns.evalRunsRunCreate({ id }),
    onError: (e) => notify.error(e, "Couldn't re-launch the run"),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["eval-runs", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-runs", "all", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-run", id] });
    },
  });
};

export const useCancelEvalRunMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.evalRuns.evalRunsCancelCreate({ id }),
    onError: (e) => notify.error(e, "Couldn't cancel the run"),
    onSuccess: (_d, id) => {
      qc.invalidateQueries({ queryKey: ["eval-runs", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-runs", "all", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-run", id] });
    },
  });
};

export const useDeleteEvalRunMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.evalRuns.evalRunsDestroy({ id }),
    onError: (e) => notify.error(e, "Couldn't delete the run"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["eval-runs", projectId] });
      qc.invalidateQueries({ queryKey: ["eval-runs", "all", projectId] });
    },
  });
};

export const useDeleteEvaluatorMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.evaluators.evaluatorsDestroy({ id }),
    onError: (e) => notify.error(e, "Couldn't delete the evaluator"),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ["evaluators", projectId] });
      qc.invalidateQueries({ queryKey: ["evaluator-catalog", projectId] });
      // Deleting cascades any eval-set memberships, so set detail/member lists go stale too.
      qc.invalidateQueries({ queryKey: ["eval-sets"] });
      qc.invalidateQueries({ queryKey: ["eval-set"] });
      // Bare (no id) so this matches every capabilityId/behaviourId variant — the
      // Task Eval Library's per-capability and per-behaviour evaluator lists.
      qc.invalidateQueries({ queryKey: ["capability-evaluators"] });
      qc.invalidateQueries({ queryKey: ["behaviour-evaluators"] });
      qc.invalidateQueries({ queryKey: ["capability-detail"] });
    },
  });
};

export const useEvalSamplesQuery = (runId: string | undefined, isRunActive = false) =>
  useQuery({
    enabled: !!runId,
    queryFn: () => apiClient.evalSamples.evalSamplesList({ pageSize: 1000, run: runId }),
    queryKey: ["eval-samples", runId],
    refetchInterval: (q) => backoffPolling(q, isRunActive ? 4_000 : false),
  });

export const useEvalSampleQuery = (id: string | undefined) =>
  useQuery({
    enabled: !!id,
    queryFn: () => apiClient.evalSamples.evalSamplesRetrieve({ id: id! }),
    queryKey: ["eval-sample", id],
  });

export const useEvalScoresQuery = (
  params: { runId?: string; sampleId?: string },
  isRunActive = false
) =>
  useQuery({
    enabled: !!(params.runId || params.sampleId),
    queryFn: () =>
      apiClient.evalScores.evalScoresList({
        pageSize: 2000,
        run: params.runId,
        sample: params.sampleId,
      }),
    queryKey: ["eval-scores", params.runId, params.sampleId],
    refetchInterval: (q) => backoffPolling(q, isRunActive ? 5_000 : false),
  });
