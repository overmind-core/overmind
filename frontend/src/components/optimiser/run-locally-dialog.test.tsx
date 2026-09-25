// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  capabilities: vi.fn(),
  catalog: vi.fn(),
  datasets: vi.fn(),
  deployed: vi.fn(),
  writeText: vi.fn(),
}));

vi.mock("@/hooks/use-evaluations", () => ({
  useModelCatalogQuery: () =>
    mocks.catalog() ?? {
      data: {
        defaults: {
          backtestModels: ["openai/gpt-5-mini", "anthropic/claude-sonnet-4"],
          judgeModel: "openai/gpt-5-mini",
          judgeModels: ["openai/gpt-5-mini"],
        },
        models: [
          {
            contextLength: null,
            id: "openai/gpt-5-mini",
            name: "GPT-5 Mini",
            provider: "openai",
          },
          {
            contextLength: null,
            id: "anthropic/claude-sonnet-4",
            name: "Claude Sonnet 4",
            provider: "anthropic",
          },
          {
            contextLength: null,
            id: "google/gemini-2.5-pro",
            name: "Gemini 2.5 Pro",
            provider: "google",
          },
          {
            contextLength: null,
            id: "xai/grok-4",
            name: "Grok 4",
            provider: "xai",
          },
          {
            contextLength: null,
            id: "deepseek/deepseek-chat",
            name: "DeepSeek Chat",
            provider: "deepseek",
          },
          {
            contextLength: null,
            id: "qwen/qwen3-32b",
            name: "Qwen3 32B",
            provider: "qwen",
          },
          {
            contextLength: null,
            id: "openai/gpt-5-mini:batch",
            name: "GPT-5 Mini (batch)",
            provider: "openai",
          },
          {
            contextLength: null,
            id: "anthropic/claude-sonnet-4:batch",
            name: "Claude Sonnet 4 (batch)",
            provider: "anthropic",
          },
        ],
      },
      error: null,
      isLoading: false,
      refetch: vi.fn(),
    },
  useProjectCapabilitiesQuery: (projectId: string | undefined) =>
    mocks.capabilities(projectId) ?? { data: { results: [] } },
  useProjectDatasetsForEvalQuery: () =>
    mocks.datasets() ?? { data: { results: [] }, error: null, isLoading: false, refetch: vi.fn() },
}));

vi.mock("@/hooks/use-inference", () => ({
  useDeployedModelsQuery: () =>
    mocks.deployed() ?? { data: { results: [] }, error: null, isLoading: false },
}));

vi.mock("@/components/model-provider-chip", () => ({
  getProviderIcon: () => undefined,
  ModelProviderChip: ({ children, model }: { children?: React.ReactNode; model: string }) => (
    <span>
      {model}
      {children}
    </span>
  ),
  ProviderLogo: () => null,
}));

vi.mock("@/components/ui/select", () => ({
  Select: ({
    children,
    onValueChange,
    value,
  }: {
    children: React.ReactNode;
    onValueChange?: (value: string) => void;
    value?: string;
  }) => (
    <div
      data-select-value={value}
      onClick={(event) => {
        const item = (event.target as HTMLElement).closest("[data-select-item]");
        const next = item?.getAttribute("data-value");
        if (next) onValueChange?.(next);
      }}
      onKeyDown={(event) => {
        if (event.key !== "Enter" && event.key !== " ") return;
        const item = (event.target as HTMLElement).closest("[data-select-item]");
        const next = item?.getAttribute("data-value");
        if (next) onValueChange?.(next);
      }}
    >
      {children}
    </div>
  ),
  SelectContent: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
  SelectItem: ({ children, value }: { children: React.ReactNode; value: string }) => (
    <button data-select-item data-value={value} type="button">
      {children}
    </button>
  ),
  SelectTrigger: ({ children, ...props }: { children: React.ReactNode; "aria-label"?: string }) => (
    <button type="button" {...props}>
      {children}
    </button>
  ),
  SelectValue: ({ placeholder }: { placeholder?: string }) => <span>{placeholder}</span>,
}));

import {
  buildBacktestPrompt,
  buildOptimisePrompt,
  isBatchModel,
  isComparisonOptionDisabled,
  MAX_COMPARISON_MODELS,
  RunLocallyDialog,
  toggleComparisonModel,
} from "./run-locally-dialog";

