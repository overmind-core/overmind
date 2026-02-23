import {
  AgentsApi,
  BacktestingApi,
  Configuration,
  JobsApi,
  OnboardingApi,
  ProjectsApi,
  PromptsApi,
  SpansApi,
  SuggestionsApi,
  TokensApi,
  TracesApi,
  UsersApi,
  AgentReviewsApi,
} from "./api";

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
  onboarding: OnboardingApi;
  users: UsersApi;
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
    this.onboarding = new OnboardingApi(config);
    this.users = new UsersApi(config);
    this.agentReviews = new AgentReviewsApi(config);
  }
}

const baseUrl = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000";

const apiClient = new OvermindClient(
  new Configuration({
    basePath: baseUrl,
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
          const token = localStorage.getItem("token");
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
