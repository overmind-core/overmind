// @vitest-environment jsdom
import { cleanup, fireEvent, render } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";

import type { AgentActivityPart } from "./activity-timeline";
import { TurnSteps } from "./turn-steps";

// The timeline measures elbow rail heights with ResizeObserver; jsdom has none.
class ResizeObserverStub {
  observe() {}
  unobserve() {}
  disconnect() {}
}
globalThis.ResizeObserver ??= ResizeObserverStub as unknown as typeof ResizeObserver;

afterEach(cleanup);

const DONE_PARTS: AgentActivityPart[] = [
  { duration_ms: 3200, id: "t0", phase: "thinking", status: "done", type: "activity" },
  {
    id: "tool-1",
    phase: "tool_start",
    status: "running",
    title: "Query the frame",
    tool: "query",
    type: "activity",
  },
  {
    id: "tool-1",
    ok: true,
    phase: "tool_done",
    preview: "3 failing traces",
    status: "done",
    tool: "query",
    type: "activity",
  },
];

describe("TurnSteps", () => {
  it("renders nothing without activity parts", () => {
    const { container } = render(<TurnSteps isStreaming={false} parts={[]} />);
    expect(container.firstChild).toBeNull();
  });

  it("collapses settled turns to a summary line with the turn duration", () => {
    const { getByRole } = render(
      <TurnSteps isStreaming={false} parts={DONE_PARTS} turnMs={4200} />
    );
    const toggle = getByRole("button", { name: /Ran 2 steps · 4\.2s/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("false");
  });

  it("falls back to summed thinking durations when the turn has no stats", () => {
    const { getByRole } = render(<TurnSteps isStreaming={false} parts={DONE_PARTS} />);
    expect(getByRole("button", { name: /Ran 2 steps · 3\.2s/ })).toBeTruthy();
  });

  it("surfaces failed steps in the summary", () => {
    const failed = DONE_PARTS.map((part) =>
      part.phase === "tool_done" ? { ...part, ok: false } : part
    );
    const { getByRole } = render(<TurnSteps isStreaming={false} parts={failed} />);
    expect(getByRole("button", { name: /Ran 2 steps · 1 failed/ })).toBeTruthy();
  });

  it("streams expanded and a manual toggle overrides the default", () => {
    const { getByRole } = render(<TurnSteps isStreaming parts={DONE_PARTS} />);
    const toggle = getByRole("button", { name: /^2 steps/ });
    expect(toggle.getAttribute("aria-expanded")).toBe("true");
    fireEvent.click(toggle);
    expect(getByRole("button", { name: /^2 steps/ }).getAttribute("aria-expanded")).toBe("false");
  });
});
