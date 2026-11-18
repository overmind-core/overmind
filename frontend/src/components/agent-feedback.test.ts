// covers: privacy
import { describe, expect, it } from "vitest";

import { choiceToRecord } from "@/components/agent-feedback";

describe("choiceToRecord", () => {
  it("saves an unticked box even before the account's choice has loaded", () => {
    expect(choiceToRecord(false, undefined)).toBe(false);
    expect(choiceToRecord(false, true)).toBe(false);
  });

  it("saves the ticked default only for an account that has not chosen", () => {
    expect(choiceToRecord(null, null)).toBe(true);
    expect(choiceToRecord(null, false)).toBeNull();
    expect(choiceToRecord(null, undefined)).toBeNull();
    expect(choiceToRecord(true, true)).toBeNull();
  });
});
