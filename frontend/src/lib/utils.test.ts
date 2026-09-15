import { describe, expect, it } from "vitest";

import { paginationItems, usdToOvermindCredits, usdToOvermindCreditsWithLabel } from "./utils";

describe("usdToOvermindCredits", () => {
  it("converts at 100 credits per USD", () => {
    expect(usdToOvermindCredits(1)).toBe(100);
    expect(usdToOvermindCredits(40)).toBe(4000);
    expect(usdToOvermindCredits(0.126)).toBe(13);
    expect(usdToOvermindCreditsWithLabel(2.5)).toBe("250 credits");
  });
});

describe("paginationItems", () => {
  it("returns every page when total fits without truncation", () => {
    expect(paginationItems(1, 5)).toEqual([1, 2, 3, 4, 5]);
    expect(paginationItems(4, 7)).toEqual([1, 2, 3, 4, 5, 6, 7]);
  });

  it("collapses the middle for a deep run (50 pages, page 12)", () => {
    expect(paginationItems(12, 50)).toEqual([1, "ellipsis", 11, 12, 13, "ellipsis", 50]);
  });

  it("keeps the leading window near the start", () => {
    expect(paginationItems(1, 50)).toEqual([1, 2, 3, 4, 5, "ellipsis", 50]);
  });

  it("keeps the trailing window near the end", () => {
    expect(paginationItems(50, 50)).toEqual([1, "ellipsis", 46, 47, 48, 49, 50]);
  });

  it("never duplicates the first/last page at the boundaries", () => {
    for (let current = 1; current <= 50; current++) {
      const items = paginationItems(current, 50);
      const numbers = items.filter((i): i is number => i !== "ellipsis");
      expect(new Set(numbers).size).toBe(numbers.length);
      expect(numbers[0]).toBe(1);
      expect(numbers.at(-1)).toBe(50);
    }
  });
});
