// @vitest-environment jsdom
import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  clearGuestSession,
  dismissGuestPrompt,
  emitGuestUpgrade,
  getGuestProjectId,
  hasGuestSession,
  isGuestAllowedPath,
  isGuestPromptOpen,
  markGuestSession,
  onGuestUpgrade,
} from "./guest";

describe("isGuestAllowedPath", () => {
  it("allows the home page and a capability page", () => {
    expect(isGuestAllowedPath("/")).toBe(true);
    expect(isGuestAllowedPath("/capabilities/12345678-1234-1234-1234-123456789abc")).toBe(true);
  });

  it("blocks every other route", () => {
    for (const path of ["/agent", "/evaluations", "/observability", "/settings"]) {
      expect(isGuestAllowedPath(path)).toBe(false);
    }
  });
});

describe("guest session storage", () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it("round-trips the session flag and project id", () => {
    expect(hasGuestSession()).toBe(false);
    markGuestSession("p1");
    expect(hasGuestSession()).toBe(true);
    expect(getGuestProjectId()).toBe("p1");
    clearGuestSession();
    expect(hasGuestSession()).toBe(false);
    expect(getGuestProjectId()).toBeNull();
  });
});

describe("guest upgrade prompt", () => {
  beforeEach(() => {
    dismissGuestPrompt();
  });

  it("opens on emit, notifies listeners, and closes on dismiss", () => {
    const listener = vi.fn();
    const off = onGuestUpgrade(listener);
    emitGuestUpgrade();
    expect(isGuestPromptOpen()).toBe(true);
    expect(listener).toHaveBeenCalledTimes(1);
    emitGuestUpgrade();
    expect(listener).toHaveBeenCalledTimes(1);
    dismissGuestPrompt();
    expect(isGuestPromptOpen()).toBe(false);
    expect(listener).toHaveBeenCalledTimes(2);
    off();
  });

  it("holds an emit made before any dialog subscribes", () => {
    emitGuestUpgrade();
    const listener = vi.fn();
    const off = onGuestUpgrade(listener);
    expect(isGuestPromptOpen()).toBe(true);
    expect(listener).not.toHaveBeenCalled();
    off();
  });
});
