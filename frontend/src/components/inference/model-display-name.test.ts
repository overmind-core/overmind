import { describe, expect, it } from "vitest";

import { primaryNameWithoutBase } from "./model-display-name";

describe("primaryNameWithoutBase", () => {
  const base = "Qwen/Qwen3-1.7B";
  const label = "Qwen3 1.7B";

  it("strips the leading base from `base · dataset · capability` names", () => {
    expect(
      primaryNameWithoutBase("Qwen3-8B · tickets-v2 · Support Bot", "Qwen/Qwen3-8B", "Qwen3 8B")
    ).toBe("tickets-v2 · Support Bot");
    expect(primaryNameWithoutBase("Qwen3 1.7B · tickets · Support", base, label)).toBe(
      "tickets · Support"
    );
  });

  it("strips a legacy trailing base suffix", () => {
    expect(primaryNameWithoutBase("known parakeet · Qwen3 1.7B", base, label)).toBe(
      "known parakeet"
    );
  });

  it("keeps names that do not restate the base", () => {
    expect(primaryNameWithoutBase("custom sweep", base, label)).toBe("custom sweep");
  });

  it("falls back when empty", () => {
    expect(primaryNameWithoutBase(null, base, label)).toBe("Fine-tuned model");
    expect(primaryNameWithoutBase("", base, label)).toBe("Fine-tuned model");
  });

  it("also strips the capability segment when capabilityName is given", () => {
    expect(
      primaryNameWithoutBase(
        "Qwen3-8B · tickets · Support Bot",
        "Qwen/Qwen3-8B",
        "Qwen3 8B",
        "Support Bot"
      )
    ).toBe("tickets");
    // Nothing distinct left, so the caller substitutes.
    expect(
      primaryNameWithoutBase(
        "Qwen3-1.7B · Invoice Triage Capability",
        base,
        label,
        "Invoice Triage Capability"
      )
    ).toBe("");
  });
});
