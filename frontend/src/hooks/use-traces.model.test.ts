import { describe, expect, it } from "vitest";

import { pickModel } from "./use-traces";

describe("pickModel", () => {
  it("returns null when no model attribute is present", () => {
    expect(pickModel({ "genai.total_tokens": 10 })).toBeNull();
  });

  it("extracts the model from each supported attribute key", () => {
    for (const key of [
      "genai.model",
      "gen_ai.request.model",
      "gen_ai.response.model",
      "genai.response.model",
      "llm.model",
      "model",
    ]) {
      expect(pickModel({ [key]: "gpt-4o" })).toBe("gpt-4o");
    }
  });

  it("prefers the canonical genai.model over fallbacks", () => {
    expect(pickModel({ "genai.model": "claude-sonnet-4-5", model: "gpt-4o" })).toBe(
      "claude-sonnet-4-5"
    );
  });

  it("trims whitespace and ignores non-string / empty values", () => {
    expect(pickModel({ "genai.model": "  gpt-4o  " })).toBe("gpt-4o");
    expect(pickModel({ "genai.model": "", "llm.model": "claude" })).toBe("claude");
    expect(pickModel({ "genai.model": 42 })).toBeNull();
  });
});