afterEach(() => {
  cleanup();
  mocks.capabilities.mockReset();
  mocks.datasets.mockReset();
  mocks.deployed.mockReset();
  mocks.writeText.mockReset();
});

describe("toggleComparisonModel", () => {
  it("adds and removes models and refuses a sixth selection", () => {
    const five = ["a", "b", "c", "d", "e"];
    expect(toggleComparisonModel(five, "f")).toEqual(five);
    expect(toggleComparisonModel(five, "c")).toEqual(["a", "b", "d", "e"]);
    expect(toggleComparisonModel([], "a")).toEqual(["a"]);
    expect(toggleComparisonModel(["only"], "only")).toEqual([]);
    expect(MAX_COMPARISON_MODELS).toBe(5);
  });

  it("refuses mixing batch and standard models", () => {
    expect(toggleComparisonModel(["openai/gpt-5-mini"], "openai/gpt-5-mini:batch")).toEqual([
      "openai/gpt-5-mini",
    ]);
    expect(toggleComparisonModel(["openai/gpt-5-mini:batch"], "anthropic/claude-sonnet-4")).toEqual(
      ["openai/gpt-5-mini:batch"]
    );
    expect(
      toggleComparisonModel(["openai/gpt-5-mini:batch"], "google/gemini-2.5-pro:batch")
    ).toEqual(["openai/gpt-5-mini:batch", "google/gemini-2.5-pro:batch"]);
  });
});

describe("isBatchModel", () => {
  it("detects OpenRouter batch slugs", () => {
    expect(isBatchModel("openai/gpt-5-mini:batch")).toBe(true);
    expect(isBatchModel("openai/gpt-5-mini")).toBe(false);
  });
});

describe("isComparisonOptionDisabled", () => {
  it("disables the other lane and a sixth selection", () => {
    expect(isComparisonOptionDisabled(["openai/gpt-5-mini"], "openai/gpt-5-mini:batch")).toBe(true);
    expect(isComparisonOptionDisabled(["openai/gpt-5-mini:batch"], "openai/gpt-5-mini")).toBe(true);
    expect(isComparisonOptionDisabled(["openai/gpt-5-mini"], "anthropic/claude-sonnet-4")).toBe(
      false
    );
    expect(isComparisonOptionDisabled([], "openai/gpt-5-mini:batch")).toBe(false);
    expect(isComparisonOptionDisabled(["a", "b", "c", "d", "e"], "f")).toBe(true);
  });
});

describe("buildOptimisePrompt", () => {
  it("copies the skill prompt with capability and dataset", () => {
    expect(buildOptimisePrompt("invoice-extract", "ds-1")).toBe(
      `/overmind optimise
capability: invoice-extract
dataset: ds-1`
    );
  });
});

describe("buildBacktestPrompt", () => {
  it("uses the selected model list", () => {
    const prompt = buildBacktestPrompt("invoice-extract", "ds-1", [
      "google/gemini-2.5-pro",
      "xai/grok-4",
    ]);
    expect(prompt).toBe(
      `/overmind backtest
capability: invoice-extract
dataset: ds-1
models: google/gemini-2.5-pro, xai/grok-4`
    );
  });
});

