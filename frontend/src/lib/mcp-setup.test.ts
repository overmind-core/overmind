import { describe, expect, it, vi } from "vitest";

import {
  MCP_CLIENTS,
  manualSetupCommand,
  mcpInitCommand,
  mcpInitEnvFlag,
  onboardWithAiBootstrapPrompt,
  projectDependencyInstallGuidance,
  projectInitShellBlock,
  SDK_PREFLIGHT_COMMAND,
  sdkPipInstall,
  telemetrySetupPrompt,
} from "./mcp-setup";

const KEY = "om_live_testkey";
const PROJECT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";
const PROD = "https://api.overmindlab.ai";
const STAGING = "https://api-staging.overmindlab.ai";
const LOCAL = "http://localhost:8000";

describe("mcp setup snippets", () => {
  it.each([
    PROD,
    STAGING,
    LOCAL,
    "https://api.customer.example",
  ])("keeps onboarding commands on the configured endpoint %s", (host) => {
    const prompt = onboardWithAiBootstrapPrompt("codex", KEY, host);
    expect(prompt).toContain(`python -m overmind sync --api-url ${host}`);
    expect(mcpInitCommand("codex", KEY, host)).toContain(`export OVERMIND_API_URL=${host}`);
    if (host !== STAGING) expect(prompt).not.toContain(STAGING);
  });

  it("checks the runtime before bootstrap and again after reload for every client", () => {
    for (const { id } of MCP_CLIENTS) {
      const prompt = onboardWithAiBootstrapPrompt(id, KEY, PROD);
      expect(prompt.indexOf(SDK_PREFLIGHT_COMMAND)).toBeLessThan(
        prompt.indexOf(`${KEY} python -m overmind init`)
      );
      expect(prompt).toContain("from overmind.__main__ import app");
      expect(prompt).toContain("assert Version(version('overmind')) >= Version('0.1.94')");
      expect(prompt).toContain("from overmind.utils import convert_json_to_toml");
      expect(prompt).toContain("uv run python");
      expect(prompt).toContain("poetry run python");
      expect(prompt).toContain("After reload, repeat both preflight commands");
      expect(prompt).toContain("before scanning or capability sync");
      expect(prompt).toContain(`python -m overmind sync --api-url ${PROD}`);
      expect(prompt).toContain("An inherited OVERMIND_API_URL must not redirect");
      expect(prompt).toContain(
        "Preserve any existing project-id, capability IDs, endpoint and saved credentials"
      );
      expect(prompt).toContain(`python -m overmind skills sync overmind --ide ${id}`);
      expect(prompt).toContain(
        "Overmind's version-consistency requirement, not a user-imposed restriction"
      );
    }
  });

  it("requires a compatible dependency without silently changing project Python support", () => {
    const guidance = projectDependencyInstallGuidance(PROD);
    expect(guidance).toContain('uv add "overmind>=0.1.94"');
    expect(guidance).toContain('poetry add "overmind>=0.1.94"');
    expect(guidance).toContain("Python 3.10+");
    expect(guidance).toContain("report the exact conflict");
    expect(guidance).toContain(
      "do not silently accept an older SDK or change the project's supported Python range"
    );
  });

  it("recovers with isolated tooling while preserving the connection and application dependencies", () => {
    for (const { id } of MCP_CLIENTS) {
      const prompt = onboardWithAiBootstrapPrompt(id, KEY, PROD);
      expect(prompt).toContain(
        'uv tool run --isolated --python 3.11 --from "overmind==0.1.94" python'
      );
      expect(prompt).toContain(
        "BOTH preflight commands, init, chassis, conversion, skills sync and capability sync"
      );
      expect(prompt).toContain("including after reload");
      expect(prompt).toContain("without init or project creation");
      expect(prompt).toContain("finish capability sync and report instrumentation as pending");
      expect(prompt).toContain("older installed skill requires project-local tooling only");
      expect(prompt).not.toContain("repair the project dependency before continuing");
    }
  });

  it("installs from PyPI on all hosts", () => {
    for (const host of [STAGING, LOCAL, PROD]) {
      expect(sdkPipInstall(host)).toBe('python -m pip install "overmind>=0.1.94"');
      expect(sdkPipInstall(host, "tracing")).toBe(
        'python -m pip install "overmind[tracing]>=0.1.94"'
      );
    }
  });

  it("flags --env from the Console API host", () => {
    expect(mcpInitEnvFlag(PROD)).toBe("");
    expect(mcpInitEnvFlag(`${PROD}/`)).toBe("");
    expect(mcpInitEnvFlag(LOCAL)).toBe(" --env local");
    expect(mcpInitEnvFlag("https://staging.overmindlab.ai")).toBe(" --env staging");
  });

  it("builds overmind init for cursor, claude, opencode, and codex", () => {
    for (const extra of [undefined, "tracing"] as const) {
      const command = mcpInitCommand("cursor", KEY, PROD, extra);
      expect(command.split(" &&\n")).toEqual([
        sdkPipInstall(PROD, extra),
        SDK_PREFLIGHT_COMMAND,
        "python -m overmind --help",
        `export OVERMIND_API_URL=${PROD}`,
        `export OVERMIND_API_KEY=${KEY}`,
        "python -m overmind init --ide cursor",
        "python -m overmind sync",
      ]);
    }
    expect(mcpInitCommand("cursor", KEY, STAGING, "tracing")).toContain(
      "python -m overmind init --ide cursor --env staging"
    );
    expect(mcpInitCommand("claude", KEY, LOCAL)).toContain(
      "overmind init --ide claude --env local"
    );
    expect(mcpInitCommand("opencode", KEY, PROD)).toContain("overmind init --ide opencode");
    expect(mcpInitCommand("codex", KEY, LOCAL)).toContain("overmind init --ide codex --env local");
  });

  it("addresses the setup prompt to the selected agent", () => {
    expect(telemetrySetupPrompt("claude")).toContain("Claude Code");
    expect(telemetrySetupPrompt("claude")).toContain("Do not rerun `overmind init`");
    expect(telemetrySetupPrompt("claude")).toContain("overmind init --ide claude");
    expect(telemetrySetupPrompt("cursor")).not.toContain("codex");
  });

  it("reuses durable local authentication in the instrumentation prompt", () => {
    expect(telemetrySetupPrompt("claude")).toContain("overmind sync");
    expect(telemetrySetupPrompt("codex")).toContain("saved by `overmind sync`");
    expect(telemetrySetupPrompt("cursor")).toContain("deployed processes");
    expect(telemetrySetupPrompt("cursor")).toContain("OVERMIND_API_KEY");
    expect(telemetrySetupPrompt("cursor")).not.toContain("--env");
    expect(telemetrySetupPrompt("cursor")).toContain("overmind[tracing]");
  });

  it("binds each IDE provider to its own copied prompt", () => {
    for (const { id, label } of MCP_CLIENTS) {
      expect(telemetrySetupPrompt(id)).toContain(`Overmind MCP and skill setup in ${label}`);
    }
  });

  it("names the current MCP instrumentation tools", () => {
    const prompt = telemetrySetupPrompt("codex");
    expect(prompt).toContain("get_instrumentation_plan");
    expect(prompt).toContain("query_traces");
    expect(prompt).toContain("verify_instrumentation(trace_id=...)");
    expect(prompt).toContain("verify_instrumentation");
    expect(prompt).toContain("capability_id");
    expect(prompt).toContain("Real run (recommended)");
    expect(prompt).toContain("Smoke run");
    expect(prompt).toContain("explicit approval");
    expect(prompt).toContain("human_action");
    expect(prompt).toContain("required_decorator");
    expect(prompt).toContain("target.qualname");
    expect(prompt).toContain("exact command or input");
    expect(prompt).toContain("conversation.id");
    expect(prompt).toContain("query_traces(session=<correlation>, all_spans=false, limit=2)");
    expect(prompt).toContain("page.total == 1");
    expect(prompt).toContain("Real-run retries require fresh approval");
    expect(prompt).toContain("application outcome separately from instrumentation status");
    expect(prompt).not.toContain("get_instrumentation_context");
    expect(prompt).not.toContain("verify_instrumentation_spans");
    expect(prompt).not.toContain("list_traces");
    expect(prompt).not.toContain("get_trace");
    expect(prompt).not.toContain("get_capability");
    expect(prompt).not.toContain("never ask for or execute a real application task");
  });

  it("builds init shell block with or without project id", () => {
    expect(projectInitShellBlock("cursor", KEY, PROD, PROJECT_ID)).toBe(
      `${SDK_PREFLIGHT_COMMAND} &&
python -m overmind --help &&
export OVERMIND_API_URL=${PROD} &&
export OVERMIND_PROJECT_ID=${PROJECT_ID} &&
export OVERMIND_API_KEY=${KEY} &&
python -m overmind init --ide cursor`
    );
    expect(projectInitShellBlock("cursor", KEY, PROD)).toBe(
      `${SDK_PREFLIGHT_COMMAND} &&
python -m overmind --help &&
export OVERMIND_API_URL=${PROD} &&
export OVERMIND_API_KEY=${KEY} &&
python -m overmind init --ide cursor`
    );
    expect(projectInitShellBlock("cursor", KEY, PROD, undefined, true)).toBe(
      `${SDK_PREFLIGHT_COMMAND} &&
python -m overmind --help &&
export OVERMIND_API_URL=${PROD} &&
export OVERMIND_API_KEY=${KEY} &&
python -m overmind init --ide cursor &&
python -m overmind sync`
    );
    expect(projectInitShellBlock("claude", KEY, LOCAL)).toContain("--env local");
    expect(projectInitShellBlock("codex", KEY, LOCAL)).toContain("--env local");
  });

  it("combines pip example and init for manual setup", () => {
    expect(manualSetupCommand("cursor", KEY, PROD, PROJECT_ID)).toBe(
      `${sdkPipInstall(PROD)} &&
${SDK_PREFLIGHT_COMMAND} &&
python -m overmind --help &&
export OVERMIND_API_URL=${PROD} &&
export OVERMIND_PROJECT_ID=${PROJECT_ID} &&
export OVERMIND_API_KEY=${KEY} &&
python -m overmind init --ide cursor &&
python -m overmind sync`
    );
  });

  it("guides manifest-aware install on production hosts", () => {
    const guidance = projectDependencyInstallGuidance(PROD);
    expect(guidance).toContain("from PyPI");
    expect(guidance).toContain('uv add "overmind>=0.1.94"');
    expect(guidance).toContain("do not install from git");
    expect(guidance).toContain("do not assume pip");
    const prompt = onboardWithAiBootstrapPrompt("cursor", KEY, PROD);
    expect(prompt).toContain("references/onboard.md");
    expect(prompt).toContain(
      `OVERMIND_API_URL=${PROD} OVERMIND_API_KEY=${KEY} python -m overmind init --ide cursor`
    );
    expect(prompt).toContain(
      `Run \`OVERMIND_API_URL=${PROD} OVERMIND_API_KEY=${KEY} python -m overmind sync\``
    );
    expect(prompt).not.toContain("export ");
    expect(prompt).toContain("temporary bootstrap credential");
    expect(prompt).toContain("Do not pass the bootstrap key again or run init again");
    expect(onboardWithAiBootstrapPrompt("claude", KEY, PROD)).toContain("`claude -c`");
    expect(prompt).not.toContain("OVERMIND_PROJECT_ID");
    expect(prompt).not.toContain("pip install overmind");
  });

  it("guides PyPI dependency via uv/poetry on staging", () => {
    const guidance = projectDependencyInstallGuidance(STAGING);
    expect(guidance).toContain("from PyPI");
    expect(guidance).toContain('uv add "overmind>=0.1.94"');
    expect(guidance).toContain("do not install from git");
    const prompt = onboardWithAiBootstrapPrompt("cursor", KEY, STAGING);
    expect(prompt).toContain("overmind init --ide cursor --env staging");
    expect(prompt).toContain('uv add "overmind>=0.1.94"');
  });

  it("guides PyPI dependency via uv/poetry on localhost without editable path", () => {
    const guidance = projectDependencyInstallGuidance(LOCAL);
    expect(guidance).toContain("from PyPI");
    expect(guidance).toContain('uv add "overmind>=0.1.94"');
    expect(guidance).toContain("do not install from git");
    const prompt = onboardWithAiBootstrapPrompt("cursor", KEY, LOCAL);
    expect(prompt).toContain("do not assume pip");
    expect(prompt).toContain('uv add "overmind>=0.1.94"');
    expect(prompt).toContain("overmind init --ide cursor --env local");
    expect(prompt).not.toContain("--editable");
  });

  it("uses editable path only on localhost when VITE_SDK_EDITABLE_PATH is set", async () => {
    vi.stubEnv("VITE_SDK_EDITABLE_PATH", "/Users/dom/git/overmind/overmind/overmind");
    vi.resetModules();
    const {
      onboardWithAiBootstrapPrompt: bootstrap,
      projectDependencyInstallGuidance: guidance,
      sdkInstallChannel: channel,
    } = await import("./mcp-setup");
    expect(channel(LOCAL)).toBe("editable");
    expect(channel(PROD)).toBe("pypi");
    expect(channel(STAGING)).toBe("pypi");
    expect(guidance(LOCAL)).toContain(
      "editable install (/Users/dom/git/overmind/overmind/overmind)"
    );
    expect(guidance(LOCAL)).toContain("do not use PyPI or git");
    expect(guidance(LOCAL)).toContain(
      'uv add --editable "/Users/dom/git/overmind/overmind/overmind"'
    );
    expect(guidance(PROD)).toContain("from PyPI");
    expect(guidance(PROD)).not.toContain("--editable");
    const prompt = bootstrap("cursor", KEY, LOCAL);
    expect(prompt).toContain("/Users/dom/git/overmind/overmind/overmind");
    expect(prompt).toContain('uv add --editable "/Users/dom/git/overmind/overmind/overmind"');
    expect(prompt).toContain(
      '--with-editable "/Users/dom/git/overmind/overmind/overmind" --from overmind python'
    );
    expect(prompt).not.toContain('--from "overmind==0.1.94"');
    expect(prompt).not.toMatch(/\bpip install -e\b/);
    vi.unstubAllEnvs();
    vi.resetModules();
  });
});
