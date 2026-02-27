import {
  AgentReviewsApi,
  AgentsApi,
  BacktestingApi,
  Configuration,
  JobsApi,
  OauthApi,
  OnboardingApi,
  ProjectsApi,
  PromptsApi,
  RolesApi,
  SpansApi,
  SuggestionsApi,
  TokenRolesApi,
  TokensApi,
  TracesApi,
  UsersApi,
} from "./api";
import { config } from "./config";

class OvermindClient {
  agents: AgentsApi;
  backtesting: BacktestingApi;
  jobs: JobsApi;
  traces: TracesApi;
  projects: ProjectsApi;
  prompts: PromptsApi;
  spans: SpansApi;
  suggestions: SuggestionsApi;
  tokens: TokensApi;
  roles: RolesApi;
  tokenRoles: TokenRolesApi;
  onboarding: OnboardingApi;
  users: UsersApi;
  oauth: OauthApi;
  agentReviews: AgentReviewsApi;
  constructor(config: Configuration) {
    this.agents = new AgentsApi(config);
    this.backtesting = new BacktestingApi(config);
    this.jobs = new JobsApi(config);
    this.traces = new TracesApi(config);
    this.projects = new ProjectsApi(config);
    this.prompts = new PromptsApi(config);
    this.spans = new SpansApi(config);
    this.suggestions = new SuggestionsApi(config);
    this.tokens = new TokensApi(config);
    this.roles = new RolesApi(config);
    this.tokenRoles = new TokenRolesApi(config);
    this.onboarding = new OnboardingApi(config);
    this.users = new UsersApi(config);
    this.agentReviews = new AgentReviewsApi(config);
    this.oauth = new OauthApi(
      new Configuration({
        basePath: config.basePath,
      })
    );
  }

  /** Custom request for endpoints not in the OpenAPI spec (e.g. chat traces) */
  // async request<T = unknown>(path: string, options?: RequestInit): Promise<T> {
  //   const apiBase = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api/";
  //   const url = path.startsWith("http") ? path : `${apiBase}${path}`;
  //   const token = localStorage.getItem("token") ?? localStorage.getItem("auth_token");
  //   const headers: Record<string, string> = {
  //     "Content-Type": "application/json",
  //     ...(options?.headers as Record<string, string>),
  //   };
  //   if (token) headers.Authorization = `Bearer ${token}`;

  //   const res = await fetch(url, { ...options, headers });
  //   if (!res.ok) {
  //     const errData = await res.json().catch(() => ({}));
  //     const msg = errData?.detail?.message ?? errData?.detail ?? "Request failed";
  //     throw new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
  //   }
  //   const ct = res.headers.get("content-type");
  //   return ct?.includes("application/json") ? res.json() : (res.text() as unknown as T);
  // }
}

// In dev mode, use empty string so requests go to /api/... (proxied by Vite to the live backend).
// In production, use the full URL.

declare global {
  interface Window {
    Clerk: {
      session: {
        getToken: () => Promise<string>;
      };
    };
  }
}
const apiClient = new OvermindClient(
  new Configuration({
    basePath: config.apiUrl,
    middleware: [
      {
        post: async (context) => {
          if (context.response.status === 401) {
            localStorage.removeItem("token");
            window.location.href = "/login";
          }
          return undefined;
        },
        pre: async (context) => {
          const token = config.clerkReady ? (await window.Clerk.session.getToken()) : localStorage.getItem("token");
          if (token) {
            context.init.headers = {
              ...context.init.headers,
              Authorization: `Bearer ${token}`,
            };
          }
          return context;
        },
      },
    ],
  })
);

export default apiClient;
