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
  data?: { projectId: string; datasetId?: string }
): string {
  const guide = `${origin.replace(/\/$/, "")}/setup.md`;
  const url = apiUrl.replace(/\/$/, "");
  const subject = data?.datasetId
    ? `Set me up on Overmind with my uploaded data\n(project_id: ${data.projectId},\ndataset_id: ${data.datasetId})`
    : data
      ? `Set me up on Overmind\n(project_id: ${data.projectId})`
      : "Set me up on Overmind";
  if (url !== HOSTED_API_URL) return `${subject}\n\nusing the API at ${url}: ${guide}`;
  return data ? `${subject}\n\n${guide}` : `${subject} ${guide}`;
}
