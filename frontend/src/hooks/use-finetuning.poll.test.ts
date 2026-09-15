/** The poll must terminate: the key carries page and filters, so a
 * never-stopping interval means one live 5s loop per visited page. */
import { describe, expect, it } from "vitest";

import { finetuningJobsPollInterval } from "./use-finetuning";

describe("finetuningJobsPollInterval", () => {
  it("stops once every job is terminal", () => {
    expect(
      finetuningJobsPollInterval([
        { status: "succeeded" },
        { status: "failed" },
        { status: "cancelled" },
      ])
    ).toBe(false);
    expect(finetuningJobsPollInterval([])).toBe(false);
  });

  it("polls while any job is unfinished, and before the first response", () => {
    expect(finetuningJobsPollInterval([{ status: "succeeded" }, { status: "running" }])).toBe(
      5_000
    );
    // Unknown statuses count as unfinished — a new backend state must not
    // silently freeze the poll.
    expect(finetuningJobsPollInterval([{ status: "uploading" }])).toBe(5_000);
    expect(finetuningJobsPollInterval(undefined)).toBe(5_000);
  });
});
