import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { backoffPolling } from "@/lib/poll";
import type { DeployedModelsListStatusEnum } from "@/openapi";

/** Lifecycle states nothing moves out of on its own. */
const SETTLED_MODEL_STATUSES = new Set(["ready", "failed", "deleted"]);

/** Stops once every model has settled: those rows then go stale until the next
 *  mount or refocus, rather than holding a permanent 10s loop per page. */
export const deployedModelsPollInterval = (models?: { status?: string }[]): number | false =>
  !models || models.some((m) => !SETTLED_MODEL_STATUSES.has(m.status ?? "")) ? 10_000 : false;

export type DeployedModelsQueryParams = {
  projectId?: string;
  /** Capability id — resolved server-side through the model's fine-tuning job. */
  capability?: string;
  page?: number;
  pageSize?: number;
  /** DRF `?search=` over model id, base model id and training-job name. */
  search?: string;
  status?: DeployedModelsListStatusEnum;
};

/** Returns the DRF page as-is — scoping is the request's job, so `count` and
 *  `results` describe the same set. */
export const useDeployedModelsQuery = ({
  projectId,
  capability,
  page,
  pageSize,
  search,
  status,
}: DeployedModelsQueryParams = {}) =>
  useQuery({
    queryFn: () =>
      apiClient.deployedModels.deployedModelsList({
        capability: capability || undefined,
        page,
        pageSize,
        project: projectId || undefined,
        search: search || undefined,
        status,
      }),
    queryKey: [
      "deployed-models",
      projectId || null,
      capability || null,
      page ?? null,
      pageSize ?? null,
      search || null,
      status ?? null,
    ],
    refetchInterval: (q) =>
      backoffPolling(q, () => deployedModelsPollInterval(q.state.data?.results)),
  });

/** Polled by default; a surface that only needs to name the deployment passes
 *  `{ poll: false }`. Same cache key either way, so the two share a result when
 *  both are mounted. */
export const useDeployedModelQuery = (id: string | undefined, { poll = true } = {}) =>
  useQuery({
    enabled: !!id,
    queryFn: () => apiClient.deployedModels.deployedModelsRetrieve({ id: id! }),
    queryKey: ["deployed-model", id],
    refetchInterval: poll ? (q) => backoffPolling(q, 10_000) : undefined,
    staleTime: poll ? undefined : 30_000,
  });

export const useModelMetricsQuery = (id: string | undefined) =>
  useQuery({
    enabled: !!id,
    queryFn: () => apiClient.deployedModels.deployedModelsMetricsRetrieve({ id: id! }),
    queryKey: ["model-metrics", id],
    refetchInterval: (q) => backoffPolling(q, 15_000),
  });

export const useModelActivityQuery = (
  id: string | undefined,
  granularity: "minute" | "hour" | "day" = "minute"
) =>
  useQuery({
    enabled: !!id,
    queryFn: () =>
      apiClient.deployedModels.deployedModelsActivityRetrieve({ granularity, id: id! }),
    queryKey: ["model-activity", id, granularity],
    refetchInterval: (q) => backoffPolling(q, 30_000),
  });

/** Checkpoint archive lives in S3 — only fetch when the tab is opened. */
export const useModelCheckpointsQuery = (id: string | undefined, enabled = false) =>
  useQuery({
    enabled: !!id && enabled,
    queryFn: () => apiClient.deployedModels.deployedModelsCheckpointsRetrieve({ id: id! }),
    queryKey: ["model-checkpoints", id],
    retry: false,
  });

/** Navigates to the presigned S3 URL rather than blob-fetching the
 * `checkpoints/download` redirect: reading a cross-origin response body needs
 * S3 bucket CORS for this origin, a plain file download does not. */
export async function downloadModelWeightsZip(id: string, filename: string): Promise<void> {
  const { files } = await apiClient.deployedModels.deployedModelsCheckpointsRetrieve({ id });
  const file = files[0];
  if (!file?.downloadUrl) {
    throw new Error("No checkpoint archive available for this model.");
  }
  const a = document.createElement("a");
  a.href = file.downloadUrl;
  a.download = filename;
  a.rel = "noopener";
  document.body.appendChild(a);
  a.click();
  a.remove();
}

/** Undeploys and marks the model deleting; it is not removed. */
export const useDeleteModelMutation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.deployedModels.deployedModelsDestroy({ id }),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["deployed-models"] });
      qc.invalidateQueries({ queryKey: ["deployed-model", id] });
    },
  });
};

/** The backend accepts this only for FAILED or DELETED models that came from a
 * finetuning job, and restarts from the checkpoint download. */
export const useRetryModelMutation = () => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (id: string) => apiClient.deployedModels.deployedModelsRetryCreate({ id }),
    onSuccess: (_data, id) => {
      qc.invalidateQueries({ queryKey: ["deployed-models"] });
      qc.invalidateQueries({ queryKey: ["deployed-model", id] });
    },
  });
};

/** Live Modal stats. `pollMs` = 0 → one-shot fetch; > 0 → poll. */
export const useModelLiveQuery = (id: string | undefined, enabled = false, pollMs = 5_000) =>
  useQuery({
    enabled: !!id && enabled,
    queryFn: () => apiClient.deployedModels.deployedModelsLiveRetrieve({ id: id! }),
    queryKey: ["model-live", id],
    refetchInterval: (q) => backoffPolling(q, enabled && pollMs ? pollMs : false),
    staleTime: pollMs ? 0 : 30_000,
  });
