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

export type SdkInstallChannel = "pypi" | "editable";

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
 * - all hosts → PyPI
 * - localhost + `VITE_SDK_EDITABLE_PATH` → editable checkout
 *
 * `VITE_SDK_EDITABLE_PATH` applies only on localhost — never on staging or production builds.
 */
export function sdkInstallChannel(apiUrl = config.apiUrl): SdkInstallChannel {
  const host = apiHost(apiUrl);
  if (isLocalApiHost(host) && config.sdkEditablePath) {
    return "editable";
  }
  return "pypi";
}

/**
 * Example `pip install` for manual terminal blocks — not the agent prompt path.
 * PyPI everywhere; localhost + `VITE_SDK_EDITABLE_PATH` → editable.
 */
export function sdkPipInstall(apiUrl = config.apiUrl, extra?: "tracing"): string {
  const channel = sdkInstallChannel(apiUrl);
  if (channel === "editable") {
    const suffix = extra === "tracing" ? "[tracing]" : "";
    return `pip install -e "${config.sdkEditablePath}${suffix}"`;
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
  return "overmind from PyPI";
}

function sdkInstallChannelRule(channel: SdkInstallChannel): string {
  if (channel === "pypi") {
    return "Use the published PyPI package only — do not install from git or a local checkout.";
  }
  return `Use the local checkout at ${config.sdkEditablePath} only — do not use PyPI or git.`;
}

function dependencyCommands(channel: SdkInstallChannel): {
  poetryAdd: string;
  requirementsLine: string;
  uvAdd: string;
} {
  if (channel === "editable") {
    const path = config.sdkEditablePath;
    return {
      poetryAdd: `poetry add --editable "${path}"`,
      requirementsLine: `-e "${path}"`,
      uvAdd: `uv add --editable "${path}"`,
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
    "Run all Overmind and Python commands in this project's environment: use uv run for uv, poetry run for Poetry, or the project's virtual-environment executables. Do not use a globally installed overmind.",
    "The account-scoped API key below is a temporary bootstrap credential. Export it only in this shell session; do not write it into project files.",
    `export OVERMIND_API_URL=${url}`,
    `export OVERMIND_API_KEY=${apiKey}`,
    initCmd,
    `Before the first sync, open ${OVERMIND_SKILL_ONBOARD} and references/onboarding-progress.md from the installed overmind skill. Show the full onboarding roadmap and data disclosure, then use its numbered progress updates throughout.`,
    "Run `overmind sync`. It creates the project, stores the final project-scoped credential locally, and updates this IDE's MCP configuration. Reload the IDE once after sync; do not re-export the key or run init again.",
    `After reload, reopen ${OVERMIND_SKILL_ONBOARD} from the installed overmind skill and continue the remaining workflow without repeating bootstrap sync.`,
  ].join("\n\n");
}

/** Instruction handed to the coding agent, addressed to the agent the user picked. */
export function telemetrySetupPrompt(client: McpClient): string {
  const meta = mcpClientMeta(client);
  return [
    `Instrument this repository using the Overmind MCP and skill setup in ${meta.label}. Do not rerun \`overmind init\` for an already configured client. If ${meta.label} has no Overmind MCP entry and onboarding sync has completed, run \`overmind init --ide ${client}\` once; it reuses the saved project credential. If MCP authentication is pending, stop and ask the user to complete onboarding with \`overmind sync\`, then reload ${meta.label} once; never configure MCP with the temporary account bootstrap key.`,
    "Install `overmind[tracing]` if tracing is not already importable, using the repository's dependency manifest and its existing package manager.",
    "For local runs, the tracing SDK reuses the credential saved by `overmind sync`; deployed processes still require `OVERMIND_API_KEY` in their runtime secret configuration.",
    "Follow `references/telemetry.md`. Call `get_instrumentation_plan` with no capability for project-wide work or the supplied capability for scoped work. If it returns `human_action` or no placements, report the instruction and stop this attempt.",
    "Apply every placement as an exact ticket. When delegating, compute touched files from `target.file` and every `required_spans[].target.file`, coalesce overlapping tickets under one owner, and never let two workers edit the same file. Preserve every ticket field verbatim, including `key`, `behaviour_id`, `version_id`, `version_analyzed_sha`, `contract_fingerprint`, `capability`, `capability_id`, `placement_mode`, `allowed_keys`, `grain`, `target`, `required_scope`, `required_spans`, and `required_identity`.",
    "After applying the tickets, report the changed files and checks, generate a unique verification correlation, and ask the user to choose Real run (recommended) or Smoke run. Present the exact command or input, capability, environment, provider/model, expected side effects, correlation value, and approved attempt count; mark unknown fields as needing user input. Do not run either mode before explicit approval. Stamp the approved correlation as `conversation.id` with the application's existing mechanism or `overmind.set_conversation_id`, run only the approved input, flush, and poll `query_traces(session=<correlation>, all_spans=false, limit=2)` within a fixed bound. Require `page.total == 1`, read that row's `overmind://traces/{trace_id}` resource, require `truncated == false` and `span_count == len(spans)`, and pass the supplied spans unchanged to `verify_instrumentation`. Real-run retries require fresh approval unless an exact input and bounded attempt count were approved. Report application outcome separately from instrumentation status.",
  ].join(" ");
}
