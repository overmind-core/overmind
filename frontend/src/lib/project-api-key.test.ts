// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearAccountApiKey,
  isCachedApiKeyValid,
  readAccountApiKey,
  resolveAccountApiKey,
  writeAccountApiKey,
} from "./project-api-key";

const API_URL = "http://localhost:8000";
const KEY = "ovr_test_key";

describe("project-api-key cache", () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it("scopes account keys by API host", () => {
    writeAccountApiKey(KEY, API_URL);
    expect(readAccountApiKey(API_URL)).toBe(KEY);
    expect(readAccountApiKey("http://api.overmindlab.ai")).toBeNull();
  });

  it("clears stale account keys after failed validation", async () => {
    writeAccountApiKey(KEY, API_URL);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: false, status: 401 }));

    await expect(resolveAccountApiKey(API_URL)).resolves.toBeNull();
    expect(readAccountApiKey(API_URL)).toBeNull();
  });

  it("keeps valid account keys", async () => {
    writeAccountApiKey(KEY, API_URL);
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue({ ok: true }));

    await expect(resolveAccountApiKey(API_URL)).resolves.toBe(KEY);
    expect(readAccountApiKey(API_URL)).toBe(KEY);
  });

  it("validates with X-Api-Key against the current endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue({ ok: true });
    vi.stubGlobal("fetch", fetchMock);

    await expect(isCachedApiKeyValid(KEY, API_URL)).resolves.toBe(true);
    expect(fetchMock).toHaveBeenCalledWith("http://localhost:8000/api/auth/api-keys/current/", {
      headers: { "X-Api-Key": KEY },
    });
  });

  it("clear removes the cached entry", () => {
    writeAccountApiKey(KEY, API_URL);
    clearAccountApiKey(API_URL);
    expect(readAccountApiKey(API_URL)).toBeNull();
  });
});
