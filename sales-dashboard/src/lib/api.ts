const TOKEN_KEY = "overmind.overwatch.session";
const API_URL = import.meta.env.VITE_API_URL ?? "";
const SESSION_MS = 30 * 60 * 1000;

export function clearToken() {
  sessionStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem("overmind.sales.token");
}

export function getToken(): string | null {
  // Retire the legacy persistent token, even for a previously signed-in browser.
  localStorage.removeItem("overmind.sales.token");
  try {
    const value = JSON.parse(sessionStorage.getItem(TOKEN_KEY) ?? "null");
    if (
      typeof value?.token === "string" &&
      typeof value?.expiresAt === "number" &&
      value.expiresAt > Date.now()
    ) {
      return value.token;
    }
  } catch {
    // Invalid or expired session state requires a fresh sign-in.
  }
  sessionStorage.removeItem(TOKEN_KEY);
  return null;
}

export function setToken(token: string) {
  clearToken();
  sessionStorage.setItem(TOKEN_KEY, JSON.stringify({ expiresAt: Date.now() + SESSION_MS, token }));
}

export class Unauthorized extends Error {}

export type Daily = { day: string; value: number };
export type DailyUsd = { day: string; value: string };

export type Overview = Cached & {
  users_total: number;
  signups: Daily[];
  sign_on_methods: Record<string, number>;
  projects_created: Daily[];
  trace_active_users_7d: number;
  trace_active_users_window: number;
  spans: Daily[];
  traces: Daily[];
  training_jobs: Record<string, number>;
  top_base_models: { base_model: string; jobs: number }[];
  live_deployments: number;
  inference_calls: Daily[];
  subscriptions: Record<string, number>;
  paying_users: number;
  cancelling_users: number;
  free_users: number;
  low_balance_users: number;
  topups_usd: DailyUsd[];
  spend_usd: DailyUsd[];
  spend_by_service: Record<string, string>;
};

export type Activation = {
  steps: { step: string; users: number }[];
  median_hours_to_first_span: number | null;
};

export type ProductUsage = Cached & {
  available: boolean;
  reason?: string;
  identity_basis?: string;
  window_start?: string;
  window_end?: string;
  active_users?: number;
  totals?: {
    cli_completions: number;
    mcp_tool_calls: number;
    training_results_available: number;
    training_checks_finished: number;
  };
  daily?: {
    day: string;
    cli_completions: number;
    mcp_tool_calls: number;
    training_results_available: number;
    training_checks_finished: number;
  }[];
  top_mcp_tools?: { name: string; calls: number; users: number }[];
  top_cli_commands?: { name: string; completions: number; users: number }[];
  mcp_tool_errors?: number;
  mcp_tool_error_rate?: number;
};

export type User = {
  id: number;
  email: string;
  date_joined: string;
  sign_on_method: string;
  projects: number;
  seats: number;
  last_span_at: string | null;
  spans: number;
  training_jobs: number;
  live_deployments: number;
  stage: string;
  subscription_status: string | null;
  spend_usd: string;
  topups_usd: string;
  balance_usd: string;
};

export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };

/** Every response is computed at most once an hour per path and query. */
export type Cached = { computed_at: string; refreshes_at: string };

type Workflow = { total: number; by_status: Record<string, number>; daily: Daily[] };

export type TrainingOutcome = "completed" | "failed" | "cancelled" | "in_progress";

export type UserActivity = Cached & {
  user: User;
  billing: {
    subscription_status: string | null;
    spend_usd: string;
    topups_usd: string;
    balance_usd: string;
    spend_daily: DailyUsd[];
    topups_daily: DailyUsd[];
  };
  training: {
    totals: Record<"started" | TrainingOutcome, number>;
    daily: { day: string; values: Partial<Record<TrainingOutcome, number>> }[];
    recent: {
      id: string;
      base_model: string;
      status: string;
      created_at: string;
      completed_at: string | null;
    }[];
  };
  traces: { total: number; daily: Daily[] };
  inference: { calls: number; models: number; by_model: Record<string, number>; daily: Daily[] };
  datasets: {
    total: number;
    by_source: Record<string, number>;
    by_intent: Record<string, number>;
    daily: Daily[];
  };
  optimizer: Workflow;
  evaluations: Workflow;
};

export async function overwatchGet<T>(
  path: "overview" | "activation" | "product-usage" | "users" | `users/${number}`,
  params: Record<string, string | number | undefined>,
  token = getToken()
): Promise<T & Cached> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  const response = await fetch(`${API_URL}/api/internal/overwatch/${path}/?${query}`, {
    cache: "no-store",
    headers: { Authorization: `Bearer ${token ?? ""}` },
    redirect: "error",
    referrerPolicy: "no-referrer",
  });
  if (response.status === 401 || response.status === 403) throw new Unauthorized();
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}
