// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Capability, DeployedModel } from "@/openapi";

const mocks = vi.hoisted(() => ({ useDeployedModelQuery: vi.fn() }));

// Unpolled: the bar only names the deployment, so a 10s loop is pure cost.
vi.mock("@/hooks/use-inference", () => ({
  useDeployedModelQuery: mocks.useDeployedModelQuery,
}));

// The @lobehub icon ESM subpaths behind the provider logo don't resolve under
// vitest's node resolver; the chip's chrome isn't the assertion.
vi.mock("@/components/model-provider-chip", () => ({
  getModelProviderInfo: (id: string) => ({ id, modelLabel: id, providerLabel: id }),
  getProviderIcon: () => () => null,
  ModelProviderChip: ({ model }: { model: string }) => <span>{model}</span>,
  ProviderLogo: () => null,
}));

import { CapabilityFactsBar } from "./CapabilityFactsBar";

const capability = (activeModel: string | null = null) =>
  ({
    activeModel,
    flow: { llmUtilities: [], model: "gpt-4o-mini" },
    id: "00000000-0000-4000-8000-000000000010",
    name: "invoice-capability",
    sourcePath: "agents/invoice.py",
  }) as unknown as Capability;

const deployment = {
  baseModelId: "gpt-4o-mini",
  capabilityName: "invoice-capability",
  finetuningJobName: "nightly",
  modelId: "ft:m-1",
  status: "ready",
} as unknown as DeployedModel;

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe("CapabilityFactsBar model cell", () => {
  it("shows the code-level model and skips the lookup when unrouted", () => {
    mocks.useDeployedModelQuery.mockReturnValue({ data: undefined, isLoading: false });

    render(<CapabilityFactsBar capability={capability()} />);

    expect(screen.getByText("gpt-4o-mini")).toBeTruthy();
    expect(mocks.useDeployedModelQuery).toHaveBeenCalledWith(undefined, { poll: false });
    expect(screen.getAllByText("Model")).toHaveLength(1);
    expect(screen.queryByText("Serving")).toBeNull();
  });

  it("shows the served deployment when routed, with the code-level model in the tooltip", () => {
    mocks.useDeployedModelQuery.mockReturnValue({ data: deployment, isLoading: false });

    render(<CapabilityFactsBar capability={capability("m-1")} />);

    expect(mocks.useDeployedModelQuery).toHaveBeenCalledWith("m-1", { poll: false });
    expect(screen.getByText("nightly")).toBeTruthy();
    // Reachable by name on a real control, not by a pointer-only `title`.
    expect(
      screen.getByRole("button", { name: "Serving ft:m-1 — the code names gpt-4o-mini" })
    ).toBeTruthy();
    // The served fine-tune replaces the chip in place: one cell, not two.
    expect(screen.queryByText("gpt-4o-mini")).toBeNull();
    expect(screen.getAllByText("Serving")).toHaveLength(1);
    expect(screen.queryByText("Model")).toBeNull();
  });

  it("falls back to the code-level model when the routed deployment can't be read", () => {
    mocks.useDeployedModelQuery.mockReturnValue({ data: undefined, isLoading: false });

    render(<CapabilityFactsBar capability={capability("m-1")} />);

    expect(screen.getByText("gpt-4o-mini")).toBeTruthy();
  });
});
