import { config } from "@/config";

export type McpClient = "cursor" | "claude" | "opencode" | "codex";

export const MCP_CLIENTS: { id: McpClient; label: string; destination: string }[] = [
  { destination: ".cursor/mcp.json", id: "cursor", label: "Cursor" },
  { destination: ".mcp.json", id: "claude", label: "Claude Code" },
  { destination: "opencode.json", id: "opencode", label: "opencode" },
  { destination: ".codex/config.toml", id: "codex", label: "Codex" },
];

export const API_KEY_PLACEHOLDER = "<your-api-key>";

export function mcpClientMeta(client: McpClient) {
  return MCP_CLIENTS.find((c) => c.id === client) ?? MCP_CLIENTS[0];
}

const PLATFORM_SDK_GIT = "git+https://github.com/overmind-core/platform.git";

export type SdkInstallChannel = "pypi" | "git" | "editable";

function apiHost(apiUrl: string): string {
  try {
    return new URL(apiUrl).hostname;
  } catch {
    return "";
  }
}

function isLocalApiHost(host: string): boolean {
  return host === "localhost" || host === "127.0.0.1";
}

function isStagingApiHost(host: string): boolean {
  return host.includes("staging");
}

/**
 * Console API host → SDK install source:
 * - production (`api.overmindlab.ai`) → PyPI
 * - staging → platform git @ `VITE_SDK_GIT_REF` (default main)
 * - local → editable checkout when `VITE_SDK_EDITABLE_PATH` is set, else git @ main
 *
 * `VITE_SDK_EDITABLE_PATH` applies only on localhost — never on staging or production builds.
 */
export function sdkInstallChannel(apiUrl = config.apiUrl): SdkInstallChannel {
  const host = apiHost(apiUrl);
  if (isLocalApiHost(host) && config.sdkEditablePath) {
    return "editable";
  }
  if (isStagingApiHost(host) || isLocalApiHost(host)) {
    return "git";
  }
  return "pypi";
}

function gitDependencySpec(extra?: "tracing"): string {
  const pkg = extra === "tracing" ? "overmind[tracing]" : "overmind";
  return `"${pkg} @ ${PLATFORM_SDK_GIT}@${config.sdkGitRef}#subdirectory=overmind"`;
}

/**
 * Example `pip install` for manual terminal blocks — not the agent prompt path.
 * Production → PyPI; staging/local → git@main; local + `VITE_SDK_EDITABLE_PATH` → editable.
 */
export function sdkPipInstall(apiUrl = config.apiUrl, extra?: "tracing"): string {
  const channel = sdkInstallChannel(apiUrl);
  if (channel === "editable") {
    const suffix = extra === "tracing" ? "[tracing]" : "";
    return `pip install -e "${config.sdkEditablePath}${suffix}"`;
  }
  if (channel === "git") {
    return `pip install ${gitDependencySpec(extra)}`;
  }
  const spec = extra === "tracing" ? "overmind[tracing]" : "overmind";
  return extra === "tracing" ? `pip install "${spec}"` : `pip install ${spec}`;
}

/** Maps Console API host onto `overmind init --env` (local / staging / production). */
export function mcpInitEnvFlag(apiUrl: string): string {
  const host = apiHost(apiUrl);
  if (isLocalApiHost(host)) return " --env local";
  if (isStagingApiHost(host)) return " --env staging";
  return "";
}

/** `--ide` plus `--env` when the Console host is not production. */
export function initCliFlags(client: McpClient, apiUrl = config.apiUrl): string {
  const envFlag = mcpInitEnvFlag(apiUrl);
  return `--ide ${client}${envFlag}`;
}

/** Human-readable package source label for agent prompts. */
function sdkInstallSource(apiUrl = config.apiUrl): string {
  const channel = sdkInstallChannel(apiUrl);
  if (channel === "editable") {
    return `overmind editable install (${config.sdkEditablePath})`;
  }
  if (channel === "git") {
    return `overmind from git (${PLATFORM_SDK_GIT}@${config.sdkGitRef}#subdirectory=overmind)`;
  }
  return "overmind from PyPI";
}

function sdkInstallChannelRule(channel: SdkInstallChannel): string {
  if (channel === "pypi") {
    return "Use the published PyPI package only — do not install from git or a local checkout.";
  }
  if (channel === "git") {
    return `Use the platform git repo at @${config.sdkGitRef} only — do not use PyPI or a local editable checkout.`;
  }
  return `Use the local checkout at ${config.sdkEditablePath} only — do not use PyPI or git.`;
}

function dependencyCommands(channel: SdkInstallChannel): {
  poetryAdd: string;
  requirementsLine: string;
  uvAdd: string;
} {
  const gitDep = gitDependencySpec();
  if (channel === "editable") {
    const path = config.sdkEditablePath;
    return {
      poetryAdd: `poetry add --editable "${path}"`,
      requirementsLine: `-e "${path}"`,
      uvAdd: `uv add --editable "${path}"`,
    };
  }
  if (channel === "git") {
    return {
      poetryAdd: `poetry add ${gitDep}`,
      requirementsLine: gitDep,
      uvAdd: `uv add ${gitDep}`,
    };
  }
  return {
    poetryAdd: "poetry add overmind",
    requirementsLine: "overmind",
    uvAdd: "uv add overmind",
  };
}

