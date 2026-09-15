// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";

import { dismissGuestPrompt, GuestUpgradeError, onGuestUpgrade } from "@/lib/guest";
import { request } from "./client";

const guestDenied = () =>
  new Response(
    JSON.stringify({ code: "guest_upgrade_required", detail: "Create an account to continue." }),
    {
      headers: { "Content-Type": "application/json" },
      status: 403,
    }
  );

const plainDenied = () =>
  new Response(JSON.stringify({ detail: "Forbidden." }), {
    headers: { "Content-Type": "application/json" },
    status: 403,
  });

describe("request() on a 403", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    dismissGuestPrompt();
  });

  it("raises the upgrade prompt and throws GuestUpgradeError for the guest code", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => guestDenied())
    );
    const prompted = vi.fn();
    const off = onGuestUpgrade(prompted);
    await expect(request("/api/projects/", { method: "POST" })).rejects.toBeInstanceOf(
      GuestUpgradeError
    );
    expect(prompted).toHaveBeenCalledTimes(1);
    off();
  });

  it("leaves other 403s to the caller", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => plainDenied())
    );
    const prompted = vi.fn();
    const off = onGuestUpgrade(prompted);
    await expect(request("/api/projects/", { method: "POST" })).rejects.toThrow("Forbidden.");
    expect(prompted).not.toHaveBeenCalled();
    off();
  });
});
