import * as z from "zod";

/** Onboarding step query param */
export const onboardingSearchSchema = z.object({
  step: z.enum(["1", "2"]).optional().catch(undefined),
});

export type OnboardingSearch = z.infer<typeof onboardingSearchSchema>;

/** OAuth callback query params */
export const oauthCallbackSearchSchema = z.object({
  code: z.string().optional(),
  state: z.string().optional(),
});

export type OAuthCallbackSearch = z.infer<typeof oauthCallbackSearchSchema>;

/** Traces / Jobs list query params */
export const jobsSearchSchema = z.object({
  job_type: z
    .enum(["all", "agent_discovery", "judge_scoring", "prompt_tuning", "model_backtesting"])
    .optional()
    .default("all"),
  page: z.coerce.number().min(1).optional().default(1),
  pageSize: z.coerce.number().min(5).max(100).optional().default(25),
  sortBy: z
    .enum(["status", "jobType", "promptSlug", "triggeredBy", "createdAt"])
    .optional()
    .default("createdAt"),
  sortDirection: z.enum(["asc", "desc"]).optional().default("desc"),
  status: z.enum(["all", "running", "completed", "partially_completed", "failed", "pending"]).optional().default("all"),
});

export type JobsSearch = z.infer<typeof jobsSearchSchema>;

/** Agent detail / traces presets */
export const tracesPresetSchema = z.enum(["plain", "judge", "slowest", "expensive"]);

export type TracesPreset = z.infer<typeof tracesPresetSchema>;

/** Traces list query params */
export const tracesSearchSchema = z.object({
  query: z.array(z.string()).optional().default([]),
  ordering: z.array(z.string()).optional().default([]),
  flatten: z.coerce.boolean().optional().default(false),
  /** Advanced filter params (agent=, run_type=, latency=, etc.) - must be in schema for router to serialize */
  agent: z.string().optional(),
  agent_op: z.string().optional(),
  /** Expand trace detail drawer to full width */
  detailExpanded: z.coerce.boolean().optional().default(false),
  error_message: z.string().optional(),
  error_message_op: z.string().optional(),
  feedback: z.string().optional(),
  feedback_op: z.string().optional(),
  feedback_source: z.string().optional(),
  feedback_source_op: z.string().optional(),
  full_text_search: z.string().optional(),
  full_text_search_op: z.string().optional(),
  input: z.string().optional(),
  input_key: z.string().optional(),
  input_key_op: z.string().optional(),
  input_op: z.string().optional(),
  is_trace: z.string().optional(),
  is_trace_op: z.string().optional(),
  latency: z.string().optional(),
  latency_op: z.string().optional(),
  metadata: z.string().optional(),
  metadata_op: z.string().optional(),
  output: z.string().optional(),
  output_key: z.string().optional(),
  output_key_op: z.string().optional(),
  output_op: z.string().optional(),
  /** Current page (1-based) */
  page: z.coerce.number().min(1).optional().default(1),
  /** Items per page */
  pageSize: z.coerce.number().min(5).max(100).optional().default(25),
  projectId: z.string().optional(),
  promptHash: z.string().optional(),
  promptSlug: z.string().optional(),
  promptVersion: z.string().optional(),
  q: z.string().optional(),
  run_id: z.string().optional(),
  run_id_op: z.string().optional(),
  run_name: z.string().optional(),
  run_name_op: z.string().optional(),
  run_type: z.string().optional(),
  run_type_op: z.string().optional(),
  sortBy: z
    .enum([
      "timestamp",
      "name",
      "duration",
      "status",
      "trace_id",
      "status_message",
      "model",
      "tokens",
      "cost",
      "judgeScore",
      "estimatedCost",
    ])
    .optional()
    .default("timestamp"),
  sortDirection: z.enum(["asc", "desc"]).optional().default("desc"),
  /** Status filter */
  status: z.enum(["all", "success", "error"]).optional().default("all"),
  tag: z.string().optional(),
  tag_op: z.string().optional(),
  thread_id: z.string().optional(),
  thread_id_op: z.string().optional(),
  timeRange: z.enum(["all", "past24h", "past7d", "past30d"]).optional().default("all"),
  /** Optional timestamp for trace detail (e.g. when trace ID can appear at multiple times) */
  timestamp: z.string().optional(),
  trace_id: z.string().optional(),
  trace_id_op: z.string().optional(),
  trace_status: z.string().optional(),
  trace_status_op: z.string().optional(),
});

export type TracesSearch = z.infer<typeof tracesSearchSchema>;

/** Chatbots / conversation detail query params */
export const chatbotsSearchSchema = z.object({
  name: z.string().optional(),
  projectId: z.string().optional(),
  timeRange: z.enum(["all", "past24h", "past7d", "past30d"]).optional().default("all"),
});

export type ChatbotsSearch = z.infer<typeof chatbotsSearchSchema>;

/** Projects list query params */
export const projectsSearchSchema = z.object({
  sortBy: z
    .enum(["name", "description", "organisationName", "createdAt"])
    .optional()
    .default("createdAt"),
  sortDirection: z.enum(["asc", "desc"]).optional().default("desc"),
});

export type ProjectsSearch = z.infer<typeof projectsSearchSchema>;
