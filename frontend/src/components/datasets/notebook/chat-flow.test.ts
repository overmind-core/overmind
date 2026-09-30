import { describe, expect, it } from "vitest";

import { chatSections, notebookFlow } from "@/components/datasets/notebook/chat-flow";
import type { Cell } from "@/openapi";

describe("Workshop conversation order", () => {
  it.each([
    'Treating "Extraing training data" as an explicit training request.',
    "Reading the [source evidence](https://example.com/source) before continuing.",
    "```python\nfirst = 1\n\nsecond = 2\n```",
  ])("keeps actions out of a complete Markdown block: %s", (paragraph) => {
    const text = `${paragraph}\n\nInspection complete.`;
    const sections = chatSections(
      text,
      [{ id: "inspect", phase: "tool_start", text_offset: 15, type: "activity" }],
      [{ action: "ran", id: "cell", text_offset: 20 }]
    );
    expect(sections[0].text).toBe(`${paragraph}\n\n`);
    expect(sections[0].steps).toEqual([]);
    expect(sections[1].steps).toHaveLength(1);
    expect(sections[1].cells).toHaveLength(1);
    expect(sections.map((section) => section.text).join("")).toBe(text);
  });

  it("keeps live text together while an action arrives before its remaining tokens", () => {
    const steps = [
      { id: "inspect", phase: "tool_start" as const, text_offset: 12, type: "activity" as const },
    ];
    const partial = "Inspecting 🔎 the attached";
    expect(chatSections(partial, steps, [], true)[0].text).toBe(partial);
    const complete = `${partial} rows.`;
    const sections = chatSections(complete, steps, [], false);
    expect(sections[0].text).toBe(complete);
    expect(sections[1].steps).toHaveLength(1);
  });

  it("keeps thinking deltas with their starting section and replaces cell states", () => {
    const sections = chatSections(
      "Plan 🔎.\n\nDone.",
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
    expect(sections.map((section) => section.text)).toEqual(["Plan 🔎.\n\n", "Done."]);
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
