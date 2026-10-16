// @vitest-environment jsdom
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
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
  inputs: null,
  model: null,
  name: "Agent",
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
    expect(screen.getByText(value === "" ? '""' : JSON.stringify(value))).toBeTruthy();
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
