import * as z from "zod";

import {
  DeployedModelsListStatusEnum,
  EvalRunsListStatusEnum,
  FinetuningJobsListStatusEnum,
} from "@/openapi";

export const projectIdSearchSchema = z.object({
  // Reopens the create-project modal (`createProject=new`).
  createProject: z.enum(["new"]).optional(),
  message: z.string().optional(),
  projectId: z.string().optional(),
});

export const onboardingSearchSchema = z.object({
  message: z.string().optional(),
});

/**
 * Named to match django-filter, so values stay strings and pass through to the
 * backend untouched. Every param must be declared even if only the client reads
 * it, or the router treats it as unknown and drops it.
 */
export const tracesSearchSchema = z.object({
  application_name__icontains: z.string().optional(),
  capability: z.string().optional(),
  detailExpanded: z.coerce.boolean().optional().default(false),
  error__icontains: z.string().optional(),
  /**
   * Inverted against its DRF-style name: "true" selects errored traces. The
   * route maps it to the API's `has_error`, which is the real lookup.
   */
  error__isnull: z.enum(["true", "false"]).optional(),
  /** "true" → only traces where some span reported a model (i.e. made an LLM call). */
  has_model: z.enum(["true", "false"]).optional(),
  iteration: z.string().optional(),
  job: z.string().optional(),
  max_duration_ms: z.string().optional(),
  min_duration_ms: z.string().optional(),
  /** Filter to traces whose LLM spans invoked this model id. */
  model: z.string().optional(),
  operation: z.string().optional(),
  operation__icontains: z.string().optional(),
  ordering: z.string().optional().default("-start_time_ns"),

  page: z.coerce.number().min(1).optional().default(1),
  page_size: z.coerce.number().min(5).max(100).optional().default(25),
  projectId: z.string().optional(),

  received_at__gte: z.string().optional(),
  received_at__lte: z.string().optional(),

  score__gte: z.string().optional(),
  score__lte: z.string().optional(),
  search: z.string().optional(),
  service_name: z.string().optional(),
  service_name__icontains: z.string().optional(),
  /** A Conversation UUID, not a span or trace id. */
  session: z.string().optional(),
  source: z.string().optional(),

  source__in: z.string().optional(),
  span_type: z.string().optional(),
  status_code: z.string().optional(),

  /** Drives `received_at__gte`/`__lte` unless set to "custom". */
  timeRange: z
    .enum(["all", "past15m", "past1h", "past24h", "past7d", "past30d", "custom"])
    .optional()
    .default("all"),
  timestamp: z.string().optional(),
  total_cost__gte: z.string().optional(),
  total_cost__lte: z.string().optional(),
  total_latency_ms__gte: z.string().optional(),
  total_latency_ms__lte: z.string().optional(),
  total_tokens__gte: z.string().optional(),
  total_tokens__lte: z.string().optional(),
  trace_group: z.string().optional(),
  trace_group__icontains: z.string().optional(),
  trace_id: z.string().optional(),
  view: z
    .enum(["executions", "roots", "sessions"])
    .optional()
    .catch("executions")
    .default("executions"),
});

/**
 * Shared by the Evaluations and Optimiser lists. "all" means no filter.
 * `run_status` stays free-form because each list has its own status vocabulary.
 */
const runFilterShape = {
  run_capability: z.string().optional().default("all"),
  run_search: z.string().optional().default(""),
  run_status: z.string().optional().default("all"),
};

/** `capability` presets the library's filter and is separate from `run_capability`. */
export const evaluationsSearchSchema = projectIdSearchSchema.extend({
  capability: z.string().optional(),
  page: z.coerce.number().min(1).optional().default(1),
  page_size: z.coerce.number().min(5).max(100).optional().default(25),
  ...runFilterShape,
  run_dataset: z.string().optional().default("all"),
  // Narrower than the shared shape: this list forwards the status to the API,
  // so it must be one `EvalRun.Status` accepts — not the generic Job statuses,
  // whose `partially_completed` the eval-runs endpoint rejects with a 400.
  // `.catch` keeps a stale value from throwing a SearchParamError that would
  // discard the rest of the search.
  run_status: z
    .enum(["all", ...Object.values(EvalRunsListStatusEnum)])
    .optional()
    .default("all")
    .catch("all"),
  view: z.enum(["runs", "sets", "library"]).optional().default("runs"),
});

