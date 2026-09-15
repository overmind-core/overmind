// @vitest-environment jsdom
import { renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  auth: { isGuest: false, requestUpgrade: vi.fn() },
}));

vi.mock("@/contexts/auth-context", () => ({
  useAuthContext: () => mocks.auth,
}));

import { useGuestGate } from "./use-guest-gate";

describe("useGuestGate", () => {
  beforeEach(() => {
    mocks.auth.isGuest = false;
    mocks.auth.requestUpgrade.mockReset();
  });

  it("runs the handler for members", () => {
    const handler = vi.fn();
    const { result } = renderHook(() => useGuestGate());
    result.current(handler)("arg");
    expect(handler).toHaveBeenCalledWith("arg");
    expect(mocks.auth.requestUpgrade).not.toHaveBeenCalled();
  });

  it("swaps the handler for the upgrade prompt and cancels the event for guests", () => {
    mocks.auth.isGuest = true;
    const handler = vi.fn();
    const event = { preventDefault: vi.fn() };
    const { result } = renderHook(() => useGuestGate());
    result.current(handler)(event);
    expect(handler).not.toHaveBeenCalled();
    expect(event.preventDefault).toHaveBeenCalledTimes(1);
    expect(mocks.auth.requestUpgrade).toHaveBeenCalledTimes(1);
  });

  it("is inert for members when called without a handler", () => {
    const event = { preventDefault: vi.fn() };
    const { result } = renderHook(() => useGuestGate());
    result.current()(event);
    expect(event.preventDefault).not.toHaveBeenCalled();
    expect(mocks.auth.requestUpgrade).not.toHaveBeenCalled();
  });
});
