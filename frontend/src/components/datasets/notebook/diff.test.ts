import { describe, expect, it } from "vitest";

import { diffText } from "./diff";

describe("diffText", () => {
  it("keeps the shared words and marks only the edit", () => {
    const ops = diffText("When did Virgin Australia start?", "When did Virgin Australia begin?");
    expect(ops).toEqual([
      { kind: "same", text: "When did Virgin Australia " },
      { kind: "del", text: "start" },
      { kind: "add", text: "begin" },
      { kind: "same", text: "?" },
    ]);
  });

  it("marks an edit in the middle of a long text without striking the rest", () => {
    const before = `${"a ".repeat(200)}x ${"b ".repeat(200)}`;
    const after = `${"a ".repeat(200)}y ${"b ".repeat(200)}`;
    const ops = diffText(before, after);
    expect(ops.filter((op) => op.kind !== "same")).toEqual([
      { kind: "del", text: "x" },
      { kind: "add", text: "y" },
    ]);
  });

  it("returns one run for equal texts and none for empty ones", () => {
    expect(diffText("same", "same")).toEqual([{ kind: "same", text: "same" }]);
    expect(diffText("", "")).toEqual([]);
  });
});
