// @vitest-environment jsdom
import { cleanup, render } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { EvalRunOperationalStat } from "@/openapi";
import {
  EvalWinnerCallout,
  type OverviewVariant,
  PerModelOps,
  type VariantSummary,
} from "./eval-results-overview";

// @lobehub icon ESM subpaths don't resolve under vitest's node resolver.
vi.mock("@/components/model-provider-chip", () => ({
  ModelProviderChip: ({ model }: { model: string }) => <span>{model}</span>,
}));

afterEach(cleanup);

const metrics = ["faithfulness", "toxicity"];

function makeOps(variantId: string, total: number): EvalRunOperationalStat {
  return {
    evalCost: total * 0.6,
    evalLatencyMs: 800,
    genCost: total * 0.4,
    genLatencyMs: null,
    label: variantId,
    sampleCount: 10,
    totalCost: total,
    totalTokens: null,
    variantId,
  };
}

describe("EvalWinnerCallout + PerModelOps", () => {
  it("renders cost/latency for a single model and omits the winner callout", () => {
    const variants: OverviewVariant[] = [
      { id: "v1", isBaseline: true, label: "kimi", order: 0, resolvedModel: "moonshotai/kimi-k2" },
    ];
    const summaryVariants: Record<string, VariantSummary> = {
      v1: {
        label: "kimi",
        metrics: {
          faithfulness: { mean: 0.8, n: 10, pass_rate: 0.7 },
          toxicity: { mean: 0.1, n: 10, pass_rate: 0.2 },
        },
      },
    };
    const { container } = render(
      <>
        <EvalWinnerCallout
          metrics={metrics}
          operational={[makeOps("v1", 0.067)]}
          summaryVariants={summaryVariants}
          variants={variants}
        />
        <PerModelOps operational={[makeOps("v1", 0.067)]} variants={variants} />
      </>
    );
    const text = container.textContent ?? "";
    // The page's Operations heading titles this table; it carries no header of its own.
    expect(text).not.toContain("Credits & latency by model");
    // The "Evaluation score" chart belongs to the score-distributions card.
    expect(text).not.toContain("Evaluation score");
    expect(text).toContain("Total credits");
    expect(container.querySelectorAll('[title="7 credits"]').length).toBeGreaterThanOrEqual(1); // $0.067 at 100 credits/$1
    // Gen cost is the provider's own $ spend, not converted to credits.
    expect(text).toContain("Gen cost");
    expect(text).toContain("$0.027"); // genCost = 0.067 * 0.4
    expect(text).not.toContain("Best overall");
  });

  it("surfaces the best model across all criteria when multiple models are tested", () => {
    const variants: OverviewVariant[] = [
      { id: "v1", label: "weak", order: 0, resolvedModel: "openai/gpt-4o-mini" },
      { id: "v2", label: "strong", order: 1, resolvedModel: "anthropic/claude-sonnet-4-5" },
    ];
    const summaryVariants: Record<string, VariantSummary> = {
      v1: {
        label: "weak",
        metrics: {
          faithfulness: { mean: 0.4, n: 10, pass_rate: 0.4 },
          toxicity: { mean: 0.3, n: 10, pass_rate: 0.3 },
        },
      },
      v2: {
        label: "strong",
        metrics: {
          faithfulness: { mean: 0.9, n: 10, pass_rate: 0.9 },
          toxicity: { mean: 0.85, n: 10, pass_rate: 0.8 },
        },
      },
    };
    const { container } = render(
      <EvalWinnerCallout
        metrics={metrics}
        operational={[makeOps("v1", 0.02), makeOps("v2", 0.05)]}
        summaryVariants={summaryVariants}
        variants={variants}
      />
    );
    const text = container.textContent ?? "";
    expect(text).toContain("Best overall");
    expect(text).toContain("88%"); // winner v2 avg mean 87.5%, rounded
  });

  it("shows a dash for a fine-tuned model's gen cost and converts eval cost to credits", () => {
    const variants: OverviewVariant[] = [
      { id: "v1", isBaseline: true, label: "base", order: 0, resolvedModel: "openai/gpt-4o-mini" },
      // ft-{jobUuid8}-{base-slug} marks a user's own fine-tuned model
      // (finetuned-serving-id.ts); its gen cost isn't tracked, so it renders "-".
      { id: "v2", label: "fine-tuned", order: 1, resolvedModel: "ft-750caa9f-gpt-4o-mini" },
    ];
    const summaryVariants: Record<string, VariantSummary> = {
      v1: { label: "base", metrics: { faithfulness: { mean: 0.6, n: 10, pass_rate: 0.6 } } },
      v2: { label: "fine-tuned", metrics: { faithfulness: { mean: 0.7, n: 10, pass_rate: 0.7 } } },
    };
    const ftOps: EvalRunOperationalStat = {
      evalCost: 0.03,
      evalLatencyMs: 800,
      genCost: 0.01,
      genLatencyMs: null,
      label: "fine-tuned",
      sampleCount: 10,
      totalCost: 0.04,
      totalTokens: null,
      variantId: "v2",
    };
    const { container } = render(
      <>
        <EvalWinnerCallout
          metrics={["faithfulness"]}
          operational={[makeOps("v1", 0.02), ftOps]}
          summaryVariants={summaryVariants}
          variants={variants}
        />
        <PerModelOps operational={[makeOps("v1", 0.02), ftOps]} variants={variants} />
      </>
    );
    const text = container.textContent ?? "";
    expect(text).not.toContain("$0.01"); // never the raw (untracked) gen cost
    expect(text).toContain("$0.0080"); // v1 genCost = 0.02 * 0.4
    // Eval cost $0.03 → 3 credits; the fine-tuned total drops gen cost, so it
    // is 3 credits too.
    expect(container.querySelectorAll('[title="3 credits"]').length).toBeGreaterThanOrEqual(2);
    expect(text).not.toContain("$0.04");
    expect(text).not.toContain("$0.030");
  });
});
