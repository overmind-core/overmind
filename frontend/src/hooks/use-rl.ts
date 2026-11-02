import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import type { HyperparameterRange, RewardCheckEntry, RLModelEntry } from "@/components/rl/types";
import { notify } from "@/lib/notify";
import { backoffPolling } from "@/lib/poll";
import type { RLRunRequestRequest } from "@/openapi";

const TERMINAL = new Set(["succeeded", "failed", "cancelled"]);

export const isTerminalRLStatus = (status: string | undefined) => !!status && TERMINAL.has(status);

export const rlKeys = {
  catalog: ["rl-catalog"] as const,
  columns: (datasetId: string | undefined) => ["rl-columns", datasetId] as const,
  detail: (runId: string | undefined) => ["rl-run", runId] as const,
  list: (projectId: string | undefined) => ["rl-runs", projectId] as const,
};

export function useRLRunsQuery(projectId: string | undefined) {
  return useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.rlRuns.rlRunsList({ ordering: "-created_at", pageSize: 50, project: projectId! }),
    queryKey: rlKeys.list(projectId),
    refetchInterval: (query) =>
      backoffPolling(query, () =>
        query.state.data?.results.some((run) => !isTerminalRLStatus(run.status)) ? 4_000 : false
      ),
  });
}

export function useRLRunQuery(runId: string | undefined) {
  return useQuery({
    enabled: !!runId,
    queryFn: () => apiClient.rlRuns.rlRunsRetrieve({ id: runId! }),
    queryKey: rlKeys.detail(runId),
    refetchInterval: (query) =>
      backoffPolling(query, () => (isTerminalRLStatus(query.state.data?.status) ? false : 3_000)),
    refetchIntervalInBackground: true,
  });
}

export interface RLCatalogData {
  models: RLModelEntry[];
  checks: RewardCheckEntry[];
  hyperparameters: Record<string, HyperparameterRange>;
}

export function useRLCatalogQuery(enabled = true) {
  return useQuery({
    enabled,
    queryFn: async () => (await apiClient.rlRuns.rlRunsCatalogRetrieve()) as RLCatalogData,
    queryKey: rlKeys.catalog,
    staleTime: 5 * 60_000,
  });
}

export function useRLColumnsQuery(datasetId: string | undefined) {
  return useQuery({
    enabled: !!datasetId,
    queryFn: () => apiClient.rlRuns.rlRunsColumnsRetrieve({ dataset: datasetId! }),
    queryKey: rlKeys.columns(datasetId),
    retry: false,
  });
}

export function useCreateRLRun(projectId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: (request: RLRunRequestRequest) =>
      apiClient.rlRuns.rlRunsCreate({ rLRunRequestRequest: request }),
    onError: (error) => notify.error(error, "The RL run could not start"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: rlKeys.list(projectId) }),
  });
}

export function useStopRLRun(runId: string | undefined) {
  const queryClient = useQueryClient();
  return useMutation({
    mutationFn: () => apiClient.rlRuns.rlRunsStopCreate({ id: runId! }),
    onError: (error) => notify.error(error, "The RL run could not stop"),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: rlKeys.detail(runId) }),
  });
}
