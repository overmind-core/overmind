import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import apiClient from "@/client";
import { notify } from "@/lib/notify";
import { backoffPolling } from "@/lib/poll";
import { isTerminalFinetuningStatus } from "@/lib/running-jobs";
import type {
  DatasetValidateRequestRequest,
  FinetuningJobList,
  FinetuningJobRequest,
  FinetuningJobsRunsListStatusEnum,
  FinetuningModelCatalogEntry,
  FinetuningModelCatalogResponse,
} from "@/openapi";

export interface ValidateDatasetParams {
  datasetId: string;
  validationEnabled?: boolean;
  validationSplitRatio?: number;
  validationDatasetId?: string | null;
  splitMethod?: string;
}

export const useTrainingBenchmarksQuery = (projectId: string) =>
  useQuery({
    enabled: !!projectId,
    queryFn: async () => {
      const models = [];
      for (let page = 1; ; page += 1) {
        const response = await apiClient.deployedModels.deployedModelsList({
          page,
          pageSize: 100,
          project: projectId,
          status: "ready",
        });
        models.push(
          ...response.results.filter((model) => model.finetuningJobId && model.status === "ready")
        );
        if (!response.next) return models;
      }
    },
    queryKey: ["training-benchmarks", projectId],
    refetchInterval: 10_000,
  });

/** Stored as `base · dataset · capability`; pages strip the base and capability
 *  segments, which have columns of their own. */
export function defaultFinetuneName({
  baseModel,
  datasetName,
  capabilityName,
}: {
  baseModel?: string | null;
  datasetName?: string | null;
  capabilityName?: string | null;
}): string {
  return [baseModel, datasetName, capabilityName]
    .map((p) => (typeof p === "string" ? p.trim() : ""))
    .filter(Boolean)
    .join(" · ");
}

/** Derived from the cached data alone, so every observer of the key agrees. */
export function finetuningJobsPollInterval(
  results: Array<{ status: string }> | undefined
): number | false {
  if (!results) return 5_000;
  return results.some((j) => !isTerminalFinetuningStatus(j.status)) ? 5_000 : false;
}

export const useFinetuningJobsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsList({
        ordering: "-created_at",
        project: projectId,
      }),
    queryKey: ["finetuning-jobs", projectId],
    refetchInterval: (q) =>
      backoffPolling(q, () => finetuningJobsPollInterval(q.state.data?.results)),
  });

export interface FinetuningRunsQueryParams {
  projectId: string | undefined;
  page: number;
  pageSize: number;
  search?: string;
  status?: FinetuningJobsRunsListStatusEnum;
  dataset?: string;
  baseModel?: string;
}

/** Each run carries its complete job set, so per-run aggregates cover the whole
 *  run; `count` counts runs. Rows are always newest-first — the endpoint
 *  accepts `?ordering=` and ignores it, so none is sent. */
export const useFinetuningRunsQuery = ({
  projectId,
  page,
  pageSize,
  search,
  status,
  dataset,
  baseModel,
}: FinetuningRunsQueryParams) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsRunsList({
        baseModel: baseModel || undefined,
        dataset: dataset || undefined,
        page,
        pageSize,
        project: projectId,
        search: search?.trim() || undefined,
        status: status || undefined,
      }),
    queryKey: [
      "finetuning-runs",
      projectId,
      page,
      pageSize,
      search?.trim() || null,
      status || null,
      dataset || null,
      baseModel || null,
    ],
    refetchInterval: (q) =>
      backoffPolling(q, () =>
        finetuningJobsPollInterval(q.state.data?.results.flatMap((r) => r.jobs))
      ),
  });

/**
 * The URL's run key is `group_id ?? job.id`, and `?group_id=` only matches the
 * former, so a groupless run is fetched by its lone job's id instead.
 *
 * Not `useFinetuningJobsQuery`: that key carries the history's page and
 * filters, so sharing it would let the monitor render a filtered page while
 * believing it holds the whole run.
 */
export const useFinetuningRunJobsQuery = (runId: string | undefined) =>
  useQuery({
    enabled: !!runId,
    queryFn: async () => {
      const page = await apiClient.finetuningJobs.finetuningJobsList({
        groupId: runId!,
        ordering: "-created_at",
      });
      if (page.results.length > 0) return page.results;
      const job = await apiClient.finetuningJobs.finetuningJobsRetrieve({ id: runId! });
      return [job as unknown as FinetuningJobList];
    },
    queryKey: ["finetuning-run-jobs", runId],
    refetchInterval: (q) => backoffPolling(q, () => finetuningJobsPollInterval(q.state.data)),
  });

/** Options come off the jobs themselves, so every option yields rows. Not
 *  cross-filtered: picking a dataset must not empty the model list. */
export const useFinetuningRunDatasetsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () => apiClient.finetuningJobs.finetuningJobsDatasetsRetrieve({ project: projectId! }),
    queryKey: ["finetuning-jobs", "datasets", projectId],
  });

export const useFinetuningRunBaseModelsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsBaseModelsRetrieve({ project: projectId! }),
    queryKey: ["finetuning-jobs", "base-models", projectId],
  });

export const useFinetuningJobQuery = (jobId: string | undefined) =>
  useQuery({
    enabled: !!jobId,
    queryFn: () => apiClient.finetuningJobs.finetuningJobsRetrieve({ id: jobId! }),
    queryKey: ["finetuning-job", jobId],
    refetchInterval: (q) => backoffPolling(q, 3_000),
    refetchIntervalInBackground: true,
  });

export const useProjectDatasetsQuery = (projectId: string | undefined) =>
  useQuery({
    enabled: !!projectId,
    queryFn: () =>
      apiClient.datasets.datasetsList({
        intent: "train",
        pageSize: 200,
        project: projectId,
      }),
    queryKey: ["datasets", "list", projectId, "train"],
  });

export const useCreateFinetuningJobsMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (inputs: FinetuningJobRequest[]) => {
      const created = [];
      for (const input of inputs) {
        created.push(
          await apiClient.finetuningJobs.finetuningJobsCreate({ finetuningJobRequest: input })
        );
      }
      return created;
    },
    onError: (e) => notify.error(e, "Couldn't create fine-tuning jobs"),
    onSuccess: (created) => {
      qc.invalidateQueries({ queryKey: ["finetuning-jobs", projectId] });
      const n = created.length;
      notify.success(n === 1 ? "Fine-tuning job created" : `Started ${n} fine-tuning jobs`);
    },
  });
};

export const useCancelFinetuningJobMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      apiClient.finetuningJobs.finetuningJobsCancelCreate({ id: jobId }),
    onError: (e) => notify.error(e, "Couldn't cancel job"),
    onSuccess: (_d, jobId) => {
      qc.invalidateQueries({ queryKey: ["finetuning-jobs", projectId] });
      qc.invalidateQueries({ queryKey: ["finetuning-job", jobId] });
      notify.success("Job cancelled");
    },
  });
};

export const useRetryFinetuningJobMutation = (projectId: string | undefined) => {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: (jobId: string) =>
      apiClient.finetuningJobs.finetuningJobsRetryCreate({ id: jobId }),
    onError: (e) => notify.error(e, "Couldn't restart job"),
    onSuccess: (_d, jobId) => {
      qc.invalidateQueries({ queryKey: ["finetuning-jobs", projectId] });
      qc.invalidateQueries({ queryKey: ["finetuning-job", jobId] });
      notify.success("Job restarted");
    },
  });
};

export const useValidateDatasetMutation = () =>
  useMutation({
    mutationFn: (params: ValidateDatasetParams | string) => {
      const body: DatasetValidateRequestRequest =
        typeof params === "string"
          ? { datasetId: params }
          : {
              datasetId: params.datasetId,
              splitMethod: params.splitMethod,
              validationDatasetId: params.validationDatasetId,
              validationEnabled: params.validationEnabled,
              validationSplitRatio: params.validationSplitRatio,
            };
      return apiClient.finetuningJobs.finetuningJobsValidateDatasetCreate({
        datasetValidateRequestRequest: body,
      });
    },
    onError: (e) => notify.error(e, "Couldn't validate dataset"),
  });

export const useRecommendModelsQuery = (
  datasetId: string | undefined,
  capabilityId?: string | undefined,
  evalDatasetId?: string | undefined
) =>
  useQuery({
    enabled: !!datasetId,
    gcTime: 10 * 60 * 1000,
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsRecommendCreate({
        finetuningRecommendRequestRequest: {
          capabilityId: capabilityId ?? null,
          datasetId: datasetId!,
          evalDatasetId: evalDatasetId ?? null,
        },
      }),
    queryKey: ["finetuning-recommend", datasetId, capabilityId ?? null, evalDatasetId ?? null],
    staleTime: 60_000,
  });

/** Count of training datapoints whose source trace also appears in the eval
 * dataset — those rows are excluded from training. */
export const useDatasetOverlapQuery = (
  datasetId: string | undefined,
  evalDatasetId: string | undefined
) =>
  useQuery({
    enabled: !!datasetId && !!evalDatasetId,
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsDatasetOverlapRetrieve({
        dataset: datasetId!,
        evalDataset: evalDatasetId!,
      }),
    queryKey: ["finetuning-dataset-overlap", datasetId, evalDatasetId],
    staleTime: 60_000,
  });

interface ClassMetricRow {
  label: string;
  precision: number;
  recall: number;
  f1: number;
  support: number;
}

interface ClassMetricsAggregate {
  precision: number;
  recall: number;
  f1: number;
}

export interface ConfusionMatrixData {
  labels: string[];
  matrix: number[][];
}

/** Per-class classification metrics stamped by the backend when the eval
    dataset's references are labels (null otherwise). */
