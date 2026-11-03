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
import { dismissSessionRecovery, requireSessionRecovery } from "./lib/session-recovery";
import type { ModelSwapPrompt } from "./openapi";
import {
  ActivityApi,
  AgentApi,
  AuthApi,
  BillingApi,
  CapabilitiesApi,
  Configuration,
  ConnectorCredentialsApi,
  DataPartitionsApi,
  DatasetPipelineBindingsApi,
  DatasetPipelinePackagesApi,
  DatasetPipelinesApi,
  DatasetsApi,
  DatasetTransfersApi,
  DecisionJobsApi,
  DecisionModelsApi,
  DecisionPerformanceApi,
  DeployedModelsApi,
  EvalMatrixApi,
  EvalRunRowsApi,
  EvalRunsApi,
  EvalScoresApi,
  EvalSetsApi,
  FeedbackApi,
  FinetuningJobsApi,
  InferenceRequestsApi,
  McpOauthApi,
  ModelsApi,
  NativeEvaluationsApi,
  OptimizerExperimentsApi,
  ProjectsApi,
  ResponseError,
  SessionsApi,
  SignalEventsApi,
  SignalSubscriptionsApi,
  TracesApi,
  TrainingExperimentsApi,
  TrainingPlansApi,
  TrainingPreparationsApi,
  UploadsApi,
} from "./openapi";
import type { Middleware } from "./openapi/runtime";

export { ResponseError };

const TOKEN_KEY = "jwt_access";
const REFRESH_KEY = "jwt_refresh";
const EMAIL_KEY = "auth_email";
const IDENTITY_KEY = "auth_identity";

// Set by auth-context when Clerk is active; its presence switches this module
// off the localStorage refresh flow entirely.
let _asyncTokenGetter: ((options?: { skipCache?: boolean }) => Promise<string | null>) | null =
  null;

let sessionRevision = 0;
let sessionIdentity: string | null = null;

function changeSession() {
  sessionRevision++;
  refreshPromise = null;
  dismissSessionRecovery();
}

