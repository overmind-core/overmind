// @vitest-environment jsdom
// covers: console-logic

import { describe, expect, it } from "vitest";

import { NETWORK_ERROR_MESSAGE } from "@/lib/api-error";
import { errorMessage } from "@/lib/notify";
import { isChunkLoadError } from "./chunk-error";

describe("isChunkLoadError", () => {
  it.each([
    "Failed to fetch dynamically imported module: https://example.com/assets/foo.js",
    "Importing a module script failed.",
    "error loading dynamically imported module",
    "ChunkLoadError: Loading chunk 42 failed.",
    "Loading chunk 9573 failed. (error: https://example.com/ui-common.js)",
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

describe("errorMessage", () => {
  it("maps chunk load errors away from the network message", () => {
    const err = new TypeError("Failed to fetch dynamically imported module");
    expect(errorMessage(err)).not.toBe(NETWORK_ERROR_MESSAGE);
    expect(errorMessage(err)).not.toBe(err.message);
  });
});
