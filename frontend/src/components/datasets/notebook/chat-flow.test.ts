import { describe, expect, it } from "vitest";

import { chatSections, notebookFlow } from "@/components/datasets/notebook/chat-flow";
import type { Cell } from "@/openapi";

describe("Workshop conversation order", () => {
  it("keeps thinking deltas with their starting section and replaces cell states", () => {
    const sections = chatSections(
      "Plan 🔎. Done.",
      [
        { id: "think", phase: "thinking", status: "running", text_offset: 8, type: "activity" },
        { id: "think", phase: "thinking", text: "Inspecting", type: "activity" },
        { id: "think", phase: "thinking", status: "done", text_offset: 8, type: "activity" },
      ],
      [
        { action: "created", id: "cell", text_offset: 8 },
        { action: "ran", id: "cell", text_offset: 8 },
      ]
    );
    expect(sections.map((section) => section.text)).toEqual(["Plan 🔎.", " Done."]);
    expect(sections[1].steps).toHaveLength(3);
    expect(sections[1].cells).toEqual([{ action: "ran", id: "cell", text_offset: 8 }]);
  });

  it("keeps a live activity position after the latest explanation", () => {
    expect(chatSections("Inspecting.", [], [], true).map((section) => section.offset)).toEqual([
      0, 11,
    ]);
  });
});

describe("Notebook cell and turn flow", () => {
  const cells = ["source", "clean", "split"].map((id) => ({ id, state: "ok" }) as Cell);
  it("preserves the chain once across repeated, out-of-order and missing references", () => {
    const flow = notebookFlow(
      [...cells, { id: "proposal", state: "proposed" } as Cell],
      [
        { cells: [], role: "user" },
        {
          cells: [
            { id: "split" },
            { id: "clean" },
            { id: "split" },
            { id: "removed" },
            { id: "proposal" },
          ],
          role: "agent",
        },
        { cells: [], role: "user" },
        { cells: [{ id: "clean" }], role: "agent" },
      ]
    );
    expect(
      flow.map((entry) => (entry.kind === "cell" ? entry.cell.id : `turn-${entry.index}`))
    ).toEqual(["source", "turn-0", "clean", "split", "turn-1", "turn-2", "turn-3"]);
  });
  it("places cells whose stream references have not arrived before the latest answer", () => {
    expect(
      notebookFlow(cells, [{ cells: [], role: "agent" }]).map((entry) =>
        entry.kind === "cell" ? entry.cell.id : "response"
      )
    ).toEqual(["source", "clean", "split", "response"]);
  });
});
