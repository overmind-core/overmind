// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it } from "vitest";

import { TooltipProvider } from "@/components/ui/tooltip";
import { ConfidenceMetric, summarizeConfidence } from "./confidence-metric";

afterEach(cleanup);

it("weights confidence bands by decisions and distinguishes soft references from correctness", () => {
  const evidence = {
    metrics: { accuracy: { decisions: 100 }, top1_reference_mass: { decisions: 100 } },
    reliability: [
      { confidence: 0.95, decisions: 90, reference_mass: 0.7 },
      { confidence: 0.5, decisions: 10, reference_mass: 0.6 },
    ],
  };
  const summary = summarizeConfidence(evidence)!;
  expect(summary.confidence).toBeCloseTo(0.905);
  expect(summary.observed).toBeCloseTo(0.69);
  expect(summary.error).toBeCloseTo(0.235);
  expect(summary.observedLabel).toBe("Correct");
  expect(
    summarizeConfidence({ ...evidence, metrics: { top1_reference_mass: { decisions: 100 } } })
      ?.observedLabel
  ).toBe("Reference support");
  expect(
    summarizeConfidence({
      ...evidence,
      metrics: { ...evidence.metrics, accuracy: { decisions: 20 } },
    })?.observedLabel
  ).toBe("Reference support");
});

it("retains calibration error when opposite confidence gaps cancel in the averages", () => {
  const summary = summarizeConfidence({
    metrics: { top1_reference_mass: { decisions: 20 } },
    reliability: [
      { confidence: 0.9, decisions: 10, reference_mass: 0.7 },
      { confidence: 0.5, decisions: 10, reference_mass: 0.7 },
    ],
  })!;
  expect(summary.confidence).toBeCloseTo(summary.observed);
  expect(summary.error).toBeCloseTo(0.2);
});

it("does not invent measurements for missing or inconsistent reliability evidence", () => {
  expect(summarizeConfidence({ metrics: {} })).toBeNull();
  expect(
    summarizeConfidence({
      metrics: { top1_reference_mass: { decisions: 20 } },
      reliability: [{ confidence: 0.9, decisions: 10, reference_mass: 0.7 }],
    })
  ).toBeNull();
  expect(
    summarizeConfidence({
      metrics: { top1_reference_mass: { decisions: 10 } },
      reliability: [{ confidence: Number.NaN, decisions: 10, reference_mass: 0.7 }],
    })
  ).toBeNull();
});

it("shows overconfidence, underconfidence and adjusted results without a mode switch", () => {
  const original = summarizeConfidence({
    metrics: { accuracy: { decisions: 100 }, top1_reference_mass: { decisions: 100 } },
    reliability: [{ confidence: 0.95, decisions: 100, reference_mass: 0.712 }],
  })!;
  render(
    <TooltipProvider>
      <ConfidenceMetric
        benchmark="Sentiment"
        rows={[
          {
            adjusted: { ...original, confidence: 0.65, error: 0.062 },
            colorIndex: 0,
            key: "model",
            name: "Model",
            original,
          },
        ]}
      />
    </TooltipProvider>
  );
  expect(screen.getByRole("heading", { name: "Confidence" })).toBeTruthy();
  expect(screen.getByText("Claimed 95.0%")).toBeTruthy();
  expect(screen.getAllByText("Correct 71.2%")).toHaveLength(2);
  expect(screen.getByRole("button", { name: /Overconfident by 23.8 pp/ })).toBeTruthy();
  expect(screen.getByRole("button", { name: /Underconfident by 6.2 pp/ })).toBeTruthy();
  expect(screen.getByText("Calibration error 6.2 pp")).toBeTruthy();
  expect(screen.getByRole("button", { name: /Model · Adjusted/ })).toBeTruthy();
  expect(screen.queryByRole("tablist")).toBeNull();
});
