import { describe, expect, it } from "vitest";

import { tokenizePrompt } from "./prompt-template-editor";

describe("tokenizePrompt", () => {
  it("splits text and completed {{...}} tokens in order", () => {
    expect(tokenizePrompt("a {{input}} b {{output}}")).toEqual([
      { type: "text", value: "a " },
      { type: "chip", value: "{{input}}" },
      { type: "text", value: " b " },
      { type: "chip", value: "{{output}}" },
    ]);
  });

  it("leaves an unterminated token as plain text (as-you-type)", () => {
    expect(tokenizePrompt("score the {{inp")).toEqual([{ type: "text", value: "score the {{inp" }]);
  });

  it("reassembles to the exact raw string (wire format preserved)", () => {
    const raw = "Grade {{ output }} vs {{reference}}.\nBe strict.";
    const joined = tokenizePrompt(raw)
      .map((s) => s.value)
      .join("");
    expect(joined).toBe(raw);
  });

  it("returns an empty list for an empty string", () => {
    expect(tokenizePrompt("")).toEqual([]);
  });
});
