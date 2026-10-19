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
export const SDK_PREFLIGHT_COMMAND = "python -m overmind --version";

const SKILL_ONBOARD = "the “Onboard a repository” section of SKILL.md";

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

/** Whichever installer the project uses; the dependency manifest stays untouched. */
function installGuidance(apiUrl: string): string {
  const spec = installSpec(apiUrl);
  const source = localSdkPath(apiUrl)
    ? "Use the local checkout only — do not use PyPI or git."
    : "Use the published PyPI package only — do not install from git or a local checkout.";
  return `Install overmind into the project's environment with the installer it already uses: \`uv pip install ${spec}\` for uv, \`poetry run pip install ${spec}\` for Poetry, otherwise \`${sdkPipInstall(apiUrl)}\`. ${source} Adding it to the dependency manifest is not required.`;
}

/** Fallback when the project cannot take the SDK (Python range, lockfile or dependency conflict). */
function isolatedToolingGuidance(apiUrl: string): string {
  const path = localSdkPath(apiUrl);
  const source = path
    ? `--with-editable "${path}" --from overmind`
    : `--from "overmind==${MIN_ONBOARDING_SDK_VERSION}"`;
  const runner = `uv tool run --isolated --python 3.11 ${source} python`;
  return `Requires Python 3.10+ and overmind>=${MIN_ONBOARDING_SDK_VERSION}. If the project cannot install it, report the exact conflict, never change the project's supported Python range, and use \`${runner}\` in place of python for every command below, from the repository root, including after reload. Tracing still has to be installed in the application environment; if that conflicts, finish capability sync and report instrumentation as pending.`;
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

/** Paste prompt: install, init with the bootstrap key, sync, then follow the onboard skill. */
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
    installGuidance(url),
    isolatedToolingGuidance(url),
    "In every command below, replace python with uv run python for uv, poetry run python for Poetry, or the project's virtual-environment Python, unless using the isolated tooling above. Invoke the CLI as python -m overmind, never uv run overmind or a bare overmind executable: those can resolve to a global CLI. This is Overmind's version-consistency requirement, not a user-imposed restriction.",
    `Before init or the first sync, run \`${SDK_PREFLIGHT_COMMAND}\`. It must succeed and report a version of at least ${MIN_ONBOARDING_SDK_VERSION}. Otherwise the install is incomplete: fix it before creating a project.`,
    "The account-scoped API key below is a temporary bootstrap credential. Shell exports may not persist between commands, so prefix each command with it as shown; do not write it into project files.",
    `${env} python -m overmind init ${initFlags(client, url)}`,
    `Keep the destination explicit on every sync, including after reload: \`python -m overmind sync --api-url ${url}\`. After bootstrap, use the saved project credential without the bootstrap key. An inherited OVERMIND_API_URL must not redirect this connection to another environment.`,
    `Before the first sync, open ${SKILL_ONBOARD} and its “Onboarding progress and data disclosure” section from the installed overmind skill. Show the full onboarding roadmap and data disclosure, then use its numbered progress updates throughout.`,
    `Run \`${env} python -m overmind sync\`. It creates the project, stores the final project-scoped credential locally, and updates this IDE's MCP configuration. ${reload} Do not pass the bootstrap key again or run init again.`,
    `After reload, repeat the preflight command before scanning or capability sync. Preserve any existing project-id, capability IDs, endpoint and saved credentials. After an SDK upgrade, refresh guidance with \`python -m overmind skills sync overmind --ide ${client}\`, reopen ${SKILL_ONBOARD} and continue without repeating bootstrap sync.`,
    "During capability discovery, follow the scan coverage audit in SKILL.md's “Local repository setup” section: inspect every registered tool's source-backed branches and caller outcomes, keep distinct execution stages, and resolve transitions to actual step anchors. Trace model and prompt defaults through constructor/configuration precedence and template predicates; anchor utility implementations beyond their wrappers. A tool list and a generic observe/decide/act summary are not a finished scan. If conversion reports incomplete detail, repair the analysis JSON from the source and retry conversion without repeating init. Do not fabricate branches or remove tools to satisfy validation.",
  ].join("\n\n");
}

/** Instruction handed to the coding agent, addressed to the agent the user picked. */
export function telemetrySetupPrompt(client: McpClient): string {
  const meta = mcpClientMeta(client);
  return [
    `Instrument this repository using the Overmind MCP and skill setup in ${meta.label}. Do not rerun \`overmind init\` for an already configured client. If ${meta.label} has no Overmind MCP entry and onboarding sync has completed, run \`overmind init --ide ${client}\` once; it reuses the saved project credential. If MCP authentication is pending, stop and ask the user to complete onboarding with \`overmind sync\`, then reload ${meta.label} once; never configure MCP with the temporary account bootstrap key.`,
    "Install `overmind[tracing]` if tracing is not already importable, with the installer the project already uses.",
    "For local runs, the tracing SDK reuses the credential saved by `overmind sync`; deployed processes still require `OVERMIND_API_KEY` in their runtime secret configuration.",
    "Follow the “Instrumentation” section of the installed overmind-observability skill. Call `get_instrumentation_plan` with no capability for project-wide work or the supplied capability for scoped work. If it returns `human_action` or no placements, report the instruction and stop this attempt.",
    "Apply each placement's `required_decorator` verbatim at `target.qualname` in `target.file`, keeping `capability_id` exact. Expectations that describe one span go on `@overmind.observe(expectations=[...])`; capability-wide expectations belong in the eval matrix, not the code. When delegating, give each file one owner so two workers never edit the same file.",
    "After applying the placements, report the changed files and checks, generate a unique verification correlation, and ask the user to choose Real run (recommended) or Smoke run. Present the exact command or input, capability, environment, provider/model, expected side effects, correlation value, and approved attempt count; mark unknown fields as needing user input. Do not run either mode before explicit approval. Stamp the approved correlation as `conversation.id` with the application's existing mechanism or `overmind.set_conversation_id`, run only the approved input, flush, and poll `query_traces(session=<correlation>, all_spans=false, limit=2)` within a fixed bound. Require `page.total == 1` and pass that row's `trace_id` to `verify_instrumentation(trace_id=...)`; the server grades the ingested spans. Real-run retries require fresh approval unless an exact input and bounded attempt count were approved. Report application outcome separately from instrumentation status.",
  ].join(" ");
}
