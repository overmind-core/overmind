import { config } from "@/config";

export type McpClient = "cursor" | "claude" | "opencode" | "codex";

export const MCP_CLIENTS: { id: McpClient; label: string; destination: string }[] = [
  { destination: ".cursor/mcp.json", id: "cursor", label: "Cursor" },
  { destination: "~/.claude.json", id: "claude", label: "Claude Code" },
  { destination: "opencode.json", id: "opencode", label: "opencode" },
  { destination: ".codex/config.toml", id: "codex", label: "Codex" },
];

export const API_KEY_PLACEHOLDER = "<your-api-key>";
export const MIN_ONBOARDING_SDK_VERSION = "0.1.106";
export const SDK_PREFLIGHT_COMMAND = "python -m overmind --version";

const HOSTED_API_URL = "https://api.overmindlab.ai";

export function mcpClientMeta(client: McpClient) {
  return MCP_CLIENTS.find((c) => c.id === client) ?? MCP_CLIENTS[0];
}

function apiHost(apiUrl: string): string {
  try {
    return new URL(apiUrl).hostname;
  } catch {
    return "";
  }
}

function isLocal(apiUrl: string): boolean {
  const host = apiHost(apiUrl);
  return host === "localhost" || host === "127.0.0.1";
}

/** Local checkout from `VITE_SDK_EDITABLE_PATH`; only honoured against a local API. */
function localSdkPath(apiUrl: string): string {
  return isLocal(apiUrl) ? config.sdkEditablePath : "";
}

/** Maps Console API host onto `overmind init --env`. */
export function mcpInitEnvFlag(apiUrl: string): string {
  if (isLocal(apiUrl)) return " --env local";
  if (apiHost(apiUrl).includes("staging")) return " --env staging";
  return "";
}

function initFlags(client: McpClient, apiUrl: string): string {
  return `--ide ${client}${mcpInitEnvFlag(apiUrl)}`;
}

/** The local checkout against a local API, PyPI otherwise. */
function installSpec(apiUrl: string, extra?: "tracing"): string {
  const path = localSdkPath(apiUrl);
  const suffix = extra ? `[${extra}]` : "";
  return path ? `-e "${path}${suffix}"` : `"overmind${suffix}>=${MIN_ONBOARDING_SDK_VERSION}"`;
}

export function sdkPipInstall(apiUrl = config.apiUrl, extra?: "tracing"): string {
  return `python -m pip install ${installSpec(apiUrl, extra)}`;
}

/** Install, preflight, exports, `overmind init`, then `overmind sync` to create the project. */
export function mcpInitCommand(
  client: McpClient,
  apiKey: string,
  apiUrl = config.apiUrl,
  extra?: "tracing",
  projectId?: string
): string {
  return [
    sdkPipInstall(apiUrl, extra),
    SDK_PREFLIGHT_COMMAND,
    `export OVERMIND_API_URL=${apiUrl.replace(/\/$/, "")}`,
    ...(projectId ? [`export OVERMIND_PROJECT_ID=${projectId}`] : []),
    `export OVERMIND_API_KEY=${apiKey}`,
    `python -m overmind init ${initFlags(client, apiUrl)}`,
    "python -m overmind sync",
  ].join(" &&\n");
}

export function onboardingPrompt(
  origin: string,
  apiUrl: string,
  data?: { projectId: string; datasetId: string }
): string {
  const guide = `${origin.replace(/\/$/, "")}/setup.md`;
  const url = apiUrl.replace(/\/$/, "");
  const subject = data
    ? `Set me up on Overmind with my uploaded data\n(project_id: ${data.projectId},\ndataset_id: ${data.datasetId})`
    : "Set me up on Overmind";
  if (url !== HOSTED_API_URL) return `${subject}\n\nusing the API at ${url}: ${guide}`;
  return data ? `${subject}\n\n${guide}` : `${subject} ${guide}`;
}

/** Instruction handed to the coding agent, addressed to the agent the user picked. */
export function telemetrySetupPrompt(client: McpClient): string {
  const meta = mcpClientMeta(client);
  return [
    `Instrument this repository using the Overmind MCP and skill setup in ${meta.label}. Do not rerun \`overmind init\` for an already configured client. If ${meta.label} has no Overmind MCP entry and onboarding sync has completed, run \`overmind init --ide ${client}\` once. If MCP authentication is pending, stop and ask the user to complete the sign-in step \`overmind init\` printed.`,
    "Install `overmind[tracing]` if tracing is not already importable, with the installer the project already uses.",
    "For local runs, the tracing SDK reuses the credential saved by `overmind sync`; deployed processes still require `OVERMIND_API_KEY` in their runtime secret configuration.",
    "Follow the “Instrumentation” section of the installed overmind-observability skill. Call `get_instrumentation_plan` with no capability for project-wide work or the supplied capability for scoped work. If it returns `human_action` or no placements, report the instruction and stop this attempt.",
    "Apply each placement's `required_decorator` verbatim at `target.qualname` in `target.file`, keeping `capability_id` exact. Expectations that describe one span go on `@overmind.observe(expectations=[...])`; capability-wide expectations belong in the eval matrix, not the code. When delegating, give each file one owner so two workers never edit the same file.",
    "After applying the placements, report the changed files and checks, generate a unique verification correlation, and ask the user to choose Real run (recommended) or Smoke run. Present the exact command or input, capability, environment, provider/model, expected side effects, correlation value, and approved attempt count; mark unknown fields as needing user input. Do not run either mode before explicit approval. Stamp the approved correlation as `conversation.id` with the application's existing mechanism or `overmind.set_conversation_id`, run only the approved input, flush, and poll `query_traces(session=<correlation>, all_spans=false, limit=2)` within a fixed bound. Require `page.total == 1` and pass that row's `trace_id` to `verify_instrumentation(trace_id=...)`; the server grades the ingested spans. Real-run retries require fresh approval unless an exact input and bounded attempt count were approved. Report application outcome separately from instrumentation status.",
  ].join(" ");
}
