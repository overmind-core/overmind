// @vitest-environment jsdom
// covers: console-logic
import { expect, it, vi } from "vitest";

const captureException = vi.fn();

vi.mock("posthog-js", () => ({
  default: { captureException, init: vi.fn() },
}));

it("drops browser noise from window error capture and keeps app errors", async () => {
  vi.stubEnv("VITE_SELF_HOSTED", "false");
  vi.stubEnv("MODE", "production");
  await import("./analytics");
  const appError = new Error("boom");

  window.dispatchEvent(new ErrorEvent("error", { message: "Script error." }));
  window.dispatchEvent(
    new ErrorEvent("error", {
      message: "ResizeObserver loop completed with undelivered notifications.",
    })
  );
  window.dispatchEvent(
    new ErrorEvent("error", {
      error: new TypeError(
        "undefined is not an object (evaluating 'window.ethereum.selectedAddress')"
      ),
      message: "",
    })
  );
  window.dispatchEvent(new ErrorEvent("error", { error: appError, message: "boom" }));
  await vi.dynamicImportSettled();

  expect(captureException).toHaveBeenCalledOnce();
  expect(captureException).toHaveBeenCalledWith(appError);
});
