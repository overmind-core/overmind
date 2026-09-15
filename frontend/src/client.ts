import { toast } from "sonner";

import {
  ApiError,
  extractErrorCode,
  extractErrorDetail,
  NETWORK_ERROR_MESSAGE,
  statusFallbackMessage,
} from "./lib/api-error";
import {
  actionForPath,
  emitPaymentRequired,
  PaymentRequiredError,
  paymentRequiredMessage,
} from "./lib/credits";
import {
  clearGuestSession,
  emitGuestUpgrade,
  GUEST_UPGRADE_CODE,
  GuestUpgradeError,
} from "./lib/guest";
import { emitPlanLimitToast, PlanLimitError, planLimitFromBody } from "./lib/plan-limits";
import type { ModelSwapPrompt } from "./openapi";
import {
  AgentApi,
  AuthApi,
  BehavioursApi,
  BillingApi,
  CapabilitiesApi,
  Configuration,
  ConnectorCredentialsApi,
  DatasetsApi,
  DeployedModelsApi,
  EvalRunsApi,
  EvalSamplesApi,
  EvalScoresApi,
  EvalSetsApi,
  EvaluatorsApi,
  FeedbackApi,
  FinetuningJobsApi,
  ModelsApi,
  OptimizerExperimentsApi,
  ProjectsApi,
  ResponseError,
  SessionsApi,
  TaskExecutionsApi,
  TracesApi,
  UploadsApi,
  VerdictsApi,
} from "./openapi";
import type { Middleware } from "./openapi/runtime";

export { ResponseError };

const TOKEN_KEY = "jwt_access";
const REFRESH_KEY = "jwt_refresh";
const EMAIL_KEY = "auth_email";

// Set by auth-context when Clerk is active; its presence switches this module
// off the localStorage refresh flow entirely.
let _asyncTokenGetter: (() => Promise<string | null>) | null = null;

export function setAsyncTokenGetter(fn: (() => Promise<string | null>) | null) {
  _asyncTokenGetter = fn;
}

export async function resolveToken(): Promise<string | null> {
  if (_asyncTokenGetter) return _asyncTokenGetter();
  return localStorage.getItem(TOKEN_KEY);
}

