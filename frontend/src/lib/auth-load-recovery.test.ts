// @vitest-environment jsdom
// covers: console-logic
import { beforeEach, expect, it, vi } from "vitest";

const capture = vi.fn();

vi.mock("posthog-js", () => ({
  default: { capture, captureException: vi.fn(), init: vi.fn() },
}));

const reload = vi.fn();

beforeEach(() => {
  vi.stubEnv("VITE_SELF_HOSTED", "false");
  vi.stubEnv("MODE", "production");
  vi.resetModules();
  window.sessionStorage.clear();
  reload.mockClear();
  capture.mockClear();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { ...window.location, reload },
  });
});

it("reloads once after an auth load failure and records the recovery", async () => {
  const { markAuthLoaded, retryAuthLoad } = await import("./auth-load-recovery");

  expect(retryAuthLoad("script")).toBe(true);
  markAuthLoaded();
  expect(retryAuthLoad("script")).toBe(false);
  await vi.dynamicImportSettled();

  expect(reload).toHaveBeenCalledOnce();
  expect(capture).toHaveBeenCalledOnce();
  expect(capture).toHaveBeenCalledWith("auth_load_recovered", { failure: "script" });
});

it("reloads on a Clerk chunk failure but not on an app chunk failure", async () => {
  const { installClerkChunkRecovery } = await import("./auth-load-recovery");
  installClerkChunkRecovery();

  window.dispatchEvent(
    new ErrorEvent("error", {
      error: new Error("Loading chunk 344 failed. (error: https://example.com/assets/app.js)"),
    })
  );
  expect(reload).not.toHaveBeenCalled();

  window.dispatchEvent(
    new ErrorEvent("error", {
      error: new Error(
        "Loading chunk 9573 failed. (error: https://clerk.example.com/npm/@clerk/clerk-js@5/dist/ui-common.js)"
      ),
    })
  );
  expect(reload).toHaveBeenCalledOnce();
});
