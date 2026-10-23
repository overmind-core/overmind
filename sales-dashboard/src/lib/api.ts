const TOKEN_KEY = "overmind.sales.token";
const API_URL = import.meta.env.VITE_API_URL ?? "";

export const getToken = () => localStorage.getItem(TOKEN_KEY);
export const setToken = (token: string) => localStorage.setItem(TOKEN_KEY, token);
export const clearToken = () => localStorage.removeItem(TOKEN_KEY);

export class Unauthorized extends Error {}

export type Daily = { day: string; value: number };
export type DailyUsd = { day: string; value: string };

export type Overview = {
  accounts_total: number;
  signups: Daily[];
  sign_on_methods: Record<string, number>;
  projects_created: Daily[];
  active_accounts_7d: number;
  active_accounts_window: number;
  subscriptions: Record<string, number>;
  paying_accounts: number;
  cancelling_accounts: number;
  free_accounts: number;
  low_balance_accounts: number;
  topups_usd: DailyUsd[];
  spend_by_service: Record<string, string>;
  spans: Daily[];
  traces: Daily[];
  training_jobs: Record<string, number>;
  top_base_models: { base_model: string; jobs: number }[];
  live_deployments: number;
  inference_calls: Daily[];
};

export type Funnel = {
  steps: { step: string; accounts: number }[];
  median_hours_to_first_span: number | null;
};

export type Account = {
  id: number;
  email: string;
  date_joined: string;
  sign_on_method: string;
  subscription_status: string | null;
  projects: number;
  seats: number;
  last_span_at: string | null;
  spans: number;
  spend_usd: string;
  topups_usd: string;
  balance_usd: string;
  training_jobs: number;
  live_deployments: number;
  stage: string;
};

export type Page<T> = { count: number; next: string | null; previous: string | null; results: T[] };

export async function salesGet<T>(
  path: "overview" | "funnel" | "accounts",
  params: Record<string, string | number | undefined>,
  token = getToken()
): Promise<T> {
  const query = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined) query.set(key, String(value));
  }
  const response = await fetch(`${API_URL}/api/internal/sales/${path}/?${query}`, {
    headers: { Authorization: `Bearer ${token ?? ""}` },
  });
  if (response.status === 401 || response.status === 403) throw new Unauthorized();
  if (!response.ok) throw new Error(`Request failed: ${response.status}`);
  return response.json();
}