export function getAccessToken(): string | null {
  return localStorage.getItem(TOKEN_KEY);
}
export function getRefreshToken(): string | null {
  return localStorage.getItem(REFRESH_KEY);
}
export function setTokens(access: string, refresh: string) {
  localStorage.setItem(TOKEN_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}
export function clearTokens() {
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(EMAIL_KEY);
  clearGuestSession();
}
const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const LOGIN_REDIRECT_GUARD_KEY = "login_redirect_at";

/**
 * Guarded to once per 30s so a 401 storm — the backend rejecting tokens Clerk
 * still considers valid — degrades to per-request errors instead of a
 * full-page reload loop.
 */
function redirectToLogin() {
  const last = Number(sessionStorage.getItem(LOGIN_REDIRECT_GUARD_KEY) || 0);
  if (Date.now() - last < 30_000) return;
  sessionStorage.setItem(LOGIN_REDIRECT_GUARD_KEY, String(Date.now()));
  toast.error("Your session has expired. Sign in again.");
  const next = window.location.pathname + window.location.search;
  const suffix =
    next && next !== "/" && next !== "/login" ? `?next=${encodeURIComponent(next)}` : "";
  setTimeout(() => {
    window.location.href = `/login${suffix}`;
  }, 800);
}

let refreshPromise: Promise<boolean> | null = null;

const authApi = new AuthApi(
  new Configuration({
    basePath: BASE_URL,
  })
);

async function refreshAccessToken(): Promise<boolean> {
  if (_asyncTokenGetter) return false;
  const refresh = getRefreshToken();
  if (!refresh) return false;
  try {
    const res = await authApi.authTokenRefreshCreate({
      publicTokenRefreshRequest: {
        refresh: refresh,
      },
    });
    setTokens(res.access, res.refresh ?? refresh);
    return true;
  } catch {
    return false;
  }
}

const authRefreshMiddleware: Middleware = {
  async post(context) {
    if (context.response.status !== 401) return;
    if (_asyncTokenGetter) {
      redirectToLogin();
      return undefined;
    }
    if (getRefreshToken()) {
      if (!refreshPromise) {
        refreshPromise = refreshAccessToken().finally(() => {
          refreshPromise = null;
        });
      }
      const refreshed = await refreshPromise;
      if (refreshed) {
        const retryInit: RequestInit = {
          ...context.init,
          headers: {
            ...(context.init.headers as Record<string, string>),
            Authorization: `Bearer ${getAccessToken()}`,
          },
        };
        return fetch(context.url, retryInit);
      }
    }
    clearTokens();
    redirectToLogin();
    return undefined;
  },
};

/**
 * Raising the dialog here rather than at call sites means a caller that only
 * logs errors still gets the right UI. Plan-limit 403s get a one-shot upgrade
 * toast instead of the credits dialog.
 */
const paymentRequiredMiddleware: Middleware = {
  async post(context) {
    const status = context.response.status;
    if (status === 402) {
      const body = await context.response.text().catch(() => "");
      emitPaymentRequired({ action: actionForPath(context.url) });
      throw new PaymentRequiredError(paymentRequiredMessage(body));
    }
    if (status === 403) {
      const body = await context.response.text().catch(() => "");
      if (extractErrorCode(body) === GUEST_UPGRADE_CODE) {
        emitGuestUpgrade();
        throw new GuestUpgradeError();
      }
      const plan = planLimitFromBody(body);
      if (plan) {
        emitPlanLimitToast(plan.message);
        throw new PlanLimitError(plan.message, plan.code);
      }
    }
  },
};

// Must stay last in the chain. No request timeout, deliberately: synchronous LLM
// endpoints run for minutes and a client abort would waste credits already spent.
const errorDetailMiddleware: Middleware = {
  async onError(context) {
    if (context.error instanceof TypeError) {
      throw new ApiError(0, NETWORK_ERROR_MESSAGE, false);
    }
    throw context.error;
  },
  async post(context) {
    const status = context.response.status;
    if (status >= 200 && status < 300) return;
    const body = await context.response.text().catch(() => "");
    const detail = extractErrorDetail(body);
    throw new ApiError(
      status,
      detail ?? statusFallbackMessage(status),
      detail != null,
      extractErrorCode(body)
    );
  },
};

const config = new Configuration({
  accessToken: async () => (await resolveToken()) ?? "",
  basePath: BASE_URL,
  middleware: [authRefreshMiddleware, paymentRequiredMiddleware, errorDetailMiddleware],
});

function snakeToCamel(s: string): string {
  return s.replace(/_([a-z])/g, (_, c) => c.toUpperCase());
}

// Arbitrary user/agent JSON that is sent back verbatim, so `camelize` must not recurse into it.
const PRESERVE_VALUE_KEYS = new Set([
  "spanAttributes",
  "resourceAttributes",
  "metadata",
  "params",
  "result",
  "inputData",
  "outputData",
  "evaluationCriteria",
  "improvementMetadata",
  "capabilityDescription",
  "backtestModelSuggestions",
  "scores",
  "feedbackScores",
  "feedbackScore",
  "inputSchema",
  "outputFields",
  "toolConfig",
  "dimensionScores",
  "backtestResults",
  "settings",
  "consistencyRules",
  "optimizableElements",
  "fixedElements",
  "input",
  "expectedOutput",
  "manifest",
  "hyperparameters",
  "progress",
  "data",
  "checklist",
  "variableMapping",
  "trajectory",
  "structured",
  "subScores",
  "config",
  "expected",
  "traceFilter",
  "summary",
  "choices",
  "profile",
  "suggestedRubrics",
  "autoRubricChecklist",
  "generated",
  "proposal",
  "viability",
  "preview",
  "ingestReport",
  "axes",
]);

export function camelize(obj: unknown): unknown {
  if (obj == null || typeof obj !== "object") return obj;
  if (Array.isArray(obj)) return obj.map((item) => camelize(item));
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    const camelKey = snakeToCamel(key);
    out[camelKey] = PRESERVE_VALUE_KEYS.has(camelKey) ? value : camelize(value);
  }
  return out;
}

function camelToSnake(s: string): string {
  return s.replace(/[A-Z]/g, (m) => `_${m.toLowerCase()}`);
}

export function snakeize(obj: unknown): unknown {
  if (obj == null || typeof obj !== "object") return obj;
  if (Array.isArray(obj)) return obj.map((item) => snakeize(item));
  const out: Record<string, unknown> = {};
  for (const [key, value] of Object.entries(obj as Record<string, unknown>)) {
    out[camelToSnake(key)] = snakeize(value);
  }
  return out;
}

