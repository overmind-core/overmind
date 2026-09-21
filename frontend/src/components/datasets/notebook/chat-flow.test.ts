import { describe, expect, it } from "vitest";

import { chatSections } from "@/components/datasets/notebook/chat-flow";

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
