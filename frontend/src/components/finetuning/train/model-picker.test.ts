import { describe, expect, it, vi } from "vitest";

import type { ModelCatalog, ModelEntry } from "@/hooks/use-finetuning";
import type { FinetuningExperiment } from "@/openapi";

// The @lobehub icon ESM subpaths behind the provider chip don't resolve under vitest's
// node resolver; these are pure ranking functions and never touch it.
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: () => ({}),
  getProviderIcon: () => null,
  ProviderLogo: () => null,
}));

import { findCatalogModel, rankedModels } from "./model-picker";

const entry = (id: string, totalParamsB: number, over: Partial<ModelEntry> = {}): ModelEntry =>
  ({ display: id, id, params: `${totalParamsB}B`, totalParamsB, ...over }) as ModelEntry;

const CATALOG = {
  models: {
    compact: [entry("vendor/tiny-1b", 1.2), entry("vendor/retired-2b", 2, { disabled: true })],
    mid: [entry("vendor/big-32b", 32)],
    small: [entry("vendor/mid-9b", 9)],
  },
  tiers: ["compact", "small", "mid"],
} as unknown as ModelCatalog;

const graded = (match: number | null): FinetuningExperiment => ({ match }) as FinetuningExperiment;

const GRADES = new Map<string, FinetuningExperiment>([
  ["vendor/mid-9b", graded(98)],
  ["vendor/big-32b", graded(64)],
  ["vendor/tiny-1b", graded(null)],
]);

describe("rankedModels", () => {
  it("orders by match, highest first", () => {
    expect(rankedModels(CATALOG, GRADES).map((m) => m.model.id)).toEqual([
      "vendor/mid-9b",
      "vendor/big-32b",
      "vendor/tiny-1b",
    ]);
  });

  it("sorts an ungraded model last rather than as a zero", () => {
    const grades = new Map([["vendor/big-32b", graded(1)]]);

    expect(rankedModels(CATALOG, grades).map((m) => m.model.id)).toEqual([
      "vendor/big-32b",
      "vendor/tiny-1b",
      "vendor/mid-9b",
    ]);
  });

  it("carries the match onto the row", () => {
    expect(rankedModels(CATALOG, GRADES)[0]).toMatchObject({ match: 98, tier: "small" });
  });

  it("falls back to size order with no grades at all", () => {
    expect(rankedModels(CATALOG).map((m) => m.model.id)).toEqual([
      "vendor/tiny-1b",
      "vendor/mid-9b",
      "vendor/big-32b",
    ]);
  });

  it("drops models already in the comparison", () => {
    const shown = rankedModels(CATALOG, GRADES, new Set(["vendor/mid-9b"]));

    expect(shown.map((m) => m.model.id)).toEqual(["vendor/big-32b", "vendor/tiny-1b"]);
  });
});

describe("findCatalogModel", () => {
  it("resolves a model to its tier", () => {
    expect(findCatalogModel(CATALOG, "vendor/big-32b")?.tier).toBe("mid");
  });

  it("resolves nothing for an id the catalog does not carry", () => {
    expect(findCatalogModel(CATALOG, "vendor/absent-70b")).toBeNull();
  });
});
