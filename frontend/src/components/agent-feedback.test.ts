// covers: privacy
import { describe, expect, it } from "vitest";

import { choiceToRecord } from "@/components/agent-feedback";

describe("choiceToRecord", () => {
  it("saves the box the person changed, whatever the account held", () => {
    expect(choiceToRecord(false, undefined)).toBe(false);
    expect(choiceToRecord(false, false)).toBe(false);
    expect(choiceToRecord(true, true)).toBe(true);
  });

  it("saves the ticked default only for an account that has not chosen", () => {
    expect(choiceToRecord(null, null)).toBe(true);
    expect(choiceToRecord(null, false)).toBeNull();
    expect(choiceToRecord(null, undefined)).toBeNull();
  });
});
