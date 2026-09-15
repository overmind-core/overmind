import { describe, expect, it } from "vitest";

import type { ClassMetrics, FinetuningJudgeEvalRow } from "@/hooks/use-finetuning";
import {
  buildClassSeries,
  buildMacroSeries,
  evalStepOf,
  latestClassMetrics,
} from "./class-metrics";

const metricsAt = (f1: number): ClassMetrics => ({
  aggregates: {
    accuracy: f1,
    macro: { f1, precision: f1 + 0.01, recall: f1 - 0.01 },
    n: 10,
  },
  classes: [
    { f1, label: "refund", precision: f1 + 0.1, recall: f1 - 0.1, support: 6 },
    { f1: f1 / 2, label: "fraud", precision: f1 / 2, recall: f1 / 2, support: 4 },
  ],
  confusion_matrix: {
    labels: ["fraud", "refund"],
    matrix: [
      [3, 1],
      [1, 5],
    ],
  },
});

const row = (
  kind: string,
  step: number | null,
  cm: ClassMetrics | null
): FinetuningJudgeEvalRow => ({
  checkpoint_step: step,
  class_metrics: cm,
  id: `${kind}-${step}`,
  kind,
  status: "completed",
});

const rows: FinetuningJudgeEvalRow[] = [
  row("baseline", null, metricsAt(0.4)),
  row("checkpoint", 24, metricsAt(0.6)),
  row("final", null, metricsAt(0.8)),
  row("checkpoint", 12, null),
];

describe("evalStepOf", () => {
  it("orders baseline, checkpoints, and final on one axis", () => {
    expect(evalStepOf(rows[0], 24)).toBe(0);
    expect(evalStepOf(rows[1], 24)).toBe(24);
    expect(evalStepOf(rows[2], 24)).toBe(25);
  });
});

describe("latestClassMetrics", () => {
  it("prefers final over checkpoints over baseline", () => {
    expect(latestClassMetrics(rows)?.row.kind).toBe("final");
    expect(latestClassMetrics(rows.slice(0, 2))?.row.kind).toBe("checkpoint");
    expect(latestClassMetrics([rows[0]])?.row.kind).toBe("baseline");
    expect(latestClassMetrics([rows[3]])).toBeNull();
  });
});

describe("buildClassSeries", () => {
  it("builds one sorted series per class for the chosen metric", () => {
    const series = buildClassSeries(rows, "f1");
    expect(series.map((s) => s.id).sort()).toEqual(["fraud", "refund"]);
    const refund = series.find((s) => s.id === "refund")!;
    expect(refund.points).toEqual([
      { step: 0, value: 0.4 },
      { step: 24, value: 0.6 },
      { step: 25, value: 0.8 },
    ]);
  });

  it("is empty when no row has class metrics", () => {
    expect(buildClassSeries([rows[3]], "precision")).toEqual([]);
  });
});

describe("buildMacroSeries", () => {
  it("returns precision, recall, and F1 macro lines", () => {
    const series = buildMacroSeries(rows);
    expect(series.map((s) => s.id)).toEqual(["macro-precision", "macro-recall", "macro-f1"]);
    expect(series[2].points.map((p) => p.value)).toEqual([0.4, 0.6, 0.8]);
  });
});