/** Authenticated fetch (multipart-safe — does not force JSON Content-Type). */
export async function fetchWithAuth(path: string, init?: RequestInit): Promise<Response> {
  const buildHeaders = async (): Promise<Headers> => {
    const token = await resolveToken();
    const headers = new Headers(init?.headers);
    if (token) headers.set("Authorization", `Bearer ${token}`);
    return headers;
  };

  let headers = await buildHeaders();
  let res = await fetch(`${BASE_URL}${path}`, { ...init, headers });

  if (res.status === 401 && !_asyncTokenGetter && getRefreshToken()) {
    if (!refreshPromise) {
      refreshPromise = refreshAccessToken().finally(() => {
        refreshPromise = null;
      });
    }
    const refreshed = await refreshPromise;
    if (refreshed) {
      headers = await buildHeaders();
      res = await fetch(`${BASE_URL}${path}`, { ...init, headers });
    }
  }

  if (res.status === 401) {
    if (!_asyncTokenGetter) {
      clearTokens();
      redirectToLogin();
    }
  }

  // Callers read the raw Response (SSE, file export), so raise the dialog here
  // and let them fall through to their own handling.
  if (res.status === 402) {
    emitPaymentRequired({ action: actionForPath(path) });
  }

  return res;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = await resolveToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(token ? { Authorization: `Bearer ${token}` } : {}),
    ...(init?.headers as Record<string, string> | undefined),
  };
  let res = await fetch(`${BASE_URL}${path}`, { ...init, headers });
  if (res.status === 401 && !_asyncTokenGetter && getRefreshToken()) {
    if (!refreshPromise) {
      refreshPromise = refreshAccessToken().finally(() => {
        refreshPromise = null;
      });
    }
    const refreshed = await refreshPromise;
    if (refreshed) {
      headers.Authorization = `Bearer ${getAccessToken()}`;
      res = await fetch(`${BASE_URL}${path}`, { ...init, headers });
    }
  }
  if (res.status === 401) {
    if (!_asyncTokenGetter) {
      clearTokens();
      redirectToLogin();
    }
    throw new ResponseError(res, "Unauthorized");
  }
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    if (res.status === 402) {
      emitPaymentRequired({ action: actionForPath(path) });
      throw new PaymentRequiredError(paymentRequiredMessage(body));
    }
    if (res.status === 403) {
      if (extractErrorCode(body) === GUEST_UPGRADE_CODE) {
        emitGuestUpgrade();
        throw new GuestUpgradeError();
      }
      const plan = planLimitFromBody(body);
      if (plan) {
        emitPlanLimitToast(plan.message);
        throw new PlanLimitError(plan.message, plan.code);
      }
    }
    const detail = extractErrorDetail(body);
    throw new ApiError(
      res.status,
      detail ?? statusFallbackMessage(res.status),
      detail != null,
      extractErrorCode(body)
    );
  }
  if (res.status === 204) return undefined as T;
  return camelize(await res.json()) as T;
}

export class API {
  agent: AgentApi;
  capabilities: CapabilitiesApi;
  auth: AuthApi;
  behaviours: BehavioursApi;
  billing: BillingApi;
  connectorCredentials: ConnectorCredentialsApi;
  datasets: DatasetsApi;
  deployedModels: DeployedModelsApi;
  feedback: FeedbackApi;
  finetuningJobs: FinetuningJobsApi;
  uploads: UploadsApi;
  optimizerExperiments: OptimizerExperimentsApi;
  /** OTLP trace ingestion (`POST /api/v1/traces`). */
  projects: ProjectsApi;
  /** Traces grouped by `conversation.id`. */
  sessions: SessionsApi;
  taskExecutions: TaskExecutionsApi;
  traces: TracesApi;
  verdicts: VerdictsApi;
  request = request;
  /** OpenRouter catalogue proxy — not this project's deployed models. */
  models: ModelsApi;
  evaluators: EvaluatorsApi;
  evalSets: EvalSetsApi;
  evalRuns: EvalRunsApi;
  evalSamples: EvalSamplesApi;
  evalScores: EvalScoresApi;

  constructor(private cfg: Configuration) {
    this.agent = new AgentApi(this.cfg);
    this.capabilities = new CapabilitiesApi(this.cfg);
    this.auth = new AuthApi(this.cfg);
    this.behaviours = new BehavioursApi(this.cfg);
    this.billing = new BillingApi(this.cfg);
    this.connectorCredentials = new ConnectorCredentialsApi(this.cfg);
    this.datasets = new DatasetsApi(this.cfg);
    this.deployedModels = new DeployedModelsApi(this.cfg);
    this.feedback = new FeedbackApi(this.cfg);
    this.finetuningJobs = new FinetuningJobsApi(this.cfg);
    this.uploads = new UploadsApi(this.cfg);
    this.optimizerExperiments = new OptimizerExperimentsApi(this.cfg);
    this.projects = new ProjectsApi(this.cfg);
    this.sessions = new SessionsApi(this.cfg);
    this.taskExecutions = new TaskExecutionsApi(this.cfg);
    this.traces = new TracesApi(this.cfg);
    this.verdicts = new VerdictsApi(this.cfg);
    this.models = new ModelsApi(this.cfg);
    this.evaluators = new EvaluatorsApi(this.cfg);
    this.evalSets = new EvalSetsApi(this.cfg);
    this.evalRuns = new EvalRunsApi(this.cfg);
    this.evalSamples = new EvalSamplesApi(this.cfg);
    this.evalScores = new EvalScoresApi(this.cfg);
  }
}

export const api = new API(config);
export default api;

export function modelSwapPrompt({
  id,
  pin,
}: {
  id: string;
  pin: boolean;
}): Promise<ModelSwapPrompt> {
  return api.finetuningJobs.finetuningJobsModelSwapPromptRetrieve({ id, pin });
}
