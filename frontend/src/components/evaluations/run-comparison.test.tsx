import { describe, expect, it } from "vitest";

import {
  aggregateEvaluator,
  buildComparisonRows,
  type ComparisonSummary,
  overallAggregate,
  primaryValue,
  runTrust,
} from "./run-comparison";

function summary(
  metrics: Record<string, { mean: number | null; pass_rate?: number | null; n?: number }>,
  extra: Partial<ComparisonSummary> = {}
): ComparisonSummary {
  return {
    metrics: Object.keys(metrics),
    variants: {
      v1: {
        label: "v1",
        metrics: Object.fromEntries(
          Object.entries(metrics).map(([name, c]) => [
            name,
            { mean: c.mean, n: c.n ?? 10, pass_rate: c.pass_rate ?? null },
          ])
        ),
      },
    },
    ...extra,
  };
}

describe("aggregateEvaluator", () => {
  it("averages per-variant means and skips missing scores", () => {
    const s: ComparisonSummary = {
      metrics: ["faithfulness"],
      variants: {
        a: { label: "a", metrics: { faithfulness: { mean: 0.6, n: 5, pass_rate: 0.5 } } },
        b: { label: "b", metrics: { faithfulness: { mean: 0.8, n: 5, pass_rate: 0.7 } } },
        c: { label: "c", metrics: {} },
      },
    };
    const agg = aggregateEvaluator(s, "faithfulness");
    expect(agg.mean).toBeCloseTo(0.7);
    expect(agg.passRate).toBeCloseTo(0.6);
    expect(agg.n).toBe(10);
    expect(agg.variantCount).toBe(2);
  });

  it("falls back to pass-rate when mean is absent", () => {
    const s = summary({ gate: { mean: null, pass_rate: 0.9 } });
    expect(primaryValue(aggregateEvaluator(s, "gate"))).toBeCloseTo(0.9);
  });
});

describe("buildComparisonRows", () => {
  it("classifies improved / regressed / unchanged / added / removed", () => {
    const current = summary({
      added_metric: { mean: 0.5 },
      improved_metric: { mean: 0.9 },
      regressed_metric: { mean: 0.4 },
      steady_metric: { mean: 0.7 },
    });
    const baseline = summary({
      improved_metric: { mean: 0.6 },
      regressed_metric: { mean: 0.8 },
      removed_metric: { mean: 0.5 },
      steady_metric: { mean: 0.7 },
    });
    const rows = buildComparisonRows(current, baseline);
    const byName = Object.fromEntries(rows.map((r) => [r.name, r]));

    expect(byName.improved_metric.status).toBe("improved");
    expect(byName.improved_metric.delta).toBeCloseTo(0.3);
    expect(byName.regressed_metric.status).toBe("regressed");
    expect(byName.regressed_metric.delta).toBeCloseTo(-0.4);
    expect(byName.steady_metric.status).toBe("unchanged");
    expect(byName.added_metric.status).toBe("added");
    expect(byName.added_metric.baseline).toBeNull();
    expect(byName.removed_metric.status).toBe("removed");
    expect(byName.removed_metric.current).toBeNull();
  });

  it("orders regressions and lost coverage ahead of gains", () => {
    const current = summary({
      down: { mean: 0.3 },
      gone: { mean: null },
      up: { mean: 0.95 },
    });
    const baseline = summary({
      down: { mean: 0.9 },
      gone: { mean: 0.8 },
      up: { mean: 0.5 },
    });
    const rows = buildComparisonRows(current, baseline);
    expect(rows.map((r) => r.status)).toEqual(["regressed", "removed", "improved"]);
  });
});

describe("overallAggregate", () => {
  it("averages across evaluators", () => {
    const s = summary({ a: { mean: 0.4 }, b: { mean: 0.8 } });
    expect(overallAggregate(s).mean).toBeCloseTo(0.6);
  });
});

describe("runTrust", () => {
  it("flags degraded / errored runs as untrusted and ignores benign N/A", () => {
    expect(runTrust(summary({ a: { mean: 0.5 } })).trusted).toBe(true);
    expect(
      runTrust(summary({ a: { mean: 0.5 } }, { trust: { degraded: 2, total: 10 } })).trusted
    ).toBe(false);
    expect(
      runTrust(summary({ a: { mean: 0.5 } }, { error_counts: { evaluator_errors: 1 } })).trusted
    ).toBe(false);
    const naOnly = runTrust(
      summary({ a: { mean: 0.5 } }, { applicability: { by_evaluator: { a: 3 }, total: 3 } })
    );
    expect(naOnly.trusted).toBe(true);
    expect(naOnly.notApplicable).toBe(3);
  });
});