/** Install instructions for coding-agent prompts — manifest-aware, not venv-only. */
export function projectDependencyInstallGuidance(apiUrl = config.apiUrl): string {
  const channel = sdkInstallChannel(apiUrl);
  const packageSource = sdkInstallSource(apiUrl);
  const { poetryAdd, requirementsLine, uvAdd } = dependencyCommands(channel);

  return [
    `Install overmind (${packageSource}). ${sdkInstallChannelRule(channel)}`,
    "Record it in the project dependency manifest using this repo's package manager — detect uv.lock, poetry.lock, pyproject.toml, or requirements.txt before choosing; do not assume pip.",
    "Do not use venv-only installs that leave the manifest unchanged (e.g. uv pip install, bare pip install into an active venv).",
    `When uv is the toolchain: ${uvAdd}, not uv pip install.`,
    `When poetry is the toolchain: ${poetryAdd}.`,
    `For requirements.txt-only repos: add ${requirementsLine}, then install from that file.`,
  ].join(" ");
}

/** Env exports, `overmind init`, and optional first sync to mint the console project. */
export function projectInitShellBlock(
  client: McpClient,
  apiKey: string,
  apiUrl = config.apiUrl,
  projectId?: string,
  initialSync = false
): string {
  const url = apiUrl.replace(/\/$/, "");
  const lines = [
    `export OVERMIND_API_URL=${url}`,
    ...(projectId ? [`export OVERMIND_PROJECT_ID=${projectId}`] : []),
    `export OVERMIND_API_KEY=${apiKey}`,
    `overmind init ${initCliFlags(client, url)}`,
    ...(initialSync ? ["overmind sync"] : []),
  ];
  return lines.join("\n");
}

export function mcpInitCommand(
  client: McpClient,
  apiKey: string,
  apiUrl = config.apiUrl,
  extra?: "tracing",
  projectId?: string
): string {
  return [
    sdkPipInstall(apiUrl, extra),
    ...(projectId ? [`export OVERMIND_PROJECT_ID=${projectId}`] : []),
    `export OVERMIND_API_KEY=${apiKey}`,
    `overmind init ${initCliFlags(client, apiUrl)}`,
    "overmind sync",
  ].join("\n");
}

/** Manual terminal block: example install line plus init exports. */
export function manualSetupCommand(
  client: McpClient,
  apiKey: string,
  apiUrl = config.apiUrl,
  projectId?: string
): string {
  return `${sdkPipInstall(apiUrl)}
${projectInitShellBlock(client, apiKey, apiUrl, projectId, true)}`;
}

const OVERMIND_SKILL_ONBOARD = "references/onboard.md";

/** Langfuse-style paste — install, export credentials, init, then follow onboard skill. */
export function onboardWithAiBootstrapPrompt(
  client: McpClient,
  apiKey: string,
  apiUrl = config.apiUrl
): string {
  const url = apiUrl.replace(/\/$/, "");
  const initCmd = `overmind init ${initCliFlags(client, url)}`;
  return [
    projectDependencyInstallGuidance(url),
    "The account-scoped API key below is a temporary bootstrap credential. Export it only in this shell session; do not write it into project files.",
    `export OVERMIND_API_URL=${url}`,
    `export OVERMIND_API_KEY=${apiKey}`,
    initCmd,
    "Run `overmind sync`. It creates the project, stores the final project-scoped credential locally, and updates this IDE's MCP configuration. Reload the IDE once after sync; do not re-export the key or run init again.",
    `Then open ${OVERMIND_SKILL_ONBOARD} from the installed overmind skill and follow it through phase 3.`,
  ].join("\n\n");
}

/** Instruction handed to the coding agent, addressed to the agent the user picked. */
export function telemetrySetupPrompt(client: McpClient): string {
  const meta = mcpClientMeta(client);
  return [
    `Instrument this repository using the Overmind MCP and skill setup in ${meta.label}. Do not rerun \`overmind init\` for an already configured client. If ${meta.label} has no Overmind MCP entry and onboarding sync has completed, run \`overmind init --ide ${client}\` once; it reuses the saved project credential. If MCP authentication is pending, stop and ask the user to complete onboarding with \`overmind sync\`, then reload ${meta.label} once; never configure MCP with the temporary account bootstrap key.`,
    "Install `overmind[tracing]` if tracing is not already importable, using the repository's dependency manifest and its existing package manager.",
    "For local runs, the tracing SDK reuses the credential saved by `overmind sync`; deployed processes still require `OVERMIND_API_KEY` in their runtime secret configuration.",
    "Follow the canonical instrumentation recipe in `references/telemetry.md`: call get_instrumentation_plan with no capability for project-wide work (or the supplied capability for scoped work), apply every returned placement as an exact ticket, and preserve each capability_id, key, required_scope, required_spans, identity, grain, and target verbatim.",
    "After applying the tickets, report the changed files and any checks performed, then ask them to choose Real run (recommended) or Smoke run; do not run either before explicit approval. Generate a unique correlation value, flush, query_traces with the narrowest correlation and all_spans=true, require exactly one matching server trace after bounded polling, read overmind://traces/{trace_id}, and call verify_instrumentation with the complete supplied spans. Treat zero or multiple matches, truncated resources, and over-limit spans as non-pass; real-run retries require fresh approval unless a bounded retry count and exact input were approved. Report application outcome separately from instrumentation status.",
  ].join(" ");
}
