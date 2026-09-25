// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { NETWORK_ERROR_MESSAGE } from "@/lib/api-error";
import { errorMessage } from "@/lib/notify";
import {
  CHUNK_RELOAD_KEY,
  isChunkLoadError,
  tryReloadOnceOnChunkError,
  withChunkReloadRetry,
} from "./chunk-error";

describe("isChunkLoadError", () => {
  it.each([
    "Failed to fetch dynamically imported module: https://example.com/assets/foo.js",
    "Importing a module script failed.",
    "error loading dynamically imported module",
    "ChunkLoadError: Loading chunk 42 failed.",
    'Failed to load module script: Expected a JavaScript-or-Wasm module script but the server responded with a MIME type of "text/html".',
  ])("detects %s", (message) => {
    expect(isChunkLoadError(new TypeError(message))).toBe(true);
    expect(isChunkLoadError(message)).toBe(true);
  });

  it("rejects generic network fetch failures", () => {
    expect(isChunkLoadError(new TypeError("Failed to fetch"))).toBe(false);
    expect(isChunkLoadError(new TypeError("NetworkError when attempting to fetch resource."))).toBe(
      false
    );
  });
});

describe("tryReloadOnceOnChunkError", () => {
  const reload = vi.fn();

  beforeEach(() => {
    sessionStorage.clear();
    reload.mockReset();
    vi.stubGlobal("location", { reload });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reloads once when the flag is absent", () => {
    const err = new TypeError("Failed to fetch dynamically imported module");
    expect(tryReloadOnceOnChunkError(err)).toBe(true);
    expect(reload).toHaveBeenCalledOnce();
    expect(sessionStorage.getItem(CHUNK_RELOAD_KEY)).toBe("1");
  });

  it("ignores non-chunk errors", () => {
    expect(tryReloadOnceOnChunkError(new TypeError("Failed to fetch"))).toBe(false);
    expect(reload).not.toHaveBeenCalled();
  });
});

describe("withChunkReloadRetry", () => {
  const reload = vi.fn();

  beforeEach(() => {
    sessionStorage.clear();
    reload.mockReset();
    vi.stubGlobal("location", { reload });
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("reloads once when the loader rejects with a chunk error", async () => {
    const loader = vi.fn(() =>
      Promise.reject(new TypeError("Failed to fetch dynamically imported module"))
    );
    const wrapped = withChunkReloadRetry(loader);
    const pending = wrapped();
    await expect(Promise.race([pending, Promise.resolve("timeout")])).resolves.toBe("timeout");
    expect(reload).toHaveBeenCalledOnce();
  });

  it("rethrows when reload was already attempted", async () => {
    sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
    const err = new TypeError("Failed to fetch dynamically imported module");
    const loader = vi.fn(() => Promise.reject(err));
    await expect(withChunkReloadRetry(loader)()).rejects.toBe(err);
    expect(reload).not.toHaveBeenCalled();
  });

  it("clears the reload guard when the loader succeeds", async () => {
    sessionStorage.setItem(CHUNK_RELOAD_KEY, "1");
    const loader = vi.fn(() => Promise.resolve({ default: "ok" }));
    await expect(withChunkReloadRetry(loader)()).resolves.toEqual({ default: "ok" });
    expect(sessionStorage.getItem(CHUNK_RELOAD_KEY)).toBeNull();
  });
});

describe("errorMessage", () => {
  it("maps chunk load errors away from the network message", () => {
    const err = new TypeError("Failed to fetch dynamically imported module");
    expect(errorMessage(err)).not.toBe(NETWORK_ERROR_MESSAGE);
    expect(errorMessage(err)).toContain("new version");
  });
});
