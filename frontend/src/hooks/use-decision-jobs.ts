import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import type { DecisionJob, DecisionJobCreateRequest, DecisionRecommendRequest } from "@/openapi";

const ACTIVE = new Set(["queued", "running", "evaluating"]);

export const isActiveDecisionJob = (job: Pick<DecisionJob, "status">) => ACTIVE.has(job.status);

const keys = {
  all: ["decision-jobs"] as const,
  detail: (id: string) => ["decision-jobs", "detail", id] as const,
  list: (projectId: string, page: number, pageSize: number) =>
    ["decision-jobs", "list", projectId, page, pageSize] as const,
  models: ["decision-models"] as const,
};

export function useDecisionModelsQuery() {
  return useQuery({
    queryFn: () => apiClient.decisionModels.decisionModelsList(),
    queryKey: keys.models,
    staleTime: 5 * 60_000,
  });
}

export function useDecisionJobsQuery(projectId: string, page: number, pageSize: number) {
  return useQuery({
    queryFn: () => apiClient.decisionJobs.decisionJobsList({ page, pageSize, project: projectId }),
    queryKey: keys.list(projectId, page, pageSize),
    refetchInterval: (query) =>
      query.state.data?.results.some(isActiveDecisionJob) ? 5000 : false,
  });
}

export function useDecisionJobQuery(id: string | null) {
  return useQuery({
    enabled: !!id,
    queryFn: () => apiClient.decisionJobs.decisionJobsRetrieve({ id: id! }),
    queryKey: keys.detail(id ?? ""),
    refetchInterval: (query) =>
      query.state.data && isActiveDecisionJob(query.state.data) ? 5000 : false,
  });
}

export function useDecisionRecommendation(projectId: string, datasetId: string) {
  return useQuery({
    enabled: !!projectId && !!datasetId,
    queryFn: () =>
      apiClient.decisionJobs.decisionJobsRecommendCreate({
        decisionRecommendRequest: {
          dataset: datasetId,
          project: projectId,
        } satisfies DecisionRecommendRequest,
      }),
    queryKey: ["decision-jobs", "recommend", projectId, datasetId],
    staleTime: 60_000,
  });
}

export function useCreateDecisionJobMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (body: DecisionJobCreateRequest) =>
      apiClient.decisionJobs.decisionJobsCreate({ decisionJobCreateRequest: body }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}

export function useCancelDecisionJobMutation() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.decisionJobs.decisionJobsCancelCreate({ id }),
    onSuccess: () => qc.invalidateQueries({ queryKey: keys.all }),
  });
}
