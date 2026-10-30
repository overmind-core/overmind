// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { SpanRow } from "@/hooks/use-traces";
import { TraceDataProvider } from "./contexts/TraceDataContext";
import { TraceSelectionProvider } from "./contexts/TraceSelectionContext";
import { SpanDetailView } from "./SpanDetailView";
import { SpanTree } from "./SpanTree";

const root: SpanRow = {
  capability: null,
  capabilityName: null,
  durationNano: 1000000,
  endTimeUnixNano: 1000000,
  evalMetrics: {},
  evalScore: null,
  events: [],
  inputAttribute: null,
  inputs: null,
  model: null,
  name: "Agent",
  outputAttribute: null,
  outputs: null,
  parentSpanId: null,
  policyOutcome: null,
  scopeName: "agent",
  spanAttributes: {},
  spanId: "root",
  spanType: null,
  startTimeUnixNano: 0,
  statusCode: 1,
  statusMessage: "",
  totalCost: null,
  totalTokens: null,
  traceId: "trace",
  traceScores: {},
  trajectoryScore: null,
};
const child: SpanRow = { ...root, name: "Lookup", parentSpanId: "root", spanId: "child" };
function Fixture({ children }: { children: React.ReactNode }) {
  return (
    <TraceDataProvider
      value={{ isLoading: false, projectId: "project", spans: [root, child], traceId: "trace" }}
    >
      <TraceSelectionProvider>{children}</TraceSelectionProvider>
    </TraceDataProvider>
  );
}
afterEach(cleanup);

describe("Trace inspector", () => {
  it.each([false, 0, [], ""])("preserves captured payload %j", (value) => {
    render(
      <Fixture>
        <SpanDetailView span={{ ...root, inputs: value, outputs: null }} />
      </Fixture>
    );
    expect(screen.getAllByText("Not captured")).toHaveLength(1);
    const input = screen.getByRole("button", { name: "Collapse Input" }).closest("section");
    expect(within(input!).getByText(value === "" ? '""' : JSON.stringify(value))).toBeTruthy();
  });
  it("lists payload attributes that Preview does not render as input or output", () => {
    render(
      <Fixture>
        <SpanDetailView
          span={{
            ...root,
            inputAttribute: "overmind.input",
            inputs: "Rendered input",
            spanAttributes: {
              "gen_ai.input.messages": "Unrendered messages",
              "overmind.input": "Rendered input",
            },
          }}
        />
      </Fixture>
    );
    expect(screen.getByText("gen_ai.input.messages")).toBeTruthy();
    expect(screen.queryByText("overmind.input")).toBeNull();
  });
  it("renders events whose timestamp is not a valid time", () => {
    render(
      <Fixture>
        <SpanDetailView
          span={{ ...root, events: [{ name: "provider.retry", timeUnixNano: Number.MAX_VALUE }] }}
        />
      </Fixture>
    );
    expect(screen.getByText("provider.retry")).toBeTruthy();
    expect(screen.queryByText("Time")).toBeNull();
  });
  it("shows content-block messages as text, with non-text blocks as JSON", () => {
    const request = {
      messages: [
        {
          content: [
            { text: "Who won?", type: "text" },
            { content: "42", tool_use_id: "t1", type: "tool_result" },
          ],
          role: "user",
        },
      ],
    };
    render(
      <Fixture>
        <SpanDetailView span={{ ...root, inputs: JSON.stringify(request), outputs: null }} />
      </Fixture>
    );
    expect(screen.getByText(/Who won\?/)).toBeTruthy();
    expect(screen.getByText(/"tool_result"/)).toBeTruthy();
  });
  it("shows OTel GenAI messages: text, tool calls and results, not server-tool results", () => {
    const answer = [
      {
        parts: [
          { arguments: { query: "who won" }, id: "t1", name: "web_search", type: "tool_call" },
          { id: "t1", response: [{ title: "Search result" }], type: "tool_call_response" },
          { content: "Team A won.", type: "text" },
        ],
        role: "assistant",
      },
    ];
    const toolResult = [
      { parts: [{ id: "t2", response: "16C, cloudy", type: "tool_call_response" }], role: "user" },
    ];
    render(
      <Fixture>
        <SpanDetailView
          span={{ ...root, inputs: JSON.stringify(toolResult), outputs: JSON.stringify(answer) }}
        />
      </Fixture>
    );
    expect(screen.getByText("16C, cloudy")).toBeTruthy();
    expect(screen.getByText("Team A won.")).toBeTruthy();
    expect(screen.getByText("web_search")).toBeTruthy();
    expect(screen.queryByText(/Search result/)).toBeNull();
  });
  it("reveals matching descendants without changing the saved collapsed state", () => {
    const { rerender } = render(
      <Fixture>
        <SpanTree />
      </Fixture>
    );
    fireEvent.click(screen.getByRole("button", { name: "Collapse Agent" }));
    expect(screen.queryByText("Lookup")).toBeNull();
    rerender(
      <Fixture>
        <SpanTree search="Lookup" />
      </Fixture>
    );
    expect(screen.getByText("Lookup")).toBeTruthy();
    expect(screen.getByText("Agent")).toBeTruthy();
    rerender(
      <Fixture>
        <SpanTree />
      </Fixture>
    );
    expect(screen.queryByText("Lookup")).toBeNull();
  });
});
