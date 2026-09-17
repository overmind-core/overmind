import { afterEach, describe, expect, it, vi } from "vitest";

describe("clerkReady derivation", () => {
  afterEach(() => {
    vi.unstubAllEnvs();
    vi.resetModules();
  });

  it("is false when VITE_SELF_HOSTED is true even with a publishable key", async () => {
    vi.stubEnv("VITE_SELF_HOSTED", "true");
    vi.stubEnv("VITE_CLERK_PUBLISHABLE_KEY", "pk_test_xxx");
    const { config } = await import("./config");
    expect(config.clerkReady).toBe(false);
  });

  it("is false when the publishable key is blank", async () => {
    vi.stubEnv("VITE_SELF_HOSTED", "false");
    vi.stubEnv("VITE_CLERK_PUBLISHABLE_KEY", "");
    const { config } = await import("./config");
    expect(config.clerkReady).toBe(false);
  });

  it("is true when a publishable key is set and not self-hosted", async () => {
    vi.stubEnv("VITE_SELF_HOSTED", "false");
    vi.stubEnv("VITE_CLERK_PUBLISHABLE_KEY", "pk_test_xxx");
    const { config } = await import("./config");
    expect(config.clerkReady).toBe(true);
  });
});
