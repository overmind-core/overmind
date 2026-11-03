// @vitest-environment jsdom
// covers: authentication
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import api, {
  clearTokens,
  fetchWithAuth,
  getAccessToken,
  getLocalIdentity,
  request,
  setAsyncTokenGetter,
  setTokens,
} from "./client";
import { dismissSessionRecovery, sessionRecoveryRequired } from "./lib/session-recovery";

const json = (status: number, body: unknown = {}) =>
  new Response(JSON.stringify(body), { headers: { "Content-Type": "application/json" }, status });

beforeEach(() => {
  localStorage.clear();
  setAsyncTokenGetter(null);
  setTokens("expired", "refresh");
  dismissSessionRecovery();
});
afterEach(() => {
  vi.unstubAllGlobals();
  setAsyncTokenGetter(null);
});

describe("authentication recovery across clients", () => {
  it.each([
    ["generated", () => api.auth.authMeRetrieve()],
    ["JSON", () => request("/api/auth/me/")],
    ["streaming", () => fetchWithAuth("/api/auth/me/")],
  ] as const)("isolates pending %s responses from a later session", async (_, call) => {
    for (const transition of ["sign out", "switch account"]) {
      for (const status of [200, 401]) {
        setTokens("expired", "refresh");
        let respond!: (response: Response) => void;
        const pending = new Promise<Response>((resolve) => {
          respond = resolve;
        });
        const fetch = vi.fn(() => pending);
        vi.stubGlobal("fetch", fetch);
        const result = call();
        await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(1));
        if (transition === "sign out") clearTokens();
        else setTokens("other-account", "other-refresh");
        respond(json(status, { id: 1 }));
        await expect(result).rejects.toMatchObject({ name: "AbortError" });
        expect(fetch).toHaveBeenCalledTimes(1);
        expect(sessionRecoveryRequired()).toBe(false);
      }
    }
  });

  it("does not revive recovery when sign-out interrupts a refresh", async () => {
    let respond!: (response: Response) => void;
    const pending = new Promise<Response>((resolve) => {
      respond = resolve;
    });
    const fetch = vi.fn(async (url: string) =>
      url.includes("token/refresh") ? pending : json(401)
    );
    vi.stubGlobal("fetch", fetch);
    const result = request("/api/auth/me/");
    await vi.waitFor(() => expect(fetch).toHaveBeenCalledTimes(2));
    clearTokens();
    respond(json(200, { access: "late", refresh: "late-refresh" }));
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    expect(getAccessToken()).toBeNull();
    expect(sessionRecoveryRequired()).toBe(false);
  });

  it("does not refresh a request cancelled as its response arrives", async () => {
    const controller = new AbortController();
    const fetch = vi.fn(async () => {
      controller.abort();
      return json(401);
    });
    vi.stubGlobal("fetch", fetch);
    await expect(
      fetchWithAuth("/api/auth/me/", { signal: controller.signal })
    ).rejects.toMatchObject({ name: "AbortError" });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(sessionRecoveryRequired()).toBe(false);
  });

  it.each([
    ["generated", () => api.auth.authMeRetrieve()],
    ["JSON", () => request("/api/auth/me/")],
    ["streaming", () => fetchWithAuth("/api/auth/me/")],
  ] as const)("keeps credentials when %s refresh cannot reach the server", async (_, call) => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => {
        if (url.includes("token/refresh")) throw new TypeError("Failed to fetch");
        return json(401);
      })
    );
    await expect(call()).rejects.toMatchObject({ status: 0 });
    expect(getAccessToken()).toBe("expired");
    expect(localStorage.getItem("jwt_refresh")).toBe("refresh");
    expect(sessionRecoveryRequired()).toBe(false);
  });

  it("refreshes a rejected cloud token once and retries the original request", async () => {
    const token = vi.fn(async (options?: { skipCache?: boolean }) =>
      options?.skipCache ? "fresh" : "expired"
    );
    setAsyncTokenGetter(token);
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) =>
        new Headers(init.headers).get("Authorization") === "Bearer fresh"
          ? json(200, { email: "test@example.com", id: 1 })
          : json(401)
      )
    );
    await expect(api.auth.authMeRetrieve()).resolves.toMatchObject({ id: 1 });
    expect(token).toHaveBeenCalledWith({ skipCache: true });
  });

  it("shares a refresh across concurrent generated, JSON and streaming requests", async () => {
    let refreshes = 0;
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string, init: RequestInit) => {
        if (url.includes("token/refresh")) {
          refreshes++;
          await new Promise((resolve) => setTimeout(resolve, 10));
          return json(200, { access: "fresh", refresh: "rotated" });
        }
        return new Headers(init.headers).get("Authorization") === "Bearer fresh"
          ? json(200, { id: 1 })
          : json(401);
      })
    );
    await Promise.all([
      api.auth.authMeRetrieve(),
      request("/api/auth/me/"),
      fetchWithAuth("/api/auth/me/"),
    ]);
    expect(refreshes).toBe(1);
    expect(getAccessToken()).toBe("fresh");
    expect(localStorage.getItem("jwt_refresh")).toBe("rotated");
    expect(sessionRecoveryRequired()).toBe(false);
  });

  it("offers recovery when the refreshed token is also rejected, without retrying forever", async () => {
    const fetch = vi.fn(async (url: string) =>
      url.includes("token/refresh") ? json(200, { access: "fresh" }) : json(401)
    );
    vi.stubGlobal("fetch", fetch);
    await expect(request("/api/auth/me/")).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(3);
    expect(sessionRecoveryRequired()).toBe(true);
  });

  it("leaves rejected login credentials in the form even if an old token remains", async () => {
    const fetch = vi.fn(async () => json(401, { detail: "Incorrect password." }));
    vi.stubGlobal("fetch", fetch);
    await expect(
      api.auth.authLocalCreate({
        localSessionRequest: { email: "test@example.com", password: "incorrect" },
      })
    ).rejects.toMatchObject({ status: 401 });
    expect(fetch).toHaveBeenCalledTimes(1);
    expect(sessionRecoveryRequired()).toBe(false);
  });

  it("keeps credentials on a refresh server failure", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (url: string) => json(url.includes("token/refresh") ? 503 : 401))
    );
    await expect(request("/api/auth/me/")).rejects.toMatchObject({ status: 503 });
    expect(getAccessToken()).toBe("expired");
    expect(sessionRecoveryRequired()).toBe(false);
  });
});

