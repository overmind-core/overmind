import { describe, expect, it } from "vitest";

import { scoreState } from "./score-reasoning";

describe("scoreState", () => {
  it("treats a null value as ungraded rather than a 0% fail", () => {
    expect(scoreState({ passed: false, value: null })).toBe("ungraded");
    expect(scoreState({ passed: false, value: 0 })).toBe("failed");
    expect(scoreState({ passed: true, value: 1 })).toBe("passed");
    expect(scoreState({ passed: null, value: 0.6 })).toBe("scored");
  });
});
