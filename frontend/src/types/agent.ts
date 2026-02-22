export type HourlyBucket = {
  hour: string;
  avg_score: number | null;
  span_count: number;
  avg_latency_ms: number | null;
  estimated_cost: number;
};

export type AgentAnalytics = {
  total_spans: number;
  scored_spans: number;
  avg_score: number | null;
  avg_latency_ms: number | null;
  total_estimated_cost: number;
  hourly: HourlyBucket[];
};

export type JobStatusItem = {
  job_id: string;
  job_type: string;
  prompt_slug?: string;
  status: string;
  celery_task_id?: string;
  result?: Record<string, unknown>;
  created_at?: string;
  updated_at?: string;
};

export type SuggestionItem = {
  id: string;
  title: string;
  description: string;
  new_prompt_version?: number;
  new_prompt_text?: string;
  scores?: Record<string, unknown>;
  status: string;
  vote?: number;
  feedback?: string | null;
  created_at?: string;
};

export type Agent = {
  slug: string;
  name: string;
  prompt_id: string;
  version: number;
  analytics: AgentAnalytics;
  suggestions: SuggestionItem[];
  jobs: JobStatusItem[];
};

export type PromptVersion = {
  prompt_id: string;
  slug: string;
  version: number;
  prompt_text: string;
  hash: string;
  evaluation_criteria?: Record<string, unknown>;
  improvement_metadata?: Record<string, unknown>;
  created_at?: string;
  total_spans: number;
  scored_spans: number;
  avg_score: number | null;
  avg_latency_ms: number | null;
};

export type AgentDetailData = {
  slug: string;
  name: string;
  latest_version: number;
  analytics: AgentAnalytics;
  versions: PromptVersion[];
  suggestions: SuggestionItem[];
  jobs: JobStatusItem[];
};
