import { describe, expect, it } from "vitest";

import { matchesSearch, scoreVerdict, sortRows } from "./datapoint-filter";

describe("scoreVerdict", () => {
  it("prefers the explicit passed flag over the numeric value", () => {
    expect(scoreVerdict(0.1, true)).toBe("passed");
    expect(scoreVerdict(0.9, false)).toBe("failed");
  });

  it("falls back to the good-tier threshold for numeric-only scores", () => {
    expect(scoreVerdict(0.7, null)).toBe("passed");
    expect(scoreVerdict(0.69, null)).toBe("failed");
    expect(scoreVerdict(1, undefined)).toBe("passed");
  });

  it("returns null when there is nothing to judge", () => {
    expect(scoreVerdict(null, null)).toBeNull();
    expect(scoreVerdict(undefined, undefined)).toBeNull();
  });
});

describe("matchesSearch", () => {
  it("matches case-insensitively across any text", () => {
    expect(matchesSearch("SPOTIFY", ["input text", '{"vendor": "Spotify"}'])).toBe(true);
    expect(matchesSearch("stripe", ["input text", '{"vendor": "Spotify"}'])).toBe(false);
  });

  it("treats blank queries as match-all and ignores null texts", () => {
    expect(matchesSearch("  ", [null, undefined])).toBe(true);
    expect(matchesSearch("x", [null, undefined])).toBe(false);
  });
});

describe("sortRows", () => {
  const rows = [{ v: 0.5 as number | null }, { v: null }, { v: 0.9 }, { v: 0.1 }];

  it("sorts numbers in both directions without mutating the input", () => {
    expect(sortRows(rows, (r) => r.v, "asc").map((r) => r.v)).toEqual([0.1, 0.5, 0.9, null]);
    expect(sortRows(rows, (r) => r.v, "desc").map((r) => r.v)).toEqual([0.9, 0.5, 0.1, null]);
    expect(rows[0].v).toBe(0.5);
  });

  it("sorts strings alphabetically", () => {
    const texts = [{ t: "banana" }, { t: "Apple" }, { t: null as string | null }];
    expect(sortRows(texts, (r) => r.t, "asc").map((r) => r.t)).toEqual(["Apple", "banana", null]);
  });
});