describe("RunLocallyDialog", () => {
  function openBacktesting() {
    fireEvent.mouseDown(screen.getByRole("tab", { name: "Backtesting" }));
  }

  it("defaults to harness and switches to the backtesting prompt", () => {
    mocks.capabilities.mockReturnValue({
      data: {
        results: [{ activeModel: null, id: "cap-1", name: "Invoice", slug: "invoice-extract" }],
      },
    });

    render(
      <RunLocallyDialog
        capabilityId="cap-1"
        datasetId="ds-99"
        onOpenChange={vi.fn()}
        open
        projectId="proj-1"
      />
    );

    expect(screen.getByText("New Optimiser Job")).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Harness" })).toBeTruthy();
    expect(screen.getByRole("tab", { name: "Backtesting" })).toBeTruthy();
    expect(screen.getByRole("button", { name: "Copy command" })).toBeTruthy();
    expect(screen.getByText(/capability: invoice-extract/)).toBeTruthy();
    expect(screen.getByText(/dataset: ds-99/)).toBeTruthy();
    expect(screen.queryByText(/models: openai\/gpt-5-mini, anthropic\/claude-sonnet-4/)).toBeNull();

    openBacktesting();
    expect(screen.getByText("New Backtesting Job")).toBeTruthy();
    expect(screen.getByText(/capability: invoice-extract/)).toBeTruthy();
    expect(screen.getByText(/dataset: ds-99/)).toBeTruthy();
    expect(screen.getByText(/models: openai\/gpt-5-mini, anthropic\/claude-sonnet-4/)).toBeTruthy();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select google/gemini-2.5-pro" }));
    expect(
      screen.getByText(
        /models: openai\/gpt-5-mini, anthropic\/claude-sonnet-4, google\/gemini-2\.5-pro/
      )
    ).toBeTruthy();

    fireEvent.click(screen.getByRole("checkbox", { name: "Select openai/gpt-5-mini" }));
    expect(
      screen.getByText(/models: anthropic\/claude-sonnet-4, google\/gemini-2\.5-pro/)
    ).toBeTruthy();

    expect(screen.getByText(/Batch and standard models can't be mixed/)).toBeTruthy();
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select openai/gpt-5-mini:batch",
        }) as HTMLButtonElement
      ).disabled
    ).toBe(true);
  });

  it("allows unselecting the last remaining model", () => {
    mocks.capabilities.mockReturnValue({
      data: {
        results: [{ activeModel: null, id: "cap-1", name: "Invoice", slug: "invoice-extract" }],
      },
    });

    render(
      <RunLocallyDialog
        capabilityId="cap-1"
        datasetId="ds-99"
        onOpenChange={vi.fn()}
        open
        projectId="proj-1"
      />
    );

    openBacktesting();
    fireEvent.click(screen.getByRole("checkbox", { name: "Select openai/gpt-5-mini" }));
    fireEvent.click(screen.getByRole("checkbox", { name: "Select anthropic/claude-sonnet-4" }));
    expect(screen.getByText(/models: <model>/)).toBeTruthy();
    expect(
      (
        screen.getByRole("checkbox", {
          name: "Select openai/gpt-5-mini:batch",
        }) as HTMLButtonElement
      ).disabled
    ).toBe(false);
  });

  it("falls back to placeholders when capability/dataset are unknown", () => {
    mocks.capabilities.mockReturnValue({ data: { results: [] } });
    render(<RunLocallyDialog onOpenChange={vi.fn()} open projectId="proj-1" />);
    expect(screen.getByText("No capabilities.")).toBeTruthy();
    expect(screen.getByText("No eval datasets.")).toBeTruthy();
    expect(screen.getByText(/capability: <slug>/)).toBeTruthy();
    expect(screen.getByText(/dataset: <dataset-id>/)).toBeTruthy();
    expect(screen.queryByLabelText("Local file")).toBeNull();
  });

  it("updates commands when a capability is picked", () => {
    mocks.capabilities.mockReturnValue({
      data: {
        results: [
          { activeModel: null, id: "cap-1", name: "Invoice", slug: "invoice-extract" },
          { activeModel: null, id: "cap-2", name: "Receipt", slug: "receipt-parse" },
        ],
      },
    });
    render(<RunLocallyDialog onOpenChange={vi.fn()} open projectId="proj-1" />);
    expect(screen.getByRole("button", { name: "Capability" })).toBeTruthy();
    expect(screen.getByText(/capability: <slug>/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Receipt" }));
    expect(screen.getByText(/capability: receipt-parse/)).toBeTruthy();
    openBacktesting();
    expect(screen.getByText(/capability: receipt-parse/)).toBeTruthy();
  });

  it("uses a picked eval dataset in the prompt", () => {
    mocks.capabilities.mockReturnValue({
      data: {
        results: [{ activeModel: null, id: "cap-1", name: "Invoice", slug: "invoice-extract" }],
      },
    });
    mocks.datasets.mockReturnValue({
      data: {
        results: [
          { capability: "cap-1", id: "ds-1", name: "Eval invoices" },
          { capability: "cap-1", id: "ds-2", name: "Eval receipts" },
        ],
      },
      error: null,
      isLoading: false,
      refetch: vi.fn(),
    });
    render(<RunLocallyDialog onOpenChange={vi.fn()} open projectId="proj-1" />);
    expect(screen.getByRole("button", { name: "Dataset" })).toBeTruthy();
    expect(screen.getByText(/dataset: <dataset-id>/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Eval receipts" }));
    expect(screen.getByText(/dataset: ds-2/)).toBeTruthy();
  });
});
