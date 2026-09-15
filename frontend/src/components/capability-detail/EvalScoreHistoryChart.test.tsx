// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { EvaluatorScoreHistory } from "@/openapi";
import { EvalScoreHistoryChart } from "./EvalScoreHistoryChart";

afterEach(cleanup);

/** One series per name, scored highest-first so ranking follows the name order. */
const seriesFor = (names: string[]): EvaluatorScoreHistory[] =>
  names.map((name, i) => ({
    evaluatorId: name,
    name,
    points: [{ runAt: new Date(2026, 0, 1), runId: "run-1", score: 100 - i }],
  }));

const isVisible = (name: string) =>
  screen.getByRole("button", { name }).getAttribute("aria-pressed") === "true";

describe("EvalScoreHistoryChart", () => {
  it("hides everything past the top 6 by latest score", () => {
    render(<EvalScoreHistoryChart series={seriesFor(["a", "b", "c", "d", "e", "f", "g", "h"])} />);
    expect(isVisible("f")).toBe(true);
    expect(isVisible("g")).toBe(false);
    expect(isVisible("h")).toBe(false);
  });

  it("re-applies the limit when the series change without a remount", () => {
    const { rerender } = render(
      <EvalScoreHistoryChart series={seriesFor(["a", "b", "c", "d", "e", "f", "g", "h"])} />
    );
    expect(isVisible("g")).toBe(false);

    // "g" and "h" now rank top of the new set; "y" and "z" fall outside it.
    rerender(
      <EvalScoreHistoryChart series={seriesFor(["g", "h", "u", "v", "w", "x", "y", "z"])} />
    );
    expect(isVisible("g")).toBe(true);
    expect(isVisible("h")).toBe(true);
    expect(isVisible("y")).toBe(false);
    expect(isVisible("z")).toBe(false);
  });
});