const localToken = (id: string, nonce: string) =>
  `header.${btoa(JSON.stringify({ nonce, user_id: id }))}.signature`;

it.each([
  false,
  true,
])("handles cross-tab token rotation with account change=%s", async (changed) => {
  const before = localToken("1", "before");
  const after = localToken(changed ? "2" : "1", "after");
  setTokens(before, before, { email: "first@example.com", id: 1 });
  let respond!: (response: Response) => void;
  const fetch = vi.fn(
    () =>
      new Promise<Response>((resolve) => {
        respond = resolve;
      })
  );
  vi.stubGlobal("fetch", fetch);
  const result = request("/api/auth/me/");
  await vi.waitFor(() => expect(fetch).toHaveBeenCalledOnce());
  localStorage.setItem("jwt_access", after);
  window.dispatchEvent(
    new StorageEvent("storage", {
      key: "jwt_access",
      newValue: after,
      oldValue: before,
    })
  );
  respond(json(200, { id: 1 }));
  if (changed) {
    await expect(result).rejects.toMatchObject({ name: "AbortError" });
    expect(getLocalIdentity()).toBeNull();
  } else {
    await expect(result).resolves.toMatchObject({ id: 1 });
    expect(getLocalIdentity()).toEqual({ email: "first@example.com", id: "1" });
  }
});
