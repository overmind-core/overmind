import { describe, expect, it } from "vitest";

import { finetunedChipLabel, isFinetunedServingId } from "./finetuned-serving-id";

describe("isFinetunedServingId", () => {
  it("matches ft-{8hex}-{slug}", () => {
    expect(isFinetunedServingId("ft-750caa9f-qwen2-5-7b-instruct")).toBe(true);
    expect(isFinetunedServingId("ft-ABCDEF12-llama-3-1-8b")).toBe(true);
  });

  it("rejects base / frontier ids", () => {
    expect(isFinetunedServingId("Qwen/Qwen2.5-7B-Instruct")).toBe(false);
    expect(isFinetunedServingId("qwen2-5-7b-instruct")).toBe(false);
    expect(isFinetunedServingId("ft-short-qwen")).toBe(false);
  });
});

describe("finetunedChipLabel", () => {
  it("strips job prefix and appends · FT", () => {
    expect(finetunedChipLabel("ft-750caa9f-qwen2-5-7b-instruct")).toBe("Qwen2 5 7B Instruct · FT");
  });
});
