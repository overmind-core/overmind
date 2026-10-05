import { Fragment } from "react";

import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { SavedEvidence } from "./common";
export type Metric = {
  mean?: number;
  candidate_minus_baseline?: number;
  decisions: number;
  groups: number;
  interval_95?: number[] | null;
};
type Coverage = {
  expected: number;
  scored: number;
  missing_predictions: number;
  invalid_predictions: number;
  incompatible_inputs: number;
  metrics: Record<string, Metric>;
};
export type Comparison = {
  baseline: { benchmarks: Record<string, Coverage>; model_identity?: string };
  candidate: { benchmarks: Record<string, Coverage>; model_identity?: string };
  benchmarks: Record<string, { metrics: Record<string, Metric> }>;
};
export type Comparisons = Record<string, Partial<Record<"raw" | "calibrated", Comparison>>>;
const labels: Record<string, string> = {
  accuracy: "Accuracy",
  brier: "Brier",
  cross_entropy: "Cross entropy",
  expected_accuracy: "Expected accuracy",
  mean_absolute_error: "Mean absolute error",
  ordinal_mae: "Ordinal MAE",
};
const number = (value: number | undefined) => (value === undefined ? "—" : value.toFixed(4));
export function ComparisonMetrics({
  comparisons,
  names,
  mode,
}: {
  comparisons: Comparisons;
  names: Record<string, string>;
  mode: "raw" | "calibrated";
}) {
  return (
    <div className="flex flex-col gap-6">
      {Object.entries(comparisons).map(([key, modes]) => {
        const comparison = modes[mode];
        if (!comparison) return null;
        return (
          <section className="min-w-0" key={key}>
            <h3 className="mb-2 text-sm font-medium">{names[key] ?? key}</h3>
            <div className="overflow-x-auto rounded-md border border-border">
              <Table>
                <TableHeader>
                  <TableRow>
                    <TableHead>Benchmark / metric</TableHead>
                    <TableHead>Baseline</TableHead>
                    <TableHead>Model</TableHead>
                    <TableHead>Difference · 95% interval</TableHead>
                    <TableHead>Denominator</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  {Object.entries(comparison.candidate.benchmarks).map(([benchmark, card]) => (
                    <Fragment key={benchmark}>
                      <TableRow>
                        <TableCell className="p-0" colSpan={5}>
                          <div className="flex flex-wrap items-center justify-between gap-2 bg-wash-subtle px-4 py-2">
                            <span className="font-medium">{benchmark}</span>
                            <span>
                              {card.scored} / {card.expected}
                            </span>
                            <span className="text-xs text-muted-foreground">
                              {card.missing_predictions} missing · {card.invalid_predictions}{" "}
                              invalid · {card.incompatible_inputs} incompatible
                            </span>
                          </div>
                        </TableCell>
                      </TableRow>
                      {Object.entries(card.metrics).map(([metric, score]) => {
                        const base = comparison.baseline.benchmarks[benchmark]?.metrics[metric];
                        const delta = comparison.benchmarks[benchmark]?.metrics[metric];
                        return (
                          <TableRow key={metric}>
                            <TableCell>{labels[metric] ?? metric.replaceAll("_", " ")}</TableCell>
                            <TableCell>{number(base?.mean)}</TableCell>
                            <TableCell>{number(score.mean)}</TableCell>
                            <TableCell>
                              {number(delta?.candidate_minus_baseline)}{" "}
                              {delta?.interval_95 &&
                                `[${delta.interval_95.map(number).join(", ")}]`}
                            </TableCell>
                            <TableCell>
                              {score.decisions} decisions · {score.groups} groups
                            </TableCell>
                          </TableRow>
                        );
                      })}
                    </Fragment>
                  ))}
                </TableBody>
              </Table>
            </div>
            <div className="mt-2">
              <SavedEvidence label="Calibration, class and slice diagnostics" value={comparison} />
            </div>
          </section>
        );
      })}
    </div>
  );
}
