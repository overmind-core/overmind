import { describe, expect, it } from "vitest";

import { defaultFinetuneName } from "./use-finetuning";

describe("defaultFinetuneName", () => {
  it("joins base model, dataset, and capability with ·", () => {
    expect(
      defaultFinetuneName({
        baseModel: "Qwen3-8B",
        capabilityName: "Support Bot",
        datasetName: "tickets-v2",
      })
    ).toBe("Qwen3-8B · tickets-v2 · Support Bot");
  });

  it("skips blank parts and trims", () => {
    expect(
      defaultFinetuneName({
        baseModel: "",
        capabilityName: "  Capability  ",
        datasetName: " ds ",
      })
    ).toBe("ds · Capability");
    expect(defaultFinetuneName({})).toBe("");
  });
});
