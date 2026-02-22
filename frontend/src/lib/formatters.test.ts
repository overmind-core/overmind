import { describe, expect, it } from "vitest";

import {
  formatDuration,
  formatNumber,
  formatPercentage,
  formatTimestamp,
  getTimeRangeStartTimestamp,
  truncateText,
} from "./formatters";

describe("formatters", () => {
  describe("formatTimestamp", () => {
    it("formats valid ISO date", () => {
      expect(formatTimestamp("2024-01-15T12:00:00.000Z")).toContain("2024");
    });
    it("returns empty for null/undefined", () => {
      expect(formatTimestamp(null)).toBe("");
      expect(formatTimestamp(undefined)).toBe("");
    });
    it("formats unix timestamp in milliseconds", () => {
      const ts = 1705312800000; // 2024-01-15
      expect(formatTimestamp(ts)).toContain("2024");
    });
  });

  describe("formatDuration", () => {
    it("formats ms under 1000", () => {
      expect(formatDuration(500)).toBe("500ms");
    });
    it("formats seconds", () => {
      expect(formatDuration(2500)).toBe("2.50s");
    });
    it("formats minutes", () => {
      expect(formatDuration(65000)).toMatch(/\d+m/);
    });
    it("returns N/A for null", () => {
      expect(formatDuration(null)).toBe("N/A");
    });
  });

  describe("formatPercentage", () => {
    it("formats 0.5 as 50%", () => {
      expect(formatPercentage(0.5)).toBe("50.0%");
    });
    it("formats with custom decimals", () => {
      expect(formatPercentage(0.1234, 2)).toBe("12.34%");
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

  describe("getTimeRangeStartTimestamp", () => {
    it("returns very old date for 'all'", () => {
      const result = getTimeRangeStartTimestamp("all");
      expect(result).toContain("1996");
    });
    it("returns ISO string for past24h", () => {
      const result = getTimeRangeStartTimestamp("past24h");
      expect(result).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    });
    it("returns ISO string for past7d", () => {
      const result = getTimeRangeStartTimestamp("past7d");
      expect(result).toMatch(/^\d{4}-\d{2}-\d{2}T/);
    });
  });
});
