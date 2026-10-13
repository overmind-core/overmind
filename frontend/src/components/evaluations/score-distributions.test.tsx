// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import { seriesColor } from "@/lib/colors";
import { type DistributionScore, ScoreDistributions } from "./score-distributions";

afterEach(cleanup);

const variants = [{ id: "v1", label: "kimi", order: 0 }];

const scores: DistributionScore[] = [
  { name: "card-constraints", value: 0.5, variant: "v1" },
  { name: "card-constraints", value: 1, variant: "v1" },
  { name: "Task Success", value: 0.8, variant: "v1" },
  // Dataset-scope rows are aggregates, not per-sample scores — excluded.
  { name: "bleu", scope: "dataset", value: 0.4, variant: "v1" },
];

describe("ScoreDistributions", () => {
  it("keeps model colours across metrics, missing scores and metric selection", () => {
    const variants = [
      { id: "second", label: "Second", order: 1 },
      { id: "first", label: "First", order: 0 },
    ];
    const scores = [
      { name: "accuracy", value: 0.8, variant: "second" },
      { name: "quality", value: 0.9, variant: "first" },
      { name: "quality", value: 0.7, variant: "second" },
    ];
    const { container, rerender } = render(
      <ScoreDistributions metrics={["accuracy", "quality"]} scores={scores} variants={variants} />
    );
    const fills = () =>
      Array.from(container.querySelectorAll("svg rect"), (bar) => bar.getAttribute("fill"));
    expect(fills()).toEqual([seriesColor(1), seriesColor(0), seriesColor(1)]);
    rerender(<ScoreDistributions metrics={["quality"]} scores={scores} variants={variants} />);
    expect(fills()).toEqual([seriesColor(0), seriesColor(1)]);
  });

  it("defaults to the Scores view with humanized test names and mean labels", () => {
    const { container, getByRole } = render(
      <ScoreDistributions
        metrics={["card-constraints", "Task Success"]}
        scores={scores}
        variants={variants}
      />
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Card constraints");
    expect(text).toContain("Task success");
    expect(text).not.toContain("card-constraints");
    expect(getByRole("button", { name: "Scores", pressed: true })).toBeTruthy();
    expect(text).toContain("75%"); // card-constraints mean of 0.5 and 1
    expect(text).toContain("80%"); // Task success mean
    expect(container.querySelectorAll("svg rect").length).toBe(2);
  });

  it("draws every series' overlaid curve in the Distribution view", () => {
    const spread: DistributionScore[] = ["a-test", "b-test"].flatMap((name) =>
      [0.9, 0.95, 1, 1].map((value) => ({ name, value, variant: "v1" }))
    );
    const { container, getByRole } = render(
      <ScoreDistributions metrics={["a-test", "b-test"]} scores={spread} variants={variants} />
    );
    fireEvent.click(getByRole("button", { name: "Distribution" }));
    expect(container.querySelectorAll('svg[data-slot="score-plot"] path').length).toBe(4);
  });

  it("shows identical all-100% series as one thick tick per lane in Box & Whisker", () => {
    const flood: DistributionScore[] = ["a-test", "b-test"].flatMap((name) =>
      [1, 1, 1, 1].map((value) => ({ name, value, variant: "v1" }))
    );
    const { container, getByRole } = render(
      <ScoreDistributions metrics={["a-test", "b-test"]} scores={flood} variants={variants} />
    );
    fireEvent.click(getByRole("button", { name: "Box & Whisker" }));
    // Zero-variance series render a thick tick, not a zero-width box.
    expect(
      container.querySelectorAll('svg[data-slot="score-plot"] line[stroke-width="5"]').length
    ).toBe(2);
  });

  it("renders nothing without per-sample scores", () => {
    const { container } = render(
      <ScoreDistributions
        metrics={["bleu"]}
        scores={[{ name: "bleu", scope: "dataset", value: 0.4, variant: "v1" }]}
        variants={variants}
      />
    );
    expect(container.textContent).toBe("");
  });
});
