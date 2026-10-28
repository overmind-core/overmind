import { afterEach, beforeEach, describe, expect, mock, spyOn, test } from "bun:test";

import { clearToken, getToken, overwatchGet, setToken } from "../src/lib/api";

class MemoryStorage {
  private values = new Map<string, string>();
  getItem(key: string) {
    return this.values.get(key) ?? null;
  }
  setItem(key: string, value: string) {
    this.values.set(key, String(value));
  }
  removeItem(key: string) {
    this.values.delete(key);
  }
  clear() {
    this.values.clear();
  }
}

const originalFetch = globalThis.fetch;
beforeEach(() => {
  Object.defineProperty(globalThis, "localStorage", {
    configurable: true,
    value: new MemoryStorage(),
  });
  Object.defineProperty(globalThis, "sessionStorage", {
    configurable: true,
    value: new MemoryStorage(),
  });
});
afterEach(() => {
  mock.restore();
  globalThis.fetch = originalFetch;
});

describe("analytics session", () => {
  test("retires persistent tokens and expires tab credentials", () => {
    localStorage.setItem("overmind.sales.token", "legacy-test-value");
    expect(getToken()).toBeNull();
    expect(localStorage.getItem("overmind.sales.token")).toBeNull();
    const clock = spyOn(Date, "now").mockReturnValue(1000);
    setToken("test-overwatch-session");
    expect(getToken()).toBe("test-overwatch-session");
    expect(localStorage.getItem("overmind.overwatch.session")).toBeNull();
    clock.mockReturnValue(1000 + 30 * 60 * 1000);
    expect(getToken()).toBeNull();
    expect(sessionStorage.getItem("overmind.overwatch.session")).toBeNull();
  });

  test("malformed session data fails closed", () => {
    sessionStorage.setItem("overmind.overwatch.session", "invalid-json");
    expect(getToken()).toBeNull();
    setToken("test-overwatch-session");
    clearToken();
    expect(getToken()).toBeNull();
  });

  test("authenticated requests forbid caches, redirects and referrers", async () => {
    let calledUrl = "";
    let options: RequestInit | undefined;
    globalThis.fetch = mock(async (url: string | URL | Request, init?: RequestInit) => {
      calledUrl = String(url);
      options = init;
      return new Response(JSON.stringify({ available: false }), { status: 200 });
    }) as typeof fetch;
    await overwatchGet("product-usage", { days: 7 }, "test-overwatch-session");
    expect(calledUrl).not.toContain("test-overwatch-session");
    expect(options?.cache).toBe("no-store");
    expect(options?.redirect).toBe("error");
    expect(options?.referrerPolicy).toBe("no-referrer");
  });
});

test("chart windows stay anchored to the cached report across midnight", async () => {
  const { fillDays } = await import("../src/lib/format");
  expect(fillDays([{ day: "2026-10-25T00:00:00Z", value: 3 }], 1, "2026-10-25T23:59:00Z")).toEqual([
    { day: "2026-10-25", value: 3 },
  ]);
});

test("UTC chart buckets do not shift to the previous local date", async () => {
  const { formatDay } = await import("../src/lib/format");
  expect(formatDay("2026-10-25T00:00:00Z")).toBe("25 Oct");
});
