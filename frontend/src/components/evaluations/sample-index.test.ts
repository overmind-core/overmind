import { describe, expect, it } from "vitest";

import { indexSamples } from "./sample-index";

const sample = (id: string, rowIndex: number | null, variant = "model-a", sourceTraceId = "") => ({
  id,
  rowIndex,
  sourceTraceId,
  variant,
});

describe("indexSamples", () => {
  it("keeps all 100 dataset outputs separately viewable, including row zero", () => {
    const samples = Array.from({ length: 100 }, (_, i) => sample(`sample-${i}`, i));
    const { datapointRows, sampleMap } = indexSamples(samples);

    expect(datapointRows).toHaveLength(100);
    for (const [i, row] of datapointRows.entries()) {
      expect(row).toEqual({ inputSampleId: `sample-${i}`, key: `row:${i}`, label: String(i + 1) });
      expect(sampleMap.get(row.key)?.get("model-a")).toBe(`sample-${i}`);
    }
  });

  it("aligns variants by row index even when generated trace IDs differ", () => {
    const { datapointRows, sampleMap, sampleVariantMap } = indexSamples([
      sample("a-0", 0, "a", "generated-a-0"),
      sample("a-5", 5, "a", "generated-a-5"),
      sample("b-5", 5, "b", "generated-b-5"),
      sample("b-0", 0, "b", "generated-b-0"),
    ]);

    expect(datapointRows.map((row) => row.key)).toEqual(["row:0", "row:5"]);
    expect([...sampleMap.get("row:0")!]).toEqual([
      ["a", "a-0"],
      ["b", "b-0"],
    ]);
    expect([...sampleMap.get("row:5")!]).toEqual([
      ["a", "a-5"],
      ["b", "b-5"],
    ]);
    expect(sampleVariantMap.get("b-5")).toBe("b");
  });

  it("aligns trace-filter samples by source trace without mixing distinct traces", () => {
    const { datapointRows, sampleMap } = indexSamples([
      sample("a-1", null, "a", "trace-1"),
      sample("a-2", null, "a", "trace-2"),
      sample("b-1", null, "b", "trace-1"),
    ]);

    expect(datapointRows).toHaveLength(2);
    expect([...sampleMap.get("trace:trace-1")!]).toEqual([
      ["a", "a-1"],
      ["b", "b-1"],
    ]);
    expect(sampleMap.get("trace:trace-2")?.get("b")).toBeUndefined();
  });

  it("never collapses samples with no dataset row or trace identity", () => {
    const { datapointRows, sampleMap } = indexSamples([
      sample("unlinked-a", null, "a"),
      sample("unlinked-b", null, "b"),
      sample("unlinked-c", null, "a"),
    ]);

    expect(datapointRows).toHaveLength(3);
    expect(sampleMap.get("sample:unlinked-b")?.get("b")).toBe("unlinked-b");
    expect(sampleMap.get("sample:unlinked-b")?.get("a")).toBeUndefined();
  });

  it("keeps row and trace identities in separate namespaces", () => {
    const { datapointRows } = indexSamples([
      sample("dataset", 0),
      sample("trace", null, "model-a", "0"),
    ]);
    expect(datapointRows).toHaveLength(2);
  });

  it("returns no rows for an empty run", () => {
    expect(indexSamples([]).datapointRows).toEqual([]);
  });
});
