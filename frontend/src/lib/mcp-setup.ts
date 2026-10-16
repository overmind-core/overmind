import { config } from "@/config";

export type McpClient = "cursor" | "claude" | "opencode" | "codex";

export const MCP_CLIENTS: { id: McpClient; label: string; destination: string }[] = [
  { destination: ".cursor/mcp.json", id: "cursor", label: "Cursor" },
  { destination: "~/.claude.json", id: "claude", label: "Claude Code" },
  { destination: "opencode.json", id: "opencode", label: "opencode" },
  { destination: ".codex/config.toml", id: "codex", label: "Codex" },
];

export const API_KEY_PLACEHOLDER = "<your-api-key>";
export const MIN_ONBOARDING_SDK_VERSION = "0.1.95";
export const SDK_PREFLIGHT_COMMAND = `python -c "import sys, overmind; from importlib.metadata import version; from packaging.version import Version; from overmind.__main__ import app; from overmind.utils import convert_json_to_toml; assert sys.version_info >= (3, 10), 'Overmind onboarding requires Python 3.10+'; print('Python:', sys.executable); print('Overmind:', version('overmind')); print('Package:', overmind.__file__); assert Version(version('overmind')) >= Version('${MIN_ONBOARDING_SDK_VERSION}'), 'Overmind onboarding requires overmind>=${MIN_ONBOARDING_SDK_VERSION}'"`;

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
    return `python -m pip install -e "${config.sdkEditablePath}${suffix}"`;
  }
  const spec = extra === "tracing" ? "overmind[tracing]" : "overmind";
  return `python -m pip install "${spec}>=${MIN_ONBOARDING_SDK_VERSION}"`;
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
    poetryAdd: `poetry add "overmind>=${MIN_ONBOARDING_SDK_VERSION}"`,
    requirementsLine: `overmind>=${MIN_ONBOARDING_SDK_VERSION}`,
    uvAdd: `uv add "overmind>=${MIN_ONBOARDING_SDK_VERSION}"`,
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
    "For application SDK installs, do not use venv-only installs that leave the manifest unchanged (e.g. uv pip install, bare pip install into an active venv). Dedicated isolated onboarding tooling is exempt.",
    `When uv is the toolchain: ${uvAdd}, not uv pip install.`,
    `When poetry is the toolchain: ${poetryAdd}.`,
    `For requirements.txt-only repos: add ${requirementsLine}, then install from that file.`,
    `Onboarding tooling requires Python 3.10+ and overmind>=${MIN_ONBOARDING_SDK_VERSION}. If a lockfile, Python requirement, or dependency conflict prevents that install, report the exact conflict and use the isolated tooling recovery below; do not silently accept an older SDK or change the project's supported Python range. The manifest requirement applies to the application SDK; isolated onboarding tooling is explicitly allowed without adding it to application dependencies.`,
  ].join(" ");
}

function isolatedToolingGuidance(apiUrl: string): string {
  const source =
    sdkInstallChannel(apiUrl) === "editable"
      ? `--with-editable "${config.sdkEditablePath}" --from overmind`
      : `--from "overmind==${MIN_ONBOARDING_SDK_VERSION}"`;
  const runner = `uv tool run --isolated --python 3.11 ${source} python`;
  return `Isolated tooling recovery: if the project SDK cannot provide the CLI/converter, continue using \`${runner}\` in place of python for BOTH preflight commands, init, chassis, conversion, skills sync and capability sync. Keep the repository root as the working directory and use this same runner throughout, including after reload. This verified tool environment is permitted; do not stop merely because it is outside the application environment, and do not accidentally resolve an unrelated global executable. Record the runner and version in the progress handoff without credentials. If uv is unavailable, create a dedicated tooling virtual environment with Python 3.10+ and install the same named source there, then run both preflight commands with its explicit Python path. Keep the existing project ID, capability IDs, endpoint and saved credentials; when bootstrap already succeeded, resume scanning and capability sync without init or project creation. Tool isolation does not install tracing into the application: verify the tracing SDK separately in the application environment before adding imports or decorators. If that dependency is incompatible, finish capability sync and report instrumentation as pending with the exact conflict; do not claim full onboarding is complete.`;
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
    SDK_PREFLIGHT_COMMAND,
    "python -m overmind --help",
    `export OVERMIND_API_URL=${url}`,
    ...(projectId ? [`export OVERMIND_PROJECT_ID=${projectId}`] : []),
    `export OVERMIND_API_KEY=${apiKey}`,
    `python -m overmind init ${initCliFlags(client, url)}`,
    ...(initialSync ? ["python -m overmind sync"] : []),
  ];
  return lines.join(" &&\n");
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
    SDK_PREFLIGHT_COMMAND,
    "python -m overmind --help",
    `export OVERMIND_API_URL=${apiUrl.replace(/\/$/, "")}`,
    ...(projectId ? [`export OVERMIND_PROJECT_ID=${projectId}`] : []),
    `export OVERMIND_API_KEY=${apiKey}`,
    `python -m overmind init ${initCliFlags(client, apiUrl)}`,
    "python -m overmind sync",
  ].join(" &&\n");
}

