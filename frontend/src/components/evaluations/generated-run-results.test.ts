import { expect, it } from "vitest";

import type { EvalRun, EvalRunRow } from "@/openapi";
import { alignRunDatapoints, matchesDatapoint } from "./generated-run-results";

const run = (id: string, cell = "same-cell") => ({ cell, id }) as EvalRun;
const row = (id: number, rowIndex: number, extra = {}) =>
  ({
    id,
    input: "question",
    output: String(id),
    reference: "answer",
    rowIndex,
    ...extra,
  }) as EvalRunRow;

it("aligns row zero across models while preserving each output and missing results", () => {
  const points = alignRunDatapoints([
    { rows: [row(1, 0), row(2, 1)], run: run("a") },
    { rows: [row(3, 0)], run: run("b") },
  ]);
  expect(points).toHaveLength(2);
  expect(points[0].samples.a.output).toBe("1");
  expect(points[0].samples.b.output).toBe("3");
  expect(points[1].samples.b).toBeUndefined();
});

it("does not merge different versions, conflicting inputs, duplicates or unknown historical indices", () => {
  const points = alignRunDatapoints([
    {
      rows: [row(1, 0), row(2, 0), row(3, 9, { metadata: { legacy_sample: { row_index: null } } })],
      run: run("a"),
    },
    { rows: [row(4, 0)], run: run("b", "other-cell") },
    {
      rows: [
        row(5, 0, { input: "different" }),
        row(6, 9, { metadata: { legacy_sample: { row_index: null } } }),
      ],
      run: run("c"),
    },
  ]);
  expect(points).toHaveLength(6);
});

it("uses the retained historical index instead of its synthetic display index", () => {
  const points = alignRunDatapoints([
    { rows: [row(1, 99, { metadata: { legacy_sample: { row_index: 0 } } })], run: run("a") },
    { rows: [row(2, 0)], run: run("b") },
  ]);
  expect(points).toHaveLength(1);
  expect(points[0].label).toBe("0");
});

it("aligns historical rows from before source indices were added to metadata", () => {
  const points = alignRunDatapoints([
    { rows: [row(1, 0, { metadata: { legacy_sample: { id: "old" } } })], run: run("a") },
    { rows: [row(2, 0)], run: run("b") },
  ]);
  expect(points).toHaveLength(1);
});

it("filters the selected model's own outcome and searches full output text", () => {
  const points = alignRunDatapoints([
    { rows: [row(1, 0, { output: "successful response", score: 1 })], run: run("a") },
    { rows: [row(2, 0, { output: "failed response", score: 0 })], run: run("b") },
  ]);
  expect(matchesDatapoint(points[0], "failed", "a", "all")).toBe(false);
  expect(matchesDatapoint(points[0], "failed", "b", "failing")).toBe(true);
  expect(matchesDatapoint(points[0], "", "a", "failing")).toBe(false);
  expect(matchesDatapoint(points[0], "", "missing", "all")).toBe(false);
});

it("keeps technical errors and absent scores separate from quality failures", () => {
  const [point] = alignRunDatapoints([
    { rows: [row(1, 0, { error: "Judge unavailable", score: 0 })], run: run("a") },
  ]);
  expect(matchesDatapoint(point, "", "all", "failing")).toBe(false);
  expect(matchesDatapoint(point, "", "all", "errored")).toBe(true);
  const [unscored] = alignRunDatapoints([{ rows: [row(2, 0, { score: null })], run: run("a") }]);
  expect(matchesDatapoint(unscored, "", "all", "failing")).toBe(false);
  expect(matchesDatapoint(unscored, "", "all", "unscored")).toBe(true);
});
