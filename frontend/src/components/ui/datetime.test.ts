import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { formatSmart } from "./datetime";

describe("datetime", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-24T12:00:00"));
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("returns fallback for empty/invalid input", () => {
    expect(formatSmart(null)).toBe("—");
    expect(formatSmart(undefined)).toBe("—");
    expect(formatSmart("not a date")).toBe("—");
  });

  it("shows exact time and date within the last 24h", () => {
    expect(formatSmart(new Date("2026-07-24T10:30:00"))).toBe("10:30, 24 Jul");
    expect(formatSmart(new Date("2026-07-23T14:00:00"))).toBe("14:00, 23 Jul");
  });

  it("shows exact time for future dates", () => {
    expect(formatSmart(new Date("2026-07-25T09:00:00"))).toBe("09:00, 25 Jul");
  });

  it("shows 'yesterday' for >24h ago but previous calendar day", () => {
    expect(formatSmart(new Date("2026-07-23T08:00:00"))).toBe("yesterday");
  });

  it("shows relative labels for older dates", () => {
    expect(formatSmart(new Date("2026-07-20T12:00:00"))).toBe("4 days ago");
    expect(formatSmart(new Date("2026-05-24T12:00:00"))).toBe("2 months ago");
  });
});
