import { describe, expect, it } from "vitest";

import type { Cell, PipelineRun } from "@/openapi";
import { cellConnections, iterationCells } from "./flow";

function cell(id: string, position: number, fields: Partial<Cell> = {}): Cell {
  return { fingerprint: id, id, inputFingerprint: "", position, review: {}, ...fields } as Cell;
}

describe("recorded Workshop lineage", () => {
  it("renders forks and joins without substituting display order for parentage", () => {
    const cells = [
      cell("source", 0),
      cell("yes", 1, { review: { condition: "eligible = true", input_cells: ["source"] } }),
      cell("no", 2, { review: { condition: "eligible = false", input_cells: ["source"] } }),
      cell("merged", 3, { review: { input_cells: ["yes", "no"] } }),
    ];
    const edges = cellConnections(cells, []);
    expect(edges.map(({ source, target, label }) => [source, target, label])).toEqual([
      ["source", "yes", "eligible = true"],
      ["source", "no", "eligible = false"],
      ["yes", "merged", undefined],
      ["no", "merged", undefined],
    ]);
    expect(iterationCells(cells, edges, "yes").map((item) => item.id)).toEqual(["source", "yes"]);
    expect(iterationCells(cells, edges, "merged")).toEqual(cells);
  });

  it("uses historical run step order even when a no-op repeats the fingerprint", () => {
    const cells = [cell("source", 0), cell("one", 1), cell("two", 2)];
    const run = {
      outputCell: "two",
      result: { steps: [{ output_cell: "one" }, { output_cell: "two" }] },
      sourceCell: "source",
    } as unknown as PipelineRun;
    expect(cellConnections(cells, [run]).map(({ source, target }) => [source, target])).toEqual([
      ["source", "one"],
      ["one", "two"],
    ]);
  });

  it("leaves ambiguous and absent lineage disconnected rather than inventing edges", () => {
    const cells = [
      cell("one", 0, { fingerprint: "same" }),
      cell("two", 1, { fingerprint: "same" }),
      cell("ambiguous", 2, { inputFingerprint: "same" }),
      cell("external", 3, { review: { input_cells: ["outside"] } }),
    ];
    expect(cellConnections(cells, [])).toEqual([]);
    expect(iterationCells(cells, [], "external").map((item) => item.id)).toEqual(["external"]);
  });

  it("ignores self-links and cannot hang while traversing malformed cyclic records", () => {
    const cells = [
      cell("a", 0, { review: { input_cells: ["a", "b"] } }),
      cell("b", 1, { review: { input_cells: ["a"] } }),
    ];
    const edges = cellConnections(cells, []);
    expect(edges.some((edge) => edge.source === edge.target)).toBe(false);
    expect(iterationCells(cells, edges, "a")).toHaveLength(2);
  });
});
