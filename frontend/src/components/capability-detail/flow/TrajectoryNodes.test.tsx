// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { type NodeProps, ReactFlowProvider } from "@xyflow/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { StepNodeData } from "./buildTrajectoryGraph";
import { trajectoryNodeTypes } from "./TrajectoryNodes";

vi.mock("@/components/model-provider-chip", () => ({
  ModelProviderChip: ({ model }: { model: string }) => (
    <span data-testid="model-chip">{model}</span>
  ),
}));

const StepNode = trajectoryNodeTypes.step;
afterEach(cleanup);
const prompt =
  "You analyse the supplied metrics.\nCompare the time windows.\nIdentify anomalies.\nReturn a JSON insight array.";

function renderStep(overrides: Partial<StepNodeData> = {}, onClick = vi.fn()) {
  const data: StepNodeData = {
    actor: "model",
    condition: "",
    contract: { action: "Find meaningful trends", input: "Metrics", output: "JSON insight array" },
    decisionNote: "",
    inputs: [],
    kind: "step",
    label: "Request the capability output",
    mayUse: [],
    model: "anthropic/claude-sonnet-4-5",
    outputs: [],
    pathNames: ["Generate"],
    promptIsExcerpt: false,
    systemPrompt: prompt,
    ...overrides,
  };
  return render(
    <ReactFlowProvider>
      <div onClick={onClick} role="presentation">
        <StepNode {...({ data, id: "model-call", selected: false } as unknown as NodeProps)} />
      </div>
    </ReactFlowProvider>
  );
}

describe("trajectory model call card", () => {
  it("uses the model chip as the title and keeps the step description and contract", () => {
    renderStep();
    const chip = screen.getByTestId("model-chip");
    expect(chip.textContent).toBe("anthropic/claude-sonnet-4-5");
    expect(chip.parentElement?.nextElementSibling?.textContent).toBe(
      "Request the capability output"
    );
    expect(screen.getByText("Find meaningful trends")).toBeTruthy();
    expect(screen.getByText("Metrics")).toBeTruthy();
  });

  it("opens the complete prompt from its three-line preview without selecting the graph node", () => {
    const onClick = vi.fn();
    renderStep({}, onClick);
    const trigger = screen.getByRole("button", { name: "Expand system prompt" });
    expect(trigger.querySelector(".line-clamp-3")?.textContent).toBe(prompt);
    fireEvent.click(trigger);
    expect(onClick).not.toHaveBeenCalled();
    const dialog = screen.getByRole("dialog", { name: "System prompt" });
    expect(dialog.querySelector("pre")?.textContent).toBe(prompt);
    fireEvent.click(within(dialog).getByRole("button", { name: "Close" }));
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(onClick).not.toHaveBeenCalled();
  });

  it("marks partial captures as excerpts in both the card and expanded view", () => {
    renderStep({ promptIsExcerpt: true });
    fireEvent.click(screen.getByRole("button", { name: "Expand system prompt (excerpt)" }));
    expect(screen.getByRole("dialog", { name: "System prompt (excerpt)" })).toBeTruthy();
    expect(
      screen.getByText("Only an excerpt was captured from the capability source.")
    ).toBeTruthy();
  });

  it("shows missing capture state without an empty expand control", () => {
    renderStep({ model: "", systemPrompt: "" });
    expect(screen.getByText("Model not captured")).toBeTruthy();
    expect(screen.getByText("Not captured")).toBeTruthy();
    expect(screen.queryByTestId("model-chip")).toBeNull();
    expect(screen.queryByRole("button", { name: /Expand system prompt/ })).toBeNull();
  });

  it("leaves non-model cards unchanged", () => {
    renderStep({ actor: "agent", contract: null, model: "", systemPrompt: "" });
    expect(screen.getByText("Request the capability output")).toBeTruthy();
    expect(screen.getByText("Generate")).toBeTruthy();
    expect(screen.queryByTestId("model-chip")).toBeNull();
    expect(screen.queryByText("System prompt")).toBeNull();
  });
});
