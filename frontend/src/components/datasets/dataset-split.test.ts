import { describe, expect, it } from "vitest";

import { evaluationRows } from "./dataset-split";

describe("evaluation row count", () => {
  it.each([
    [10, 30, 3],
    [15, 30, 5],
    [25, 30, 8],
    [5, 50, 3],
    [7, 50, 4],
    [2, 1, 1],
    [2, 99, 1],
    [0, 30, 0],
    [1, 30, 0],
  ])("matches the backend cut for %i rows at %i percent", (rows, percent, expected) => {
    expect(evaluationRows(rows, percent)).toBe(expected);
  });
});