/** Manual terminal block: example install line plus init exports. */
export function manualSetupCommand(
  client: McpClient,
  apiKey: string,
  apiUrl = config.apiUrl,
  projectId?: string
): string {
  return `${sdkPipInstall(apiUrl)} &&
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
  const env = `OVERMIND_API_URL=${url} OVERMIND_API_KEY=${apiKey}`;
  const reload =
    client === "claude"
      ? "Then ask the user to exit Claude Code and run `claude -c` to continue with the Overmind MCP server loaded."
      : "Reload the IDE once after sync.";
  return [
    projectDependencyInstallGuidance(url),
    "In every command below, replace python with uv run python for uv, poetry run python for Poetry, or the project's virtual-environment Python executable, unless using the isolated tooling recovery below. Invoke the CLI as python -m overmind, never uv run overmind or a bare overmind executable: those can resolve to a global CLI when the project package has none. This is Overmind's version-consistency requirement, not a user-imposed restriction.",
    isolatedToolingGuidance(url),
    `Before init or the first sync, run \`${SDK_PREFLIGHT_COMMAND}\` and \`python -m overmind --help\` using the selected Python runner. Require both commands to succeed, the reported version to be at least ${MIN_ONBOARDING_SDK_VERSION}, and the interpreter/package paths to match the selected project or isolated tool environment (or the specified editable source). Missing CLI/converter or a mismatched path means installation is incomplete: use isolated tooling recovery and rerun these checks before creating a project.`,
    "The account-scoped API key below is a temporary bootstrap credential. Shell exports may not persist between commands, so prefix each command with it as shown; do not write it into project files.",
    `${env} python -m overmind init ${initCliFlags(client, url)}`,
    `Keep the destination explicit on every sync, including after reload: \`python -m overmind sync --api-url ${url}\`. After bootstrap, use the saved project credential without the bootstrap key. An inherited OVERMIND_API_URL must not redirect this connection to another environment.`,
    `Before the first sync, open ${OVERMIND_SKILL_ONBOARD} and references/onboarding-progress.md from the installed overmind skill. Show the full onboarding roadmap and data disclosure, then use its numbered progress updates throughout.`,
    `Run \`${env} python -m overmind sync\`. It creates the project, stores the final project-scoped credential locally, and updates this IDE's MCP configuration. ${reload} Do not pass the bootstrap key again or run init again.`,
    `After reload, repeat both preflight commands with the selected Python runner before scanning or capability sync. Preserve any existing project-id, capability IDs, endpoint and saved credentials while repairing a dependency. Refresh installed guidance with \`python -m overmind skills sync overmind --ide ${client}\` after an SDK upgrade, then reopen ${OVERMIND_SKILL_ONBOARD} and continue without repeating bootstrap sync. The isolated tooling recovery in this request also applies if an older installed skill requires project-local tooling only.`,
    "During capability discovery, follow references/setup.md's scan coverage audit: inspect every registered tool's source-backed branches and caller outcomes, keep distinct execution stages, and resolve transitions to actual step anchors. Trace model and prompt defaults through constructor/configuration precedence and template predicates; anchor utility implementations beyond their wrappers. Review distinct success, target and continuation outcomes even when conversion passes. A tool list and a generic observe/decide/act summary are not a finished scan. If conversion reports incomplete detail, repair the analysis JSON from the source and retry conversion; preserve the previous snapshot and continue without repeating init. Do not fabricate branches or remove tools to satisfy validation.",
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
    "Apply each placement's `required_decorator` verbatim at `target.qualname` in `target.file`, keeping `capability_id` exact. Expectations that describe one span go on `@overmind.observe(expectations=[...])`; capability-wide expectations belong in the eval matrix, not the code. When delegating, give each file one owner so two workers never edit the same file.",
    "After applying the placements, report the changed files and checks, generate a unique verification correlation, and ask the user to choose Real run (recommended) or Smoke run. Present the exact command or input, capability, environment, provider/model, expected side effects, correlation value, and approved attempt count; mark unknown fields as needing user input. Do not run either mode before explicit approval. Stamp the approved correlation as `conversation.id` with the application's existing mechanism or `overmind.set_conversation_id`, run only the approved input, flush, and poll `query_traces(session=<correlation>, all_spans=false, limit=2)` within a fixed bound. Require `page.total == 1` and pass that row's `trace_id` to `verify_instrumentation(trace_id=...)`; the server grades the ingested spans. Real-run retries require fresh approval unless an exact input and bounded attempt count were approved. Report application outcome separately from instrumentation status.",
  ].join(" ");
}