export type EvaluationsSearch = z.infer<typeof evaluationsSearchSchema>;

/** `optimize` (and legacy `wizard`) open the run-locally instructions dialog;
 * `capabilityId` / `datasetId` prefill the example commands. */
export const optimiserSearchSchema = projectIdSearchSchema.extend({
  capabilityId: z.string().optional(),
  datasetId: z.string().optional(),
  optimize: z.boolean().optional(),
  page: z.coerce.number().min(1).optional().default(1),
  page_size: z.coerce.number().min(5).max(100).optional().default(25),
  ...runFilterShape,
});

export type OptimiserSearch = z.infer<typeof optimiserSearchSchema>;

// `ft_*` rather than the shared `run_*`: `ft_status` is a closed set, `run_status` is
// free-form, and a `search: (prev) => …` navigation carries every route's params.
export const trainingSearchSchema = projectIdSearchSchema.extend({
  capabilityId: z.string().optional(),
  /** `datasetId` preselects the train dataset; `evalDatasetId` the eval one. */
  datasetId: z.string().optional(),
  evalDatasetId: z.string().optional(),
  // Free-form ids: a dataset uuid and a base-model slug.
  ft_dataset: z.string().optional().default("all"),
  ft_model: z.string().optional().default("all"),
  ft_search: z.string().optional().default(""),
  // Forwarded to the API, so it must be one the select offers. `.catch` keeps a
  // hand-edited value from throwing a SearchParamError that would discard the
  // rest of the search — including the open run.
  ft_status: z
    .enum(["all", ...Object.values(FinetuningJobsListStatusEnum)])
    .optional()
    .default("all")
    .catch("all"),
  groupId: z.string().optional(),
  job: z.string().optional(),
  jobId: z.string().optional(),
  page: z.coerce.number().min(1).optional().default(1),
  page_size: z.coerce.number().min(5).max(100).optional().default(25),
  train: z.boolean().optional(),
});

export type TrainingSearch = z.infer<typeof trainingSearchSchema>;

/**
 * Matching is client-side (`filterDatasets`), so nothing is forwarded to the
 * API — but the closed sets still take `.catch` so a stale link falls back to
 * unfiltered instead of throwing a SearchParamError that would discard the rest
 * of the search, `projectId` included.
 */
export const datasetsSearchSchema = projectIdSearchSchema.extend({
  /** Opens the create-dataset dialog on land. The palette sends it as a string. */
  create: z
    .union([z.boolean(), z.enum(["true", "false"]).transform((v) => v === "true")])
    .optional(),
  /** A capability id, or the `NO_CAPABILITY` sentinel for datasets attached to none. */
  ds_capability: z.string().optional().default("all"),
  ds_intent: z.enum(["all", "train", "eval", "pending"]).optional().default("all").catch("all"),
  ds_search: z.string().optional().default(""),
});

export type DatasetsSearch = z.infer<typeof datasetsSearchSchema>;

// `inf_capability` is prefixed so it can't collide with the bare `capability` other routes
// carry through `search: (prev) => …`. The model detail route must share this schema:
// zod strips undeclared keys, and the detail page rebuilds the list URL from them.
export const inferenceSearchSchema = projectIdSearchSchema.extend({
  /** A capability id, the `NO_CAPABILITY` sentinel for deployments attached to none, or "all". */
  inf_capability: z.string().optional().default("all"),
  /** DRF `?search=` — matches the model id, its base model, or the job's name. */
  inf_search: z.string().optional().default(""),
  page: z.coerce.number().min(1).optional().default(1),
  page_size: z.coerce.number().min(5).max(100).optional().default(25),
  status: z.enum(Object.values(DeployedModelsListStatusEnum)).optional().catch(undefined),
});

export type InferenceSearch = z.infer<typeof inferenceSearchSchema>;

export const projectsSearchSchema = z.object({
  createProject: z.enum(["new"]).optional(),
  message: z.string().optional(),
  sortBy: z
    .enum(["name", "organisationName", "memberCount", "createdAt"])
    .optional()
    .default("createdAt"),
  sortDirection: z.enum(["asc", "desc"]).optional().default("desc"),
});

export type ProjectsSearch = z.infer<typeof projectsSearchSchema>;
