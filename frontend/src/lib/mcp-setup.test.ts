import { describe, expect, it, vi } from "vitest";

import {
  initCliFlags,
  MCP_CLIENTS,
  manualSetupCommand,
  mcpInitCommand,
  mcpInitEnvFlag,
  onboardWithAiBootstrapPrompt,
  projectDependencyInstallGuidance,
  projectInitShellBlock,
  sdkInstallChannel,
  sdkPipInstall,
  telemetrySetupPrompt,
} from "./mcp-setup";

const KEY = "om_live_testkey";
const PROJECT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";
const PROD = "https://api.overmindlab.ai";
const STAGING = "https://api-staging.overmindlab.ai";
const LOCAL = "http://localhost:8000";

describe("mcp setup snippets", () => {
  it("resolves install channel from the Console API host", () => {
    expect(sdkInstallChannel(PROD)).toBe("pypi");
    expect(sdkInstallChannel(STAGING)).toBe("pypi");
    expect(sdkInstallChannel(LOCAL)).toBe("pypi");
  });

  it("installs from PyPI on all hosts", () => {
    expect(sdkPipInstall(STAGING)).toBe("pip install overmind");
    expect(sdkPipInstall(LOCAL)).toBe("pip install overmind");
    expect(sdkPipInstall(PROD)).toBe("pip install overmind");
    expect(sdkPipInstall(PROD, "tracing")).toBe('pip install "overmind[tracing]"');
    expect(sdkPipInstall(STAGING, "tracing")).toBe('pip install "overmind[tracing]"');
  });

  it("flags --env from the Console API host", () => {
    expect(mcpInitEnvFlag(PROD)).toBe("");
    expect(mcpInitEnvFlag(`${PROD}/`)).toBe("");
    expect(mcpInitEnvFlag(LOCAL)).toBe(" --env local");
    expect(mcpInitEnvFlag("https://staging.overmindlab.ai")).toBe(" --env staging");
  });

  it("puts --ide and --env on the copied init command", () => {
    expect(initCliFlags("cursor", PROD)).toBe("--ide cursor");
    expect(initCliFlags("claude", LOCAL)).toBe("--ide claude --env local");
    expect(initCliFlags("codex", LOCAL)).toBe("--ide codex --env local");
  });

  it("builds overmind init for cursor, claude, opencode, and codex", () => {
    expect(mcpInitCommand("cursor", KEY, PROD)).toBe(
      `pip install overmind\nexport OVERMIND_API_KEY=${KEY}\novermind init --ide cursor\novermind sync`
    );
    expect(mcpInitCommand("cursor", KEY, PROD, "tracing")).toBe(
      `pip install "overmind[tracing]"\nexport OVERMIND_API_KEY=${KEY}\novermind init --ide cursor\novermind sync`
    );
    expect(mcpInitCommand("cursor", KEY, STAGING, "tracing")).toBe(
      `pip install "overmind[tracing]"\nexport OVERMIND_API_KEY=${KEY}\novermind init --ide cursor --env staging\novermind sync`
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
    expect(prompt).toContain("overmind://traces/{trace_id}");
    expect(prompt).toContain("verify_instrumentation");
    expect(prompt).toContain("capability_id");
    expect(prompt).toContain("Real run (recommended)");
    expect(prompt).toContain("Smoke run");
    expect(prompt).toContain("explicit approval");
    expect(prompt).toContain("human_action");
    expect(prompt).toContain("version_analyzed_sha");
    expect(prompt).toContain("required_spans[].target.file");
    expect(prompt).toContain("version_id");
    expect(prompt).toContain("contract_fingerprint");
    expect(prompt).toContain("required_identity");
    expect(prompt).toContain("exact command or input");
    expect(prompt).toContain("conversation.id");
    expect(prompt).toContain("query_traces(session=<correlation>, all_spans=false, limit=2)");
    expect(prompt).toContain("page.total == 1");
    expect(prompt).toContain("truncated == false");
    expect(prompt).toContain("span_count == len(spans)");
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
      `export OVERMIND_API_URL=${PROD}
export OVERMIND_PROJECT_ID=${PROJECT_ID}
export OVERMIND_API_KEY=${KEY}
overmind init --ide cursor`
    );
    expect(projectInitShellBlock("cursor", KEY, PROD)).toBe(
      `export OVERMIND_API_URL=${PROD}
export OVERMIND_API_KEY=${KEY}
overmind init --ide cursor`
    );
    expect(projectInitShellBlock("cursor", KEY, PROD, undefined, true)).toBe(
      `export OVERMIND_API_URL=${PROD}
export OVERMIND_API_KEY=${KEY}
overmind init --ide cursor
overmind sync`
    );
    expect(projectInitShellBlock("claude", KEY, LOCAL)).toContain("--env local");
    expect(projectInitShellBlock("codex", KEY, LOCAL)).toContain("--env local");
  });

  it("combines pip example and init for manual setup", () => {
    expect(manualSetupCommand("cursor", KEY, PROD, PROJECT_ID)).toBe(
      `${sdkPipInstall(PROD)}
export OVERMIND_API_URL=${PROD}
export OVERMIND_PROJECT_ID=${PROJECT_ID}
export OVERMIND_API_KEY=${KEY}
overmind init --ide cursor
overmind sync`
    );
  });

  it("guides manifest-aware install on production hosts", () => {
    const guidance = projectDependencyInstallGuidance(PROD);
    expect(guidance).toContain("from PyPI");
    expect(guidance).toContain("uv add overmind");
    expect(guidance).toContain("do not install from git");
    expect(guidance).toContain("do not assume pip");
    const prompt = onboardWithAiBootstrapPrompt("cursor", KEY, PROD);
    expect(prompt).toContain("references/onboard.md");
    expect(prompt).toContain(`export OVERMIND_API_URL=${PROD}`);
    expect(prompt).toContain(`export OVERMIND_API_KEY=${KEY}`);
    expect(prompt).toContain("overmind init --ide cursor");
    expect(prompt).toContain("temporary bootstrap credential");
    expect(prompt).toContain("Run `overmind sync`");
    expect(prompt).toContain("do not re-export the key or run init again");
    expect(prompt).not.toContain("OVERMIND_PROJECT_ID");
    expect(prompt).not.toContain("pip install overmind");
  });

  it("guides PyPI dependency via uv/poetry on staging", () => {
    const guidance = projectDependencyInstallGuidance(STAGING);
    expect(guidance).toContain("from PyPI");
    expect(guidance).toContain("uv add overmind");
    expect(guidance).toContain("do not install from git");
    const prompt = onboardWithAiBootstrapPrompt("cursor", KEY, STAGING);
    expect(prompt).toContain("overmind init --ide cursor --env staging");
    expect(prompt).toContain("uv add overmind");
  });

  it("guides PyPI dependency via uv/poetry on localhost without editable path", () => {
    const guidance = projectDependencyInstallGuidance(LOCAL);
    expect(guidance).toContain("from PyPI");
    expect(guidance).toContain("uv add overmind");
    expect(guidance).toContain("do not install from git");
    const prompt = onboardWithAiBootstrapPrompt("cursor", KEY, LOCAL);
    expect(prompt).toContain("do not assume pip");
    expect(prompt).toContain("uv add overmind");
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
    expect(prompt).not.toMatch(/\bpip install -e\b/);
    vi.unstubAllEnvs();
    vi.resetModules();
  });
});
