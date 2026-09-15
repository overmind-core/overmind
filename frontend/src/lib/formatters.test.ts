import { describe, expect, it } from "vitest";

import { formatCost, formatDuration, formatNumber, truncateText } from "./formatters";

describe("formatters", () => {
  describe("formatDuration", () => {
    it("formats sub-millisecond as microseconds", () => {
      expect(formatDuration(0.5)).toBe("500µs");
    });
    it("formats ms under 1000", () => {
      expect(formatDuration(500)).toBe("500ms");
    });
    it("formats seconds with two decimals under 10s", () => {
      expect(formatDuration(2500)).toBe("2.50s");
    });
    it("formats seconds with one decimal at/over 10s", () => {
      expect(formatDuration(12_400)).toBe("12.4s");
    });
    it("formats minutes compactly", () => {
      expect(formatDuration(65_000)).toBe("1m5s");
    });
    it("returns em dash for null/zero", () => {
      expect(formatDuration(null)).toBe("—");
      expect(formatDuration(0)).toBe("—");
    });
  });

  describe("formatCost", () => {
    it("returns em dash for null", () => {
      expect(formatCost(null)).toBe("—");
    });
    it("clamps tiny costs", () => {
      expect(formatCost(0.0005)).toBe("<$0.001");
    });
    it("formats sub-cent costs with four decimals", () => {
      expect(formatCost(0.0024)).toBe("$0.0024");
    });
    it("formats dollar costs with two decimals", () => {
      expect(formatCost(1.5)).toBe("$1.50");
    });
    it("rounds large costs", () => {
      expect(formatCost(123.4)).toBe("$123");
    });
    it("parses numeric strings", () => {
      expect(formatCost("0.5")).toBe("$0.500");
    });
  });

  describe("formatNumber", () => {
    it("formats with locale", () => {
      expect(formatNumber(1000)).toBe("1,000");
    });
  });

  describe("truncateText", () => {
    it("returns full text when under max", () => {
      expect(truncateText("hello", 10)).toBe("hello");
    });
    it("truncates long text", () => {
      expect(truncateText("hello world", 5)).toBe("hello...");
    });
    it("returns empty for empty input", () => {
      expect(truncateText("")).toBe("");
    });
  });
});
