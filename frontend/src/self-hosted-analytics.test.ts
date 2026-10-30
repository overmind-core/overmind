import { afterEach, describe, expect, it, vi } from "vitest";

const posthogInit = vi.fn();
const posthogCapture = vi.fn();
const posthogIdentify = vi.fn();
const posthogCaptureException = vi.fn();

vi.mock("posthog-js", () => ({
  default: {
    capture: posthogCapture,
    captureException: posthogCaptureException,
    identify: posthogIdentify,
    init: posthogInit,
  },
}));

describe("self-hosted PostHog strip", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
    posthogInit.mockClear();
    posthogCapture.mockClear();
  });

  it("skips analytics.ts init when VITE_SELF_HOSTED is true", async () => {
    vi.stubEnv("VITE_SELF_HOSTED", "true");
    vi.stubEnv("MODE", "production");
    await import("./analytics");
    expect(posthogInit).not.toHaveBeenCalled();
  });

  it("initializes once and preserves events captured while the SDK loads", async () => {
    vi.stubEnv("VITE_SELF_HOSTED", "false");
    vi.stubEnv("VITE_PUBLIC_POSTHOG_KEY", "phc_test");
    vi.stubEnv("VITE_PUBLIC_POSTHOG_HOST", "https://analytics.example");
    vi.stubEnv("MODE", "production");
    const { trackEvent } = await import("./analytics");
    trackEvent("setup_prompt_copied", { project_id: "project-1" });
    await vi.dynamicImportSettled();
    expect(posthogInit).toHaveBeenCalledOnce();
    expect(posthogInit).toHaveBeenCalledWith(
      "phc_test",
      expect.objectContaining({
        api_host: "https://analytics.example",
      })
    );
    expect(posthogCapture).toHaveBeenCalledWith("setup_prompt_copied", { project_id: "project-1" });
  });
});
