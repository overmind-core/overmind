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
  });

  it("skips analytics.ts init when VITE_SELF_HOSTED is true", async () => {
    vi.stubEnv("VITE_SELF_HOSTED", "true");
    vi.stubEnv("MODE", "production");
    await import("./analytics");
    expect(posthogInit).not.toHaveBeenCalled();
  });

  it("skips posthog-provider init when VITE_SELF_HOSTED is true", async () => {
    vi.stubEnv("VITE_SELF_HOSTED", "true");
    vi.stubEnv("VITE_PUBLIC_POSTHOG_KEY", "phc_test");
    vi.stubEnv("MODE", "production");
    await import("./integrations/posthog-provider");
    expect(posthogInit).not.toHaveBeenCalled();
  });
});
