import { describe, expect, it, vi } from "vitest";

import {
  MCP_CLIENTS,
  mcpInitCommand,
  mcpInitEnvFlag,
  onboardWithAiBootstrapPrompt,
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
      expect(prompt).toContain("After reload, repeat the preflight command");
      expect(prompt).toContain(`python -m overmind skills sync overmind --ide ${id}`);
    }
  });

  it("installs from PyPI with the project's installer on every host", () => {
    for (const host of [STAGING, PROD, LOCAL]) {
      expect(sdkPipInstall(host)).toBe('python -m pip install "overmind>=0.1.95"');
      expect(sdkPipInstall(host, "tracing")).toBe(
        'python -m pip install "overmind[tracing]>=0.1.95"'
      );
      const prompt = onboardWithAiBootstrapPrompt("cursor", KEY, host);
      expect(prompt).toContain('`uv pip install "overmind>=0.1.95"` for uv');
      expect(prompt).toContain('`poetry run pip install "overmind>=0.1.95"` for Poetry');
      expect(prompt).toContain('otherwise `python -m pip install "overmind>=0.1.95"`');
      expect(prompt).toContain("manifest is not required");
      expect(prompt).not.toContain("uv add");
    }
  });

  it("recovers with isolated tooling without touching application dependencies", () => {
    const prompt = onboardWithAiBootstrapPrompt("cursor", KEY, PROD);
    expect(prompt).toContain(
      'uv tool run --isolated --python 3.11 --from "overmind==0.1.95" python'
    );
    expect(prompt).toContain("never change the project's supported Python range");
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
    const { onboardWithAiBootstrapPrompt: bootstrap, sdkPipInstall: pip } = await import(
      "./mcp-setup"
    );
    expect(pip(LOCAL)).toBe(`python -m pip install -e "${EDITABLE}"`);
    expect(pip(LOCAL, "tracing")).toBe(`python -m pip install -e "${EDITABLE}[tracing]"`);
    expect(pip(PROD)).toBe('python -m pip install "overmind>=0.1.95"');

    const local = bootstrap("cursor", KEY, LOCAL);
    expect(local).toContain(`uv pip install -e "${EDITABLE}"`);
    expect(local).toContain(`--with-editable "${EDITABLE}" --from overmind python`);
    expect(local).not.toContain('--from "overmind==0.1.95"');

    expect(bootstrap("cursor", KEY, PROD)).not.toContain(EDITABLE);
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
