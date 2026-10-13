// @vitest-environment jsdom
import { cleanup, render, screen, within } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import type { NativeCallProgress, NativeSample } from "@/openapi";
import { SampleModelOutput } from "./probability-evaluation-monitor";

afterEach(cleanup);

const model: NativeCallProgress = {
  error: "",
  key: "model",
  name: "Model",
  observedAt: null,
  progress: null,
  progressError: "",
  stage: "completed",
  state: "completed",
};

function sample(probabilities = [0.7, 0.3], target = [0, 1]): NativeSample {
  return {
    benchmark: "Fixture",
    error: "",
    index: 0,
    input: { kind: "choice", options: ["review_required", "block_transaction"] },
    key: "0",
    predictions: {
      model: {
        calibrated: null,
        calibratedMetrics: {},
        error: "",
        metrics: { accuracy: 0 },
        raw: { probabilities },
      },
    },
    reference: { probabilities: target },
  };
}

it("marks the target and incorrect prediction on their respective option rows", () => {
  render(<SampleModelOutput model={model} sample={sample()} stopped />);
  expect(
    within(screen.getByRole("row", { name: /review_required/ })).getByText("Predicted")
  ).toBeTruthy();
  expect(
    within(screen.getByRole("row", { name: /block_transaction/ })).getByText("Target")
  ).toBeTruthy();
  expect(screen.getByText("Predicted").className).toContain("text-destructive");
  for (const label of ["Target", "Predicted"]) {
    const marker = screen.getByText(label);
    expect(marker.closest('[data-slot="badge"]')).toBeNull();
    expect(marker.closest("tr")?.className).not.toMatch(/bg-(info|destructive|success)\//);
  }
});

it("keeps both text markers on a correctly predicted target", () => {
  const value = sample([0.1, 0.9]);
  value.predictions.model.metrics.accuracy = 1;
  render(<SampleModelOutput model={model} sample={value} stopped />);
  const row = within(screen.getByRole("row", { name: /block_transaction/ }));
  expect(row.getByText("Target")).toBeTruthy();
  expect(row.getByText("Predicted").className).toContain("text-success");
});

it("distinguishes soft target peaks and the scored first-option tie break", () => {
  const value = sample([0.5, 0.5], [0.5, 0.5]);
  value.predictions.model.metrics = {};
  render(<SampleModelOutput model={model} sample={value} stopped />);
  expect(screen.getAllByText("Target peak")).toHaveLength(2);
  expect(screen.queryByText("Target")).toBeNull();
  expect(
    within(screen.getByRole("row", { name: /review_required/ })).getByText("Predicted · tied")
  ).toBeTruthy();
  expect(
    within(screen.getByRole("row", { name: /block_transaction/ })).queryByText("Predicted · tied")
  ).toBeNull();
});

it("marks a different adjusted prediction without replacing the raw prediction", () => {
  const value = sample();
  value.predictions.model.calibrated = { probabilities: [0.2, 0.8] };
  render(<SampleModelOutput model={model} sample={value} stopped />);
  expect(
    within(screen.getByRole("row", { name: /review_required/ })).getByText("Predicted")
  ).toBeTruthy();
  expect(
    within(screen.getByRole("row", { name: /block_transaction/ })).getByText("Adjusted prediction")
  ).toBeTruthy();
});

it.each(["missing", "invalid", "mean"])("does not invent a selected label for %s data", (kind) => {
  const value = sample();
  if (kind === "missing") value.predictions = {};
  if (kind === "invalid") value.predictions.model.error = "Invalid output";
  if (kind === "mean") {
    value.reference = { mean: 0.4, values: [0, 1] };
    value.predictions = {};
  }
  render(<SampleModelOutput model={model} sample={value} stopped />);
  expect(screen.queryByText(/^Predicted( · tied)?$/)).toBeNull();
  if (kind === "mean") expect(screen.queryByText("Target")).toBeNull();
});
