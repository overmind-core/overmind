import { describe, expect, it } from "vitest";

import {
  groupSampleRows,
  matchesSearch,
  sampleRowKey,
  scoreVerdict,
  sortRows,
} from "./datapoint-filter";

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

describe("sampleRowKey", () => {
  it("prefers rowIndex, then sourceTraceId, then sample id", () => {
    expect(sampleRowKey({ id: "s1", rowIndex: 3, sourceTraceId: "abc" })).toBe("row:3");
    expect(sampleRowKey({ id: "s1", rowIndex: 0 })).toBe("row:0");
    expect(sampleRowKey({ id: "s1", rowIndex: null, sourceTraceId: "abc" })).toBe("trace:abc");
    expect(sampleRowKey({ id: "s1" })).toBe("sample:s1");
    expect(sampleRowKey({})).toBeNull();
  });
});

describe("groupSampleRows", () => {
  it("emits one row per dataset row_index and maps each variant", () => {
    const grouped = groupSampleRows([
      { id: "a", rowIndex: 0, variant: "v1" },
      { id: "b", rowIndex: 1, variant: "v1" },
      { id: "c", rowIndex: 0, variant: "v2" },
    ]);
    expect(grouped.datapointRows.map((r) => r.rowIndex)).toEqual([0, 1]);
    expect(grouped.sampleMap.get("row:0")?.get("v1")).toBe("a");
    expect(grouped.sampleMap.get("row:0")?.get("v2")).toBe("c");
    expect(grouped.sampleMap.get("row:1")?.get("v1")).toBe("b");
  });

  it("groups trace-filter samples by sourceTraceId when rowIndex is missing", () => {
    const grouped = groupSampleRows([
      { id: "a", rowIndex: null, sourceTraceId: "t1", variant: "v1" },
      { id: "b", rowIndex: null, sourceTraceId: "t1", variant: "v2" },
      { id: "c", rowIndex: null, sourceTraceId: "t2", variant: "v1" },
    ]);
    expect(grouped.datapointRows).toHaveLength(2);
    expect(grouped.sampleMap.get("trace:t1")?.size).toBe(2);
    expect(grouped.datapointRows[1]?.fallbackSampleId).toBe("c");
  });

  it("does not collapse unrelated samples onto one missing-datapoint row", () => {
    const grouped = groupSampleRows([
      { id: "a", variant: "v1" },
      { id: "b", variant: "v1" },
    ]);
    expect(grouped.datapointRows.map((r) => r.fallbackSampleId)).toEqual(["a", "b"]);
  });
});

describe("sortRows", () => {
  const rows = [{ v: 0.5 as number | null }, { v: null }, { v: 0.9 }, { v: 0.1 }];

  it("sorts numbers in both directions without mutating the input", () => {
    expect(sortRows(rows, (r) => r.v, "asc").map((r) => r.v)).toEqual([0.1, 0.5, 0.9, null]);
    expect(sortRows(rows, (r) => r.v, "desc").map((r) => r.v)).toEqual([0.9, 0.5, 0.1, null]);
    expect(rows[0].v).toBe(0.5);
  });

  it("keeps nulls last regardless of direction", () => {
    expect(sortRows(rows, (r) => r.v, "asc").at(-1)?.v).toBeNull();
    expect(sortRows(rows, (r) => r.v, "desc").at(-1)?.v).toBeNull();
  });

  it("sorts strings alphabetically", () => {
    const texts = [{ t: "banana" }, { t: "Apple" }, { t: null as string | null }];
    expect(sortRows(texts, (r) => r.t, "asc").map((r) => r.t)).toEqual(["Apple", "banana", null]);
  });
});
