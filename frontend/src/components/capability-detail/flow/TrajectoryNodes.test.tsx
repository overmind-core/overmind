// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { type EdgeProps, type NodeProps, Position, ReactFlowProvider } from "@xyflow/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { StepNodeData } from "./buildTrajectoryGraph";
import { TrajectoryConnector } from "./TrajectoryConnector";
import { trajectoryNodeTypes } from "./TrajectoryNodes";

vi.mock("@/components/model-provider-chip", () => ({
  getProviderIcon: () => undefined,
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
    hasActions: false,
    inputs: [],
    kind: "step",
    label: "Request the capability output",
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
  it("labels a source default without presenting it as the observed model", () => {
    renderStep({
      model: "",
      modelResolution: {
        defaultModel: "bu-2-0",
        provenance: [],
        selection: "The caller can supply another model.",
      },
    });
    expect(screen.getByTestId("model-chip").textContent).toBe("bu-2-0");
    expect(screen.getByText("Source default")).toBeTruthy();
  });

  it("explains runtime selection when the repository declares no fixed model", () => {
    renderStep({
      model: "",
      modelResolution: {
        defaultModel: "",
        provenance: [],
        selection: "Selected by the CLI or profile.",
      },
    });
    expect(screen.getByText("Selected at runtime")).toBeTruthy();
    expect(screen.queryByTestId("model-chip")).toBeNull();
  });
  it("opens attached evaluations without selecting the graph node", () => {
    const onClick = vi.fn();
    renderStep(
      {
        evaluations: [
          {
            createdAt: new Date(0),
            definitionSource: "code",
            enabled: true,
            id: "check",
            name: "Cites evidence",
            project: "project",
            source: "Cites evidence",
            spanName: "Agent.decide",
            type: "llm",
            updatedAt: new Date(0),
          },
        ],
      },
      onClick
    );
    expect(screen.queryByRole("button", { name: "Cites evidence" })).toBeNull();
    const disclosure = screen.getByRole("button", { name: "Show 1 evaluation" });
    expect(disclosure.getAttribute("aria-expanded")).toBe("false");
    fireEvent.click(disclosure);
    expect(disclosure.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(screen.getByRole("button", { name: "Cites evidence" }));
    expect(onClick).not.toHaveBeenCalled();
    const detail = screen.getByRole("dialog", { name: "Cites evidence" });
    fireEvent.click(within(detail).getByRole("button", { name: "Close" }));
    fireEvent.click(screen.getByRole("button", { name: "Hide 1 evaluation" }));
    expect(screen.queryByRole("button", { name: "Cites evidence" })).toBeNull();
    expect(onClick).not.toHaveBeenCalled();
  });

  it("expands action outcomes in place without selecting the graph node", () => {
    const onToggle = vi.fn();
    const onClick = vi.fn();
    const ActionNode = trajectoryNodeTypes.action;
    render(
      <ReactFlowProvider>
        <div onClick={onClick} role="presentation">
          <ActionNode
            {...({
              data: {
                evaluations: [],
                expanded: false,
                kind: "action",
                onToggle,
                tool: {
                  branches: [{ condition: "Select input" }],
                  name: "click",
                  purpose: "Activate an element",
                },
              },
              id: "click",
            } as unknown as NodeProps)}
          />
        </div>
      </ReactFlowProvider>
    );
    fireEvent.click(screen.getByText("1 outcome"));
    expect(onToggle).toHaveBeenCalledWith("click");
    expect(onClick).not.toHaveBeenCalled();
  });

  it("shows a branch's local outcome and continuation", () => {
    const OutcomeNode = trajectoryNodeTypes.outcome;
    render(
      <ReactFlowProvider>
        <OutcomeNode
          {...({
            data: {
              branch: {
                action: "Read dropdown options",
                basis: "code",
                condition: "Element is a select input",
                next: "Return choices to the model",
                outcome: "Available choices",
                provenance: [],
              },
              kind: "outcome",
            },
            id: "branch",
          } as unknown as NodeProps)}
        />
      </ReactFlowProvider>
    );
    expect(screen.getAllByText("Available choices").length).toBeGreaterThan(0);
    expect(screen.getAllByText("Return choices to the model").length).toBeGreaterThan(0);
  });

  it("groups provider and model identity while retaining the behaviour and contract", () => {
    renderStep();
    const chip = screen.getByTestId("model-chip");
    expect(chip.textContent).toBe("anthropic/claude-sonnet-4-5");
    expect(screen.getByText("Request the capability output")).toBeTruthy();
    expect(screen.getByText("Anthropic")).toBeTruthy();
    expect(screen.queryByText("Model call")).toBeNull();
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
    expect(screen.queryByRole("dialog", { name: "System prompt" })).toBeNull();
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
    expect(screen.queryByTestId("model-chip")).toBeNull();
    expect(screen.getByText("Model not captured")).toBeTruthy();
    expect(screen.queryByRole("button", { name: /Expand system prompt/ })).toBeNull();
  });

  it("leaves non-model cards unchanged", () => {
    renderStep({ actor: "agent", contract: null, model: "", systemPrompt: "" });
    expect(screen.getByText("Request the capability output")).toBeTruthy();
    expect(screen.queryByTestId("model-chip")).toBeNull();
    expect(screen.queryByText("System prompt")).toBeNull();
  });
});

it("keeps full field text available without opening a sidebar from the card header", () => {
  const input = "Conversation history, policy, tools and provider configuration. ".repeat(30);
  renderStep({
    contract: { action: "Select the next action", input, output: "A tool call or response" },
  });
  const title = screen.getByText("Request the capability output");
  const cardText = title.closest(".group")?.textContent;
  fireEvent.click(title);
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(screen.queryByRole("button", { name: /Inspect/ })).toBeNull();
  expect(screen.getByText(input.trim()).getAttribute("tabindex")).toBe("0");
  expect(screen.getByText(input.trim()).textContent).toBe(input);
  expect(title.closest(".group")?.textContent).toBe(cardText);
});

it("rounds continuation bends without changing their endpoints or arrow", () => {
  const { container } = render(
    <ReactFlowProvider>
      <svg>
        <TrajectoryConnector
          {...({
            data: { summary: true },
            id: "return",
            markerEnd: "url(#arrow)",
            source: "tool",
            sourcePosition: Position.Right,
            sourceX: 320,
            sourceY: 500,
            target: "model",
            targetPosition: Position.Bottom,
            targetX: 100,
            targetY: 100,
            type: "continuation",
          } as EdgeProps)}
        />
      </svg>
    </ReactFlowProvider>
  );
  const edge = container.querySelector(".react-flow__edge-path")!;
  expect(edge.getAttribute("d")).toMatch(/^M\s*320 500/);
  expect(edge.getAttribute("d")).toMatch(/L\s*100 100$/);
  expect(edge.getAttribute("d")).toContain("Q");
  expect(edge.getAttribute("marker-end")).toBe("url(#arrow)");
});
