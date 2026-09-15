import { describe, expect, it } from "vitest";

import { type AgentActivityPart, buildTimelineSteps, elbowHeightPx } from "./activity-timeline";

describe("elbowHeightPx", () => {
  it("keeps the shared start pad on the first row", () => {
    expect(elbowHeightPx(100, 100, 12)).toBe(12);
  });

  it("grows by the real distance between row centers", () => {
    // Expanded detail pushed the next header 48px further down.
    expect(elbowHeightPx(100, 100 + 32 + 48, 12)).toBe(92);
  });
});

describe("buildTimelineSteps", () => {
  it("merges thinking and tool activity parts into timeline steps", () => {
    const parts: AgentActivityPart[] = [
      { id: "think-0", phase: "thinking", status: "running", type: "activity" },
      { duration_ms: 6200, id: "think-0", phase: "thinking", status: "done", type: "activity" },
      {
        id: "tool-capability_failures-0-0",
        phase: "tool_start",
        status: "running",
        title: "Query capability failures for demo",
        tool: "capability_failures",
        type: "activity",
      },
      {
        id: "tool-capability_failures-0-0",
        ok: true,
        phase: "tool_done",
        preview: "3 failing traces",
        status: "done",
        tool: "capability_failures",
        type: "activity",
      },
    ];
    const steps = buildTimelineSteps(parts);
    expect(steps).toHaveLength(2);
    expect(steps[0]).toMatchObject({ durationMs: 6200, kind: "thinking", status: "done" });
    expect(steps[1]).toMatchObject({
      kind: "tool",
      preview: "3 failing traces",
      status: "done",
      title: "Query capability failures for demo",
    });
  });
});
