// covers: console-logic
import { describe, expect, it } from "vitest";

import { runningLabel, stepLabel, turnsOf } from "./assistant-transcript";
import type { AssistantEvent, Presentation } from "./types";

const user = (sequence: number, text: string): AssistantEvent => ({
  kind: "user",
  payload: { cells: [], input: `i${sequence}`, interaction: null, text, user: 1 },
  sequence,
});
const reply = (sequence: number, text: string, ...calls: string[]): AssistantEvent => ({
  kind: "assistant",
  payload: { calls: calls.map((id) => ({ id, name: "run_python", raw: "{}" })), text },
  sequence,
});
const result = (
  sequence: number,
  call: string,
  ok = true,
  presentation: Presentation | null = null
): AssistantEvent => ({
  kind: "tool_result",
  payload: { call, content: {}, name: "run_python", ok },
  presentation,
  sequence,
});
const shown: Presentation = {
  cells: {},
  data: {},
  images: {},
  root: { text: "Done", type: "Text" },
};

function segmentsOf(events: AssistantEvent[], active: boolean) {
  const run = turnsOf(events, active).find((turn) => turn.kind === "run");
  return run?.kind === "run" ? run.segments : [];
}

describe("turnsOf", () => {
  it("groups tool calls of consecutive replies until text or a result separates them", () => {
    const segments = segmentsOf(
      [
        user(1, "Prepare this."),
        reply(2, "", "a"),
        result(3, "a"),
        reply(4, "", "b", "c"),
        result(5, "b"),
        result(6, "c", true, shown),
        reply(7, "Publishing.", "d"),
        result(8, "d", false),
      ],
      false
    );
    expect(segments.map((segment) => segment.kind)).toEqual(["tools", "result", "text", "tools"]);
    const [first, , , last] = segments;
    expect(first.kind === "tools" && first.steps.map((step) => step.call.id)).toEqual([
      "a",
      "b",
      "c",
    ]);
    expect(last.kind === "tools" && last.steps[0].state).toBe("Failed");
  });

  it("shows a call without a result as running only while it is the newest work", () => {
    const open = [user(1, "Go."), reply(2, "", "a")];
    const state = (events: AssistantEvent[], active: boolean) => {
      const [group] = segmentsOf(events, active);
      return group.kind === "tools" && group.steps[0].state;
    };
    expect(state(open, true)).toBe("Running");
    expect(state(open, false)).toBe("Interrupted");
    const stopped: AssistantEvent = {
      kind: "status",
      payload: { detail: "Stopped.", state: "stopped" },
      sequence: 3,
    };
    expect(state([...open, stopped], true)).toBe("Interrupted");
  });
});

describe("presented results", () => {
  it("shows what a call presented without a step for the call", () => {
    const presenting: AssistantEvent = {
      kind: "assistant",
      payload: { calls: [{ id: "p", name: "present", raw: "{}" }], text: "" },
      sequence: 2,
    };
    const done: AssistantEvent = {
      kind: "tool_result",
      payload: { call: "p", content: {}, name: "present", ok: true },
      presentation: shown,
      sequence: 3,
    };
    expect(segmentsOf([user(1, "Go"), presenting], true).map((s) => s.kind)).toEqual(["tools"]);
    expect(segmentsOf([user(1, "Go"), presenting, done], false).map((s) => s.kind)).toEqual([
      "result",
    ]);
  });
});

describe("step labels", () => {
  const call = (name: string, args: object) => ({ id: "a", name, raw: JSON.stringify(args) });

  it("names a step by the summary the model wrote, else by its tool in words", () => {
    expect(stepLabel(call("run_python", { code: "1", summary: "Counting the rows" }))).toBe(
      "Counting the rows"
    );
    expect(stepLabel(call("query_dataset", { sql: "select 1" }))).toBe("Query dataset");
  });

  it("names the step a run works on, and none once every call has its result", () => {
    const working: AssistantEvent = {
      kind: "assistant",
      payload: { calls: [call("run_python", { summary: "Counting the rows" })], text: "" },
      sequence: 2,
    };
    expect(runningLabel([user(1, "Go"), working])).toBe("Counting the rows");
    expect(runningLabel([user(1, "Go"), working, result(3, "a")])).toBeNull();
  });
});