export interface ClassMetrics {
  classes: ClassMetricRow[];
  aggregates?: {
    accuracy?: number;
    n?: number;
    macro?: ClassMetricsAggregate;
    micro?: ClassMetricsAggregate;
    weighted?: ClassMetricsAggregate;
  };
  confusion_matrix?: ConfusionMatrixData;
}

export interface FinetuningJudgeEvalRow {
  id: string;
  kind: "baseline" | "checkpoint" | "final" | string;
  label?: string;
  comparison_label?: string | null;
  status: string;
  checkpoint_id?: string | null;
  checkpoint_step?: number | null;
  model_id?: string;
  aggregate_score?: number | null;
  baseline_delta?: number | null;
  class_metrics?: ClassMetrics | null;
  eval_run_id?: string | null;
  error_message?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  /** Mean judge score (0–1) per rubric criterion, averaged across samples;
      `aggregate_score` is their average. */
  metric_scores?: Array<{ name: string; score: number }> | null;
  sample_count?: number | null;
}

export interface LossCurveData {
  epochs: (number | null)[];
  steps?: (number | null)[];
  train_loss: (number | null)[];
  valid_loss: (number | null)[];
  eval_loss?: (number | null)[];
  learning_rate?: Array<{ step: number | null; value: number | null }>;
  grad_norm?: Array<{ step: number | null; value: number | null }>;
  /** Mean token accuracy per logged step (train) / eval checkpoint (eval). */
  token_accuracy?: Array<{ step: number | null; train?: number | null; eval?: number | null }>;
  checkpoints?: FinetuningCheckpointRow[];
  judge_evals?: FinetuningJudgeEvalRow[];
  progress?: FinetuningProgressSnapshot;
}

export interface FinetuningCheckpointRow {
  id?: string;
  step: number;
  type?: string;
  path?: string;
  train_loss?: number | null;
  valid_loss?: number | null;
  valid_mean_token_accuracy?: number | null;
  result_files?: string[];
  file_count?: number;
  size_bytes?: number | null;
  created_at?: number | string | null;
  resumable?: boolean;
  has_eval?: boolean;
  upload_status?: string;
}

interface FinetuningProgressSnapshot {
  trained_steps?: number | null;
  total_steps?: number | null;
  percent?: number | null;
  eta_seconds?: number | null;
  elapsed_seconds?: number | null;
  estimated_finish?: number | null;
  tokens_processed?: number | null;
  /** The loss-curves endpoint mirrors these, so the monitor poll stays fresh. */
  metrics_history?: Array<{
    step: number;
    epoch?: number;
    train_loss?: number;
    token_accuracy?: number;
    lr?: number;
    grad_norm?: number;
  }> | null;
  eval_history?: Array<{
    step: number;
    epoch?: number;
    eval_loss?: number;
    eval_token_accuracy?: number;
  }> | null;
  /** Live-activity feed: filtered provider/deploy stage lines (ts in ms). */
  activity?: Array<{ ts?: number | null; message: string }> | null;
  train_loss?: number | null;
  eval_loss?: number | null;
  token_accuracy?: number | null;
  eval_token_accuracy?: number | null;
  current_epoch?: number | null;
  phase?: string | null;
  /** Fine-grained pre-training sub-stage + live base-model download progress. */
  stage?: string | null;
  download?: {
    pct?: number | null;
    downloaded_gb?: number | null;
    total_gb?: number | null;
  } | null;
  /** Server-stamped canonical lifecycle stage (setup → … → completed). */
  lifecycle_stage?: string | null;
  judge_evals?: FinetuningJudgeEvalRow[] | null;
}

export const useLossCurvesQuery = (jobId: string | undefined, refetchWhileLive = false) =>
  useQuery({
    enabled: !!jobId,
    queryFn: async () => {
      const raw = await apiClient.finetuningJobs.finetuningJobsLossCurvesRetrieve({
        id: jobId!,
      });
      return raw as unknown as LossCurveData;
    },
    queryKey: ["finetuning-loss-curves", jobId],
    refetchInterval: (q) => backoffPolling(q, refetchWhileLive ? 3_000 : false),
  });

/** Catalog ``training_type.lora.enabled === false`` ⇒ Full-only (rare). */
export type ModelEntry = FinetuningModelCatalogEntry;
export type ModelCatalog = FinetuningModelCatalogResponse;

export const useModelCatalogQuery = (hasToolCalling = false, maxContext?: number) =>
  useQuery({
    gcTime: 30 * 60 * 1000,
    queryFn: () =>
      apiClient.finetuningJobs.finetuningJobsModelsRetrieve({
        hasToolCalling: hasToolCalling || undefined,
        maxContext: maxContext ?? undefined,
      }),
    queryKey: ["finetuning-model-catalog", hasToolCalling, maxContext ?? null],
    staleTime: Infinity,
  });