export function setAsyncTokenGetter(
  fn: ((options?: { skipCache?: boolean }) => Promise<string | null>) | null,
  identity: string | null = null
) {
  if (_asyncTokenGetter !== fn || sessionIdentity !== identity) changeSession();
  sessionIdentity = identity;
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
// Token claims only bind cached UI identity; the API still verifies every credential.
export function getLocalAccountId(token = getAccessToken()): string | null {
  if (!token) return null;
  try {
    const claims = JSON.parse(atob(token.split(".")[1].replace(/-/g, "+").replace(/_/g, "/")));
    return typeof claims.user_id === "string" || typeof claims.user_id === "number"
      ? String(claims.user_id)
      : null;
  } catch {
    return null;
  }
}

export function rememberLocalIdentity(user: { id: string | number; email: string }) {
  localStorage.setItem(IDENTITY_KEY, JSON.stringify({ email: user.email, id: String(user.id) }));
}

export function getLocalIdentity(): { id: string; email: string } | null {
  try {
    const user = JSON.parse(localStorage.getItem(IDENTITY_KEY) ?? "null");
    return user &&
      typeof user.id === "string" &&
      typeof user.email === "string" &&
      user.id === getLocalAccountId()
      ? user
      : null;
  } catch {
    return null;
  }
}

export function setTokens(
  access: string,
  refresh: string,
  user?: { id: string | number; email: string }
) {
  changeSession();
  storeTokens(access, refresh);
  if (user) rememberLocalIdentity(user);
  else localStorage.removeItem(IDENTITY_KEY);
}

function storeTokens(access: string, refresh: string) {
  localStorage.setItem(TOKEN_KEY, access);
  localStorage.setItem(REFRESH_KEY, refresh);
}
export function clearTokens() {
  changeSession();
  localStorage.removeItem(TOKEN_KEY);
  localStorage.removeItem(REFRESH_KEY);
  localStorage.removeItem(EMAIL_KEY);
  localStorage.removeItem(IDENTITY_KEY);
  clearGuestSession();
}
if (typeof window !== "undefined") {
  window.addEventListener("storage", (event) => {
    if (event.storageArea && event.storageArea !== localStorage) return;
    if (event.key === null) changeSession();
    if (event.key !== TOKEN_KEY && event.key !== REFRESH_KEY) return;
    const previous = getLocalAccountId(event.oldValue);
    if (!previous || previous !== getLocalAccountId(event.newValue)) changeSession();
  });
}

const configuredBaseURL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";
const BASE_URL =
  import.meta.env.VITE_SELF_HOSTED === "true" &&
  typeof window !== "undefined" &&
  window.location.hostname === "127.0.0.1"
    ? configuredBaseURL.replace(/^http:\/\/localhost(?=[:/]|$)/, "http://127.0.0.1")
    : configuredBaseURL;

let refreshPromise: Promise<string | null> | null = null;

const authApi = new AuthApi(new Configuration({ basePath: BASE_URL }));

async function refreshAccessToken(revision: number): Promise<string | null> {
  if (_asyncTokenGetter) return _asyncTokenGetter({ skipCache: true });
  const refresh = getRefreshToken();
  if (!refresh) return null;
  try {
    const res = await authApi.authTokenRefreshCreate({
      publicTokenRefreshRequest: { refresh },
    });
    // A sign-out or another tab changing accounts must not be undone by this response.
    if (revision !== sessionRevision || getRefreshToken() !== refresh) return null;
    storeTokens(res.access, res.refresh ?? refresh);
    return res.access;
  } catch (error) {
    if (error instanceof ResponseError) {
      if (error.response.status === 401 || error.response.status === 403) return null;
      throw new ApiError(
        error.response.status,
        statusFallbackMessage(error.response.status),
        false
      );
    }
    throw new ApiError(0, NETWORK_ERROR_MESSAGE, false);
  }
}

// A retry belongs to the session that started the request, never a later sign-in.
async function authenticatedFetch(input: RequestInfo | URL, init?: RequestInit): Promise<Response> {
  const revision = sessionRevision;
  const assertSession = () => {
    if (revision !== sessionRevision) throw new DOMException("Session changed", "AbortError");
  };
  const token = await resolveToken();
  const headers = new Headers(
    init?.headers ?? (input instanceof Request ? input.headers : undefined)
  );
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const options = { ...init, headers };
  const signal = init?.signal ?? (input instanceof Request ? input.signal : undefined);
  signal?.throwIfAborted();
  assertSession();
  let response = await fetch(input, options);
  signal?.throwIfAborted();
  assertSession();
  const url = input instanceof Request ? input.url : String(input);
  if (response.status !== 401 || !token || new URL(url).pathname === "/api/auth/local/")
    return response;

  let current = await resolveToken();
  assertSession();
  if (!current || current === token) {
    if (!refreshPromise) {
      const pending = refreshAccessToken(revision).finally(() => {
        if (refreshPromise === pending) refreshPromise = null;
      });
      refreshPromise = pending;
    }
    current = await refreshPromise;
  }
  signal?.throwIfAborted();
  assertSession();
  if (current) {
    headers.set("Authorization", `Bearer ${current}`);
    response = await fetch(input, { ...options, headers });
  }
  signal?.throwIfAborted();
  assertSession();
  if (response.status === 401) requireSessionRecovery();
  return response;
}

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
  basePath: BASE_URL,
  fetchApi: authenticatedFetch,
  middleware: [paymentRequiredMiddleware, errorDetailMiddleware],
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
  const res = await authenticatedFetch(`${BASE_URL}${path}`, init);

  // Callers read the raw Response (SSE, file export), so raise the dialog here
  // and let them fall through to their own handling.
  if (res.status === 402) {
    emitPaymentRequired({ action: actionForPath(path) });
  }

  return res;
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const headers = new Headers(init?.headers);
  if (!headers.has("Content-Type")) headers.set("Content-Type", "application/json");
  const res = await fetchWithAuth(path, { ...init, headers });
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
  mcpOauth: McpOauthApi;
  agent: AgentApi;
  capabilities: CapabilitiesApi;
  auth: AuthApi;
  billing: BillingApi;
  connectorCredentials: ConnectorCredentialsApi;
  datasets: DatasetsApi;
  datasetPipelines: DatasetPipelinesApi;
  datasetPipelinePackages: DatasetPipelinePackagesApi;
  datasetPipelineBindings: DatasetPipelineBindingsApi;
  datasetTransfers: DatasetTransfersApi;
  deployedModels: DeployedModelsApi;
  inferenceRequests: InferenceRequestsApi;
  feedback: FeedbackApi;
  finetuningJobs: FinetuningJobsApi;
  uploads: UploadsApi;
  trainingPreparations: TrainingPreparationsApi;
  dataPartitions: DataPartitionsApi;
  nativeEvaluations: NativeEvaluationsApi;
  trainingExperiments: TrainingExperimentsApi;
  trainingPlans: TrainingPlansApi;
  decisionJobs: DecisionJobsApi;
  decisionModels: DecisionModelsApi;
  decisionPerformance: DecisionPerformanceApi;
  optimizerExperiments: OptimizerExperimentsApi;
  /** OTLP trace ingestion (`POST /api/v1/traces`). */
  projects: ProjectsApi;
  /** Traces grouped by `conversation.id`. */
  sessions: SessionsApi;
  traces: TracesApi;
  request = request;
  /** OpenRouter catalogue proxy — not this project's deployed models. */
  models: ModelsApi;
  evalMatrix: EvalMatrixApi;
  evalSets: EvalSetsApi;
  evalRuns: EvalRunsApi;
  evalRunRows: EvalRunRowsApi;
  evalScores: EvalScoresApi;
  signalEvents: SignalEventsApi;
  signalSubscriptions: SignalSubscriptionsApi;
  activity: ActivityApi;

  constructor(private cfg: Configuration) {
    this.activity = new ActivityApi(this.cfg);
    this.mcpOauth = new McpOauthApi(this.cfg);
    this.agent = new AgentApi(this.cfg);
    this.capabilities = new CapabilitiesApi(this.cfg);
    this.auth = new AuthApi(this.cfg);
    this.billing = new BillingApi(this.cfg);
    this.connectorCredentials = new ConnectorCredentialsApi(this.cfg);
    this.datasets = new DatasetsApi(this.cfg);
    this.datasetPipelines = new DatasetPipelinesApi(this.cfg);
    this.datasetPipelinePackages = new DatasetPipelinePackagesApi(this.cfg);
    this.datasetPipelineBindings = new DatasetPipelineBindingsApi(this.cfg);
    this.datasetTransfers = new DatasetTransfersApi(this.cfg);
    this.deployedModels = new DeployedModelsApi(this.cfg);
    this.inferenceRequests = new InferenceRequestsApi(this.cfg);
    this.feedback = new FeedbackApi(this.cfg);
    this.finetuningJobs = new FinetuningJobsApi(this.cfg);
    this.uploads = new UploadsApi(this.cfg);
    this.trainingPreparations = new TrainingPreparationsApi(this.cfg);
    this.dataPartitions = new DataPartitionsApi(this.cfg);
    this.nativeEvaluations = new NativeEvaluationsApi(this.cfg);
    this.trainingExperiments = new TrainingExperimentsApi(this.cfg);
    this.trainingPlans = new TrainingPlansApi(this.cfg);
    this.decisionJobs = new DecisionJobsApi(this.cfg);
    this.decisionModels = new DecisionModelsApi(this.cfg);
    this.decisionPerformance = new DecisionPerformanceApi(this.cfg);
    this.optimizerExperiments = new OptimizerExperimentsApi(this.cfg);
    this.projects = new ProjectsApi(this.cfg);
    this.sessions = new SessionsApi(this.cfg);
    this.traces = new TracesApi(this.cfg);
    this.models = new ModelsApi(this.cfg);
    this.evalMatrix = new EvalMatrixApi(this.cfg);
    this.evalSets = new EvalSetsApi(this.cfg);
    this.evalRuns = new EvalRunsApi(this.cfg);
    this.evalRunRows = new EvalRunRowsApi(this.cfg);
    this.evalScores = new EvalScoresApi(this.cfg);
    this.signalEvents = new SignalEventsApi(this.cfg);
    this.signalSubscriptions = new SignalSubscriptionsApi(this.cfg);
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
