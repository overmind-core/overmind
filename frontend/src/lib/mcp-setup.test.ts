import { describe, expect, it, vi } from "vitest";

import {
  MCP_CLIENTS,
  mcpInitCommand,
  mcpInitEnvFlag,
  onboardingPrompt,
  SDK_PREFLIGHT_COMMAND,
  sdkPipInstall,
  telemetrySetupPrompt,
} from "./mcp-setup";

const KEY = "om_live_testkey";
const PROJECT_ID = "a1b2c3d4-e5f6-7890-abcd-ef1234567890";
const PROD = "https://api.overmindlab.ai";
const STAGING = "https://api-staging.overmindlab.ai";
const LOCAL = "http://localhost:8000";
const EDITABLE = "/Users/dom/git/overmind/overmind/overmind";

describe("mcp setup snippets", () => {
  it("hands the agent the hosted setup guide with no credential", () => {
    expect(onboardingPrompt("https://console.overmindlab.ai/", PROD)).toBe(
      "Set me up on Overmind https://console.overmindlab.ai/setup.md"
    );
    expect(
      onboardingPrompt("https://console.overmindlab.ai", PROD, {
        datasetId: "d-1",
        projectId: PROJECT_ID,
      })
    ).toBe(
      `Set me up on Overmind with my uploaded data\n(project_id: ${PROJECT_ID},\ndataset_id: d-1)\n\nhttps://console.overmindlab.ai/setup.md`
    );
  });

  it.each([
    STAGING,
    LOCAL,
    "https://api.customer.example",
  ])("names a non-hosted API endpoint %s", (host) => {
    expect(onboardingPrompt("http://localhost:5173", `${host}/`)).toBe(
      `Set me up on Overmind\n\nusing the API at ${host}: http://localhost:5173/setup.md`
    );
  });

  it("installs from PyPI on every host", () => {
    for (const host of [STAGING, PROD, LOCAL]) {
      expect(sdkPipInstall(host)).toBe('python -m pip install "overmind>=0.1.106"');
      expect(sdkPipInstall(host, "tracing")).toBe(
        'python -m pip install "overmind[tracing]>=0.1.106"'
      );
    }
  });

  it("preflights with the CLI version", () => {
    expect(SDK_PREFLIGHT_COMMAND).toBe("python -m overmind --version");
  });

  it("flags --env from the Console API host", () => {
    expect(mcpInitEnvFlag(PROD)).toBe("");
    expect(mcpInitEnvFlag(`${PROD}/`)).toBe("");
    expect(mcpInitEnvFlag(LOCAL)).toBe(" --env local");
    expect(mcpInitEnvFlag("https://staging.overmindlab.ai")).toBe(" --env staging");
  });

  it("builds the terminal setup block", () => {
    for (const extra of [undefined, "tracing"] as const) {
      expect(mcpInitCommand("cursor", KEY, PROD, extra, PROJECT_ID).split(" &&\n")).toEqual([
        sdkPipInstall(PROD, extra),
        SDK_PREFLIGHT_COMMAND,
        `export OVERMIND_API_URL=${PROD}`,
        `export OVERMIND_PROJECT_ID=${PROJECT_ID}`,
        `export OVERMIND_API_KEY=${KEY}`,
        "python -m overmind init --ide cursor",
        "python -m overmind sync",
      ]);
    }
    expect(mcpInitCommand("claude", KEY, LOCAL)).toContain(
      "overmind init --ide claude --env local"
    );
    expect(mcpInitCommand("cursor", KEY, STAGING)).toContain(
      "overmind init --ide cursor --env staging"
    );
  });

  it("uses the editable checkout only against a local API when VITE_SDK_EDITABLE_PATH is set", async () => {
    vi.stubEnv("VITE_SDK_EDITABLE_PATH", EDITABLE);
    vi.resetModules();
    const { sdkPipInstall: pip } = await import("./mcp-setup");
    expect(pip(LOCAL)).toBe(`python -m pip install -e "${EDITABLE}"`);
    expect(pip(LOCAL, "tracing")).toBe(`python -m pip install -e "${EDITABLE}[tracing]"`);
    expect(pip(PROD)).toBe('python -m pip install "overmind>=0.1.106"');
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("addresses the setup prompt to the selected agent", () => {
    for (const { id, label } of MCP_CLIENTS) {
      expect(telemetrySetupPrompt(id)).toContain(`Overmind MCP and skill setup in ${label}`);
      expect(telemetrySetupPrompt(id)).toContain(`overmind init --ide ${id}`);
    }
    expect(telemetrySetupPrompt("cursor")).not.toContain("codex");
  });

  it("reuses durable local authentication in the instrumentation prompt", () => {
    const prompt = telemetrySetupPrompt("codex");
    expect(prompt).toContain("saved by `overmind sync`");
    expect(prompt).toContain("deployed processes");
    expect(prompt).toContain("OVERMIND_API_KEY");
    expect(prompt).toContain("overmind[tracing]");
    expect(prompt).not.toContain("--env");
  });

  it("names the current MCP instrumentation tools", () => {
    const prompt = telemetrySetupPrompt("codex");
    expect(prompt).toContain("get_instrumentation_plan");
    expect(prompt).toContain("query_traces(session=<correlation>, all_spans=false, limit=2)");
    expect(prompt).toContain("verify_instrumentation(trace_id=...)");
    expect(prompt).toContain("Real run (recommended)");
    expect(prompt).toContain("explicit approval");
    expect(prompt).toContain("human_action");
    expect(prompt).toContain("required_decorator");
    expect(prompt).toContain("conversation.id");
    expect(prompt).toContain("page.total == 1");
    expect(prompt).not.toContain("get_instrumentation_context");
    expect(prompt).not.toContain("verify_instrumentation_spans");
  });
});
